"""
report_cross_stem_links.py -- three signals that two nodes in different stems
belong together, and how much they agree.

Design brief rev 0 §6, §8 Q2, §9, §11. The sequencer's comparison table needs
pairing evidence; this script measures what the data already gives it:

  (a) STATED   writers' free-text "Associated Concepts from Other Stems"
               (node_links, link_type='stated'; related_node_id is never set).
               Resolved to a STEM, and where the text is specific, to candidate
               concept/skills and nodes. Never written back to the database.
  (b) CCSS     node pairs in different stems sharing a CCSS code
               (node_standards_parsed, state IS NULL).
  (c) LESSON   node pairs in different stems citing the same EM2 lesson
               (product_refs.lesson_id), with Jaccard over lesson sets.

Reads mh2.db read-only. Writes to data/reports/:
    cross_stem_links_stated.csv            every stated mention, classified
    cross_stem_links_stated_unresolved.csv mentions that reached no stem
    cross_stem_links_shared_ccss.csv       node pairs, one row per pair
    cross_stem_links_shared_lesson.csv     node pairs, one row per pair
    cross_stem_pairs_combined.csv          union of the signals, per node pair
    cross_stem_links_summary.md

    python scripts/report_cross_stem_links.py
"""
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

# ----------------------------------------------------------------------------
# Text helpers (pure)
# ----------------------------------------------------------------------------

STOP = {"the", "a", "an", "for", "of", "in", "on", "to", "with", "from", "by",
        "as", "is", "are", "or", "at", "that", "this", "it", "its", "lays",
        "foundation", "related", "associated", "concepts", "concept", "other",
        "stems", "stem", "informal", "formal", "and"}
# One-word aliases that are ordinary English; a hit on these is never better
# than 'low' when it is found inside a longer phrase.
GENERIC = {"time", "data", "general", "area", "volume", "length", "linear",
           "counting", "comparing", "ordering", "estimating", "fractions",
           "addition", "multiplication", "constructions", "percents"}


def norm(text: str) -> str:
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"\([^)]*\)", " ", t)          # drop parentheticals
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_keep_parens(text: str) -> str:
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9()]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> List[str]:
    return norm(text).split()


def stem_key(tok: str) -> str:
    """Crude stemmer: 'classification' and 'classify' share a key."""
    tok = re.sub(r"s$", "", tok)
    if len(tok) > 5:
        tok = re.sub(r"(ing|ed)$", "", tok)
    return tok[:6]


def content_keys(text: str) -> Set[str]:
    return {stem_key(t) for t in tokens(text) if t not in STOP and len(t) > 2}


def split_group(mention: str) -> Tuple[str, List[str]]:
    """'Measurement and Data (Time, Money)' -> ('Measurement and Data', ['Time','Money'])"""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", mention.strip())
    if not m:
        return mention.strip(), []
    subs = [s.strip() for s in re.split(r"[,;/]", m.group(2)) if s.strip()]
    return m.group(1).strip(), subs


# Lines that end a run of "associated concepts": time estimates, grade-prefixed
# rows, lesson references, quotations, and the headings of other sections.
NOISE_PATTERNS = [
    ("time_estimate", re.compile(r"instructional\s+(?:period|day)|\blikely\b|^\d|^(?:PK|GK|G\d)\b|\bperiods?\b", re.I)),
    ("section_heading", re.compile(r"^(?:fluency|lesson|launch|additional notes|other\b|notes?\b|associated concepts)", re.I)),
    ("lesson_ref", re.compile(r"\bM\d+\b|\bL\d+\b|EM2|Bluebonnet|TX BB", re.I)),
    ("quotation", re.compile(r"^[“\"]|\(Source", re.I)),
    ("too_long", re.compile(r"^.{80,}$", re.S)),
]


def noise_reason(line: str) -> Optional[str]:
    for name, rx in NOISE_PATTERNS:
        if rx.search(line or ""):
            return name
    return None


# Headers that introduce a list of related concepts. The ingest only knows the
# first; the others were found by reading additional_notes.
HEADER_RES = [
    ("assoc_header", re.compile(r"^associated\s+concepts?\s+from\s+other\s+stems?\s*:?\s*(?P<rest>.*)$", re.I)),
    ("related_concepts_header", re.compile(r"^related\s+concepts\s*:\s*(?P<rest>.*)$", re.I)),
    ("related_to_header", re.compile(r"^related\s+to\s*:\s*(?P<rest>.*)$", re.I)),
    ("assoc_short_header", re.compile(r"^associated\s+concepts?\s*:\s*(?P<rest>.*)$", re.I)),
]
# Prose that states a cross-stem relationship without a heading.
PROSE_CUE = re.compile(
    r"\b(?:overlap\s+with|connect\s+to|lays\s+the\s+foundation\s+for|related\s+to)\b", re.I)


def extract_mentions(lines: List[str]) -> List[Tuple[str, str]]:
    """(kind, text) for one node's additional_notes paragraphs. A header opens a
    run that ends at the first line noise_reason() rejects. Headerless prose
    with a cross-stem cue is returned as kind 'prose_cue' and is not a run."""
    out: List[Tuple[str, str]] = []
    in_run = False
    used_as_run = set()
    for i, raw in enumerate(lines):
        line = re.sub(r"\s+", " ", raw or "").strip()
        hdr = None
        for kind, rx in HEADER_RES:
            m = rx.match(line)
            if m:
                hdr = (kind, m.group("rest").strip())
                break
        if hdr:
            in_run = True
            used_as_run.add(i)
            if hdr[1] and not noise_reason(hdr[1]):
                out.append((hdr[0], hdr[1]))
            elif hdr[1]:
                in_run = False
            continue
        if in_run:
            if noise_reason(line) or not line:
                in_run = False
            else:
                out.append(("run_item", line))
                used_as_run.add(i)
    for i, raw in enumerate(lines):
        line = re.sub(r"\s+", " ", raw or "").strip()
        if i in used_as_run or len(line) > 160:
            continue
        if PROSE_CUE.search(line) and not any(rx.match(line) for _, rx in HEADER_RES):
            out.append(("prose_cue", line))
    return out


# ----------------------------------------------------------------------------
# Stem alias table and resolution
# ----------------------------------------------------------------------------

# The only hand-made aliases. Writers shorten stem names in prose; each entry
# below was read off an actual sentence in additional_notes, not guessed.
SHORTHAND = {"base ten": "NS-BASE10", "base ten structure": "NS-BASE10",
             "subitizing": "NS-SUBITIZE"}


class Resolver:
    """Maps free text to stem_ids using only names the project already keeps:
    stems.name, stem_map.masterlist_name / workbook_stem / stem_group."""

    def __init__(self, con: sqlite3.Connection):
        cur = con.cursor()
        self.stem_name = {s: " ".join((n or s).split()) for s, n in
                          cur.execute("SELECT stem_id, name FROM stems")}
        self.has_nodes = {s for (s,) in cur.execute("SELECT DISTINCT stem_id FROM nodes")}
        self.alias: Dict[str, Set[str]] = defaultdict(set)
        self.group_members: Dict[str, Set[str]] = defaultdict(set)
        self.ladder_of: Dict[str, str] = {}
        for sid, name in self.stem_name.items():
            self.alias[norm(name)].add(sid)
        for (sg, ml, wb, lf, wid) in cur.execute(
                "SELECT stem_group, masterlist_name, workbook_stem, ladder_file, workbook_stem_id FROM stem_map"):
            if not wid:
                continue
            for name in (ml, wb):
                if name:
                    self.alias[norm(name)].add(wid)
            if sg:
                self.group_members[norm(sg)].add(wid)
            if lf:
                self.ladder_of[wid] = lf
        for phrase, sid in SHORTHAND.items():
            self.alias[phrase].add(sid)
        for a in list(self.alias):
            last = a.split()[-1]
            variant = a[:-1] if last.endswith("s") else a + "s"
            if variant not in self.alias:
                self.alias[variant] = set(self.alias[a])
        for sid in self.stem_name:
            self.ladder_of.setdefault(sid, "")
        self.band_of = {sid: (b or "PK5") for sid, b in
                        cur.execute("SELECT stem_id, band FROM stems")}
        # Concept pool: ladder headings and the workbook's pre-ladder concept rows.
        self.concepts: List[Tuple[str, str, str]] = []   # (stem_id, text, origin)
        for sid, cs in cur.execute(
                "SELECT DISTINCT stem_id, concept_skill FROM nodes WHERE concept_skill IS NOT NULL"):
            self.concepts.append((sid, cs, "ladder_heading"))
        for sid, tx in cur.execute(
                "SELECT DISTINCT stem_id, text FROM concepts WHERE stem_id IS NOT NULL"):
            self.concepts.append((sid, tx, "workbook_concept"))
        self.node_text = {}
        for nid, sid, tx in cur.execute("SELECT node_id, stem_id, node_text FROM nodes"):
            self.node_text[nid] = (sid, tx)
        self.max_alias_len = max((len(a.split()) for a in self.alias), default=1)

    def same_ladder(self, a: str, b: str) -> bool:
        la, lb = self.ladder_of.get(a, ""), self.ladder_of.get(b, "")
        return a == b or (bool(la) and la == lb)

    # -- one mention -> (stems, method, confidence, residual_tokens) ----------
    def resolve(self, mention: str, from_stem: str):
        group, subs = split_group(mention)
        g = norm(group)
        # 1. whole mention equals a stem name
        if g in self.alias and not subs:
            st = self.alias[g]
            return sorted(st), "exact_name", "high" if len(st) == 1 else "medium", []
        # 2. "Group (Member, Member)"
        if subs and g in self.group_members:
            found: Set[str] = set()
            unknown = []
            for s in subs:
                hit = self.alias.get(norm(s), set()) & self.group_members[g]
                if hit:
                    found |= hit
                else:
                    unknown.append(s)
            if found:
                return sorted(found), "group_member", "medium", [t for u in unknown for t in tokens(u)]
            return [], "group_only", "none", [t for s in subs for t in tokens(s)]
        if subs and g in self.alias:
            return sorted(self.alias[g]), "exact_name", "high" if len(self.alias[g]) == 1 else "medium", []
        # 3. longest stem-name n-gram inside the text (self-stem hits are not links)
        toks = tokens(mention)
        best: Optional[Tuple[int, Set[str], int, int]] = None
        hits: List[Tuple[int, int, Set[str]]] = []
        for n in range(min(len(toks), self.max_alias_len), 0, -1):
            for i in range(0, len(toks) - n + 1):
                gram = toks[i:i + n]
                if gram[0] in STOP or gram[-1] in STOP:
                    continue
                key = " ".join(gram)
                if key in self.alias:
                    hits.append((n, i, self.alias[key]))
        taken: List[Tuple[int, int, Set[str]]] = []
        used: Set[int] = set()
        for n, i, st in sorted(hits, key=lambda h: (-h[0], h[1])):
            span = set(range(i, i + n))
            if span & used:
                continue
            if all(s == from_stem for s in st) and taken:
                continue
            used |= span
            taken.append((n, i, st))
        others = [h for h in taken if not all(s == from_stem for s in h[2])]
        if others:
            stems: Set[str] = set()
            for _, _, st in others:
                stems |= {s for s in st if s != from_stem}
            nmax = max(h[0] for h in others)
            gram_words = {toks[j] for n, i, _ in others for j in range(i, i + n)}
            resid = [t for t in toks if t not in gram_words and t not in STOP]
            single_generic = nmax == 1 and any(
                toks[i] in GENERIC for n, i, _ in others if n == 1)
            conf = "low" if (nmax == 1 and len(toks) > 1) or single_generic and len(toks) > 1 else "medium"
            return sorted(stems), "name_inside_text", conf, resid
        # 4. some word run in the text is the start of a stem name
        #    ("Lays the foundation for Multiplication" -> Multiplication and Division)
        pre_best: Tuple[int, Set[str]] = (0, set())
        for n in range(len(toks), 0, -1):
            for i in range(0, len(toks) - n + 1):
                gram = toks[i:i + n]
                if gram[0] in STOP or gram[-1] in STOP or len(" ".join(gram)) < 5:
                    continue
                pre: Set[str] = set()
                for a, st in self.alias.items():
                    at = a.split()
                    if at[:n] == gram and len(at) > n:
                        pre |= {x for x in st if x != from_stem}
                if pre and n > pre_best[0]:
                    pre_best = (n, pre)
        if pre_best[1]:
            return sorted(pre_best[1]), "name_prefix", "low", []
        return [], "none", "none", [t for t in toks if t not in STOP]

    # -- concept/skill candidates for residual text ---------------------------
    def concept_candidates(self, text: str, stems: Optional[List[str]],
                           band_of_stem: Optional[str] = None, k: int = 2):
        """Headings whose content words overlap `text`. `stems` restricts to
        target stems; `band_of_stem` restricts to that stem's band (PK-5 or 6-9),
        so a K node is never matched to a high-school heading by accident."""
        want = content_keys(text)
        if not want:
            return []
        band = self.band_of.get(band_of_stem) if band_of_stem else None
        scored = []
        for sid, ctext, origin in self.concepts:
            if stems is not None and sid not in stems:
                continue
            if band and self.band_of.get(sid, "PK5") != band:
                continue
            have = content_keys(ctext)
            inter = want & have
            if not inter:
                continue
            contain = len(inter) / len(want)
            scored.append((contain, len(inter) / len(want | have), len(inter), sid, ctext, origin))
        scored.sort(key=lambda r: (-r[0], -r[1]))
        out, seen = [], set()
        for contain, j, ni, sid, ctext, origin in scored:
            if contain < 0.5 or (sid, ctext) in seen:
                continue
            seen.add((sid, ctext))
            out.append((sid, ctext[:90], round(contain, 2), origin, ni))
            if len(out) >= k:
                break
        return out

    def node_candidates(self, resid: List[str], stems: List[str], k: int = 3):
        want = {stem_key(t) for t in resid if len(t) > 2}
        if len(want) < 2:
            return []
        scored = []
        for nid, (sid, tx) in self.node_text.items():
            if sid not in stems:
                continue
            have = content_keys(tx)
            inter = want & have
            if len(inter) >= 2:
                scored.append((len(inter) / len(want), nid))
        scored.sort(reverse=True)
        return [nid for s, nid in scored[:k] if s >= 0.5]


# ----------------------------------------------------------------------------
# Pair signals
# ----------------------------------------------------------------------------

def jaccard(a: Set[str], b: Set[str]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


OTHER_PRODUCT = re.compile(r"TX\b|Bluebonnet|Maryland|Catalyst", re.I)


def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.0f}%" if b else "n/a"


def write_csv(name: str, header: List[str], rows: List[list]) -> Path:
    path = config.REPORTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def main() -> None:
    con = sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True)
    cur = con.cursor()
    R = Resolver(con)

    nodes = {r[0]: r[1:] for r in cur.execute(
        "SELECT node_id, source_key, stem_id, concept_skill, node_text FROM nodes")}
    grades: Dict[str, Set[str]] = defaultdict(set)
    for nid, g in cur.execute("SELECT node_id, grade FROM node_grade"):
        grades[nid].add(g)
    ord_of = dict(cur.execute("SELECT grade, ord FROM grade_order"))

    def gstr(nid):
        return ",".join(sorted(grades.get(nid, ()), key=lambda g: ord_of.get(g, 999)))

    # ------------------------------------------------------------- (a) stated
    links = defaultdict(list)   # node_id -> [(rowid, text)] in insertion order
    for rid, nid, txt in cur.execute(
            "SELECT rowid, node_id, related_text FROM node_links "
            "WHERE link_type='stated' ORDER BY rowid"):
        links[nid].append((rid, txt))
    n_links = sum(len(v) for v in links.values())

    stated_rows: List[list] = []
    unresolved: List[list] = []
    stated_targets: Dict[str, Dict[str, str]] = defaultdict(dict)  # node -> {stem: conf}
    status_counts = Counter()
    conf_counts = Counter()
    lines_of = defaultdict(list)
    for nid, v in cur.execute(
            "SELECT node_id, value FROM node_fields WHERE field='additional_notes' "
            "ORDER BY node_id, ordinal"):
        lines_of[nid].append(v)

    def emit(src, nid, text, status, reason, kind):
        sk, from_stem = nodes[nid][0], nodes[nid][1]
        row = [src, nid, sk, from_stem, text, status, reason, kind]
        if status != "kept":
            stated_rows.append(row + [""] * 10)
            return
        group, subs = split_group(text)
        stems, method, conf, resid = R.resolve(text, from_stem)
        cross = [s for s in stems if not R.same_ladder(from_stem, s)]
        same = [s for s in stems if R.same_ladder(from_stem, s) and s != from_stem]
        if stems and not cross:
            conf_eff = "same_ladder"
        else:
            conf_eff = conf
        if resid or not stems:
            cc = R.concept_candidates(" ".join(resid) if resid else text,
                                      (cross or None) if stems else None,
                                      band_of_stem=None if stems else from_stem)
        else:
            cc = []
        cc_s = " | ".join(f"{s}: {t} (containment {j}, {o})" for s, t, j, o, _ in cc)
        nc = R.node_candidates(resid, cross) if cross and resid else []
        if not stems and cc and cc[0][4] >= 2:
            # the text names no stem, but two or more content words meet a heading
            stems = sorted({c[0] for c in cc if c[4] >= 2})
            cross = [s for s in stems if not R.same_ladder(from_stem, s)]
            method, conf_eff = "concept_overlap", "low"
        conf_counts[(conf_eff)] += 1
        row2 = row + [";".join(stems), ";".join(cross), ";".join(same),
                      int(any(s in R.has_nodes for s in cross)), method, conf_eff,
                      cc_s, ";".join(nc), " ".join(resid), ""]
        stated_rows.append(row2)
        if cross and conf_eff in ("high", "medium", "low"):
            for s in cross:
                prev = stated_targets[nid].get(s)
                rank = {"high": 3, "medium": 2, "low": 1}
                if prev is None or rank[conf_eff] > rank[prev]:
                    stated_targets[nid][s] = conf_eff
        if not stems:
            unresolved.append([nid, sk, from_stem, text, method, " ".join(resid), cc_s])

    seen_keys = set()
    for nid in sorted(links):
        run_open = True
        for rid, txt in links[nid]:
            if any(rx.match(txt.strip()) for _, rx in HEADER_RES):
                status_counts["noise"] += 1
                emit("node_links", nid, txt, "noise", "header_echo", "")
                run_open = True
                continue
            reason = noise_reason(txt) if run_open else "after_run_ended"
            if reason:
                run_open = False
                status_counts["noise"] += 1
                emit("node_links", nid, txt, "noise", reason, "")
            else:
                status_counts["kept"] += 1
                seen_keys.add((nid, txt.strip()))
                emit("node_links", nid, txt, "kept", "", "")
    # Mentions the ingest's single regex never captured.
    for nid in sorted(lines_of):
        for kind, txt in extract_mentions(lines_of[nid]):
            if (nid, txt.strip()) in seen_keys:
                continue
            seen_keys.add((nid, txt.strip()))
            status_counts["missed_by_ingest"] += 1
            emit("node_fields_reparse", nid, txt, "kept", "", kind)

    stated_header = ["source", "node_id", "source_key", "from_stem", "related_text",
                     "status", "noise_reason", "reparse_kind", "target_stems",
                     "cross_stem_targets", "same_ladder_targets", "target_has_nodes",
                     "match_method", "match_confidence", "concept_candidates",
                     "node_candidates", "residual_tokens", "notes"]
    write_csv("cross_stem_links_stated.csv", stated_header, stated_rows)
    write_csv("cross_stem_links_stated_unresolved.csv",
              ["node_id", "source_key", "from_stem", "related_text", "match_method",
               "residual_tokens", "concept_hints"], unresolved)

    # ------------------------------------------------------------- (b) CCSS
    code_nodes = defaultdict(set)
    rel = {}
    for nid, code, relation in cur.execute(
            "SELECT node_id, standard_code, relation FROM node_standards_parsed "
            "WHERE state IS NULL"):
        if nid in nodes:
            code_nodes[code].add(nid)
            rel[(nid, code)] = relation
    ccss_pairs: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for code, ns in code_nodes.items():
        for a, b in combinations(sorted(ns), 2):
            if nodes[a][1] != nodes[b][1]:
                ccss_pairs[(a, b)].append(code)
    codes_in_2_stems = sum(
        1 for c, ns in code_nodes.items() if len({nodes[n][1] for n in ns}) > 1)

    def pair_common(a, b):
        ga, gb = grades.get(a, set()), grades.get(b, set())
        return ",".join(sorted(ga & gb, key=lambda g: ord_of.get(g, 999)))

    def stem_pair(a, b):
        return tuple(sorted((nodes[a][1], nodes[b][1])))

    rows_b = []
    for (a, b), codes in sorted(ccss_pairs.items()):
        codes = sorted(codes)
        all_aligned = all(rel.get((a, c)) == "aligned" and rel.get((b, c)) == "aligned"
                          for c in codes)
        rows_b.append([a, b, nodes[a][1], nodes[b][1], nodes[a][2][:60], nodes[b][2][:60],
                       gstr(a), gstr(b), int(bool(pair_common(a, b))), pair_common(a, b),
                       len(codes), ";".join(codes), int(all_aligned),
                       int(R.same_ladder(nodes[a][1], nodes[b][1]))])
    write_csv("cross_stem_links_shared_ccss.csv",
              ["node_a", "node_b", "stem_a", "stem_b", "concept_skill_a", "concept_skill_b",
               "grades_a", "grades_b", "shares_grade", "shared_grades", "n_shared_codes",
               "shared_codes", "all_shared_relations_aligned", "same_ladder"], rows_b)

    # ------------------------------------------------------------- (c) lessons
    lessons: Dict[str, Set[str]] = defaultdict(set)
    basis: Dict[Tuple[str, str], str] = {}
    for nid, prod, raw, lid in cur.execute(
            "SELECT node_id, product, raw_ref, lesson_id FROM product_refs "
            "WHERE lesson_id IS NOT NULL"):
        if nid not in nodes or prod == "TX BB" or OTHER_PRODUCT.search(raw or ""):
            continue
        lessons[nid].add(lid)
        b = "em2_stated" if prod == "EM2" else "em2_presumed"
        if basis.get((nid, lid)) != "em2_stated":
            basis[(nid, lid)] = b
    by_lesson = defaultdict(set)
    for nid, ls in lessons.items():
        for l in ls:
            by_lesson[l].add(nid)
    lesson_pairs: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for l, ns in by_lesson.items():
        for a, b in combinations(sorted(ns), 2):
            if nodes[a][1] != nodes[b][1]:
                lesson_pairs[(a, b)].add(l)
    rows_c = []
    for (a, b), ls in sorted(lesson_pairs.items()):
        ls_s = sorted(ls)
        stated_all = all(basis[(a, l)] == "em2_stated" and basis[(b, l)] == "em2_stated"
                         for l in ls_s)
        rows_c.append([a, b, nodes[a][1], nodes[b][1], nodes[a][2][:60], nodes[b][2][:60],
                       gstr(a), gstr(b), int(bool(pair_common(a, b))), pair_common(a, b),
                       len(ls_s), ";".join(ls_s), len(lessons[a]), len(lessons[b]),
                       round(jaccard(lessons[a], lessons[b]), 3),
                       "em2_stated" if stated_all else "includes_unstated_product",
                       int(R.same_ladder(nodes[a][1], nodes[b][1]))])
    write_csv("cross_stem_links_shared_lesson.csv",
              ["node_a", "node_b", "stem_a", "stem_b", "concept_skill_a", "concept_skill_b",
               "grades_a", "grades_b", "shares_grade", "shared_grades", "n_shared_lessons",
               "shared_lessons", "lessons_a", "lessons_b", "jaccard", "lesson_basis",
               "same_ladder"], rows_c)

    # ------------------------------------------------------------- overlap
    cset = set(ccss_pairs)
    lset = set(lesson_pairs)
    both = cset & lset

    def stated_between(a, b) -> Optional[str]:
        """Best stated-link confidence from either node toward the other's stem."""
        best = None
        rank = {"high": 3, "medium": 2, "low": 1}
        for x, y in ((a, b), (b, a)):
            c = stated_targets.get(x, {}).get(nodes[y][1])
            if c and (best is None or rank[c] > rank[best]):
                best = c
        return best

    union = sorted(cset | lset)
    comb_rows = []
    for a, b in union:
        st = stated_between(a, b)
        sig = [("ccss" if (a, b) in cset else ""), ("lesson" if (a, b) in lset else ""),
               ("stated_stem" if st in ("high", "medium") else "")]
        n_sig = sum(1 for s in sig if s)
        comb_rows.append([a, b, nodes[a][1], nodes[b][1], nodes[a][2][:60], nodes[b][2][:60],
                          gstr(a), gstr(b), pair_common(a, b),
                          len(ccss_pairs.get((a, b), ())), len(lesson_pairs.get((a, b), ())),
                          round(jaccard(lessons[a], lessons[b]), 3) if (a, b) in lset else "",
                          st or "", n_sig, "+".join(s for s in sig if s)])
    comb_rows.sort(key=lambda r: (-r[13], -(r[9] or 0) - (r[10] or 0)))
    write_csv("cross_stem_pairs_combined.csv",
              ["node_a", "node_b", "stem_a", "stem_b", "concept_skill_a", "concept_skill_b",
               "grades_a", "grades_b", "shared_grades", "n_shared_ccss", "n_shared_lessons",
               "lesson_jaccard", "stated_stem_confidence", "n_signals", "signals"], comb_rows)

    # stem-pair level
    sp = defaultdict(lambda: {"ccss": 0, "lesson": 0, "stated": 0})
    placeholder_pairs: Set[Tuple[str, str]] = set()   # stated target has no drafted ladder
    for a, b in cset:
        sp[stem_pair(a, b)]["ccss"] += 1
    for a, b in lset:
        sp[stem_pair(a, b)]["lesson"] += 1
    for nid, tg in stated_targets.items():
        for s, c in tg.items():
            if c in ("high", "medium"):
                if s in R.has_nodes:
                    sp[tuple(sorted((nodes[nid][1], s)))]["stated"] += 1
                else:
                    placeholder_pairs.add(tuple(sorted((nodes[nid][1], s))))

    L = dict(locals())
    write_summary(L)
    print(f"stated rows {n_links}: {dict(status_counts)}; unresolved mentions {len(unresolved)}")
    print(f"CCSS pairs {len(cset)} (codes in 2+ stems {codes_in_2_stems}); lesson pairs {len(lset)}; both {len(both)}")


def write_summary(L: dict) -> None:
    nodes, grades, R = L["nodes"], L["grades"], L["R"]
    cset, lset, both = L["cset"], L["lset"], L["both"]
    comb_rows, sp = L["comb_rows"], L["sp"]
    stated_rows, status_counts = L["stated_rows"], L["status_counts"]
    out: List[str] = []
    a = out.append

    a("# Cross-stem links: three signals and how they overlap\n")
    a(f"Generated by `scripts/report_cross_stem_links.py` against `{config.DB.name}` (read-only). "
      "Every figure names its predicate. Nothing is written back to the database.\n")

    # ---- counts per link type
    kept = [r for r in stated_rows if r[5] == "kept"]
    kept_nl = [r for r in kept if r[0] == "node_links"]
    kept_re = [r for r in kept if r[0] == "node_fields_reparse"]
    resolved = [r for r in kept if r[9]]          # has cross-stem targets
    hm = [r for r in kept if r[13] in ("high", "medium")]
    a("## 1. Counts per link type\n")
    a("| Link type | Count | Predicate |")
    a("|---|---:|---|")
    a(f"| Stated rows in `node_links` | {L['n_links']} | `SELECT COUNT(*) FROM node_links WHERE link_type='stated'` (all have `related_node_id IS NULL`) |")
    a(f"| ... of which a real mention | {len(kept_nl)} | not matched by `noise_reason()` and before the first noise line in that node's rows |")
    a(f"| ... of which noise | {status_counts['noise']} | time estimates, section headings, lesson refs, quotations captured by the ingest's greedy `ASSOC_RE` |")
    a(f"| Mentions the ingest missed | {len(kept_re)} | `extract_mentions(additional_notes)` minus `node_links` (headers `Related concepts:`, `Related to:`, `Associated concepts:`; headerless prose such as \"overlap with\") |")
    a(f"| Mentions reaching a cross-stem target (any confidence) | {len(resolved)} of {len(kept)} | `cross_stem_targets` non-empty |")
    a(f"| ... at high or medium confidence | {len(hm)} | `match_confidence IN ('high','medium')` |")
    a(f"| Nodes with >= 1 real stated mention | {len({r[1] for r in kept})} | distinct `node_id` among kept |")
    a(f"| CCSS-shared node pairs, different stems | {len(cset)} | `node_standards_parsed` with `state IS NULL`; two nodes share `standard_code`; `nodes.stem_id` differs |")
    a(f"| ... distinct CCSS codes in 2+ stems | {L['codes_in_2_stems']} | `GROUP BY standard_code HAVING COUNT(DISTINCT stem_id) > 1` (brief §6 said 33 on 10 ladders) |")
    a(f"| EM2-lesson-shared node pairs, different stems | {len(lset)} | `product_refs.lesson_id IS NOT NULL`, excluding `product='TX BB'` and refs naming TX/Bluebonnet/Maryland/Catalyst; two nodes share a `lesson_id`; stems differ |")
    a("")
    n_stated_heading = len(L["links"])
    a(f"Brief §6 counted 38 prose cross-references. The ingest stores {L['n_links']} rows under "
      f"{n_stated_heading} nodes, but only {len(kept_nl)} of them are mentions: the ingest takes "
      "everything after the heading (up to 8 items), so fluency ideas, lesson lists, "
      "quotations and period estimates land in `related_text`. Treat `node_links` as a "
      "raw capture, not as a table of links.\n")

    # ---- stated resolution detail
    a("## 2. Stated links\n")
    a("Resolution uses only names the project already keeps: `stems.name`, "
      "`stem_map.masterlist_name`, `stem_map.workbook_stem`, `stem_map.stem_group`. "
      "Confidence: **high** = the whole mention equals one stem name; **medium** = equals a name "
      "shared by two stems (\"Comparing and Ordering\"), is \"Group (Member)\", or a multi-word "
      "name sits inside longer text; **low** = one-word name inside a longer phrase, name prefix, "
      "or concept-heading overlap only. Self-references (a node naming its own stem or "
      "ladder) are labelled `same_ladder`, not counted as cross-stem.\n")
    cc = Counter(r[13] for r in kept)
    a("| match_confidence | Mentions |")
    a("|---|---:|")
    for k in ("high", "medium", "low", "same_ladder", "none"):
        a(f"| {k} | {cc.get(k, 0)} |")
    a("")
    tstem = Counter()
    for r in kept:
        if r[13] in ("high", "medium") and r[9]:
            for s in r[9].split(";"):
                tstem[s] += 1
    a("Most-named target stems (high or medium): "
      + ", ".join(f"{R.stem_name.get(s, s).strip()} ({s}) {n}" for s, n in tstem.most_common(6)) + ".\n")
    unres = L["unresolved"]
    a(f"Unresolved mentions ({len(unres)}; full list in `cross_stem_links_stated_unresolved.csv`): "
      + "; ".join(f"`{u[0]}` \"{u[3][:60]}\"" for u in unres[:12]) + ".\n")
    with_cc = [r for r in kept if r[14]]
    a(f"{len(with_cc)} mentions carry concept/skill candidates and "
      f"{sum(1 for r in kept if r[15])} carry node candidates. Both are overlap guesses on content words, "
      "never written as links; treat them as prompts for a human.\n")
    a("Target stems with no drafted ladder (`target_has_nodes = 0`): "
      f"{sum(1 for r in kept if r[9] and not r[11])} of {len(resolved)} resolved mentions. "
      "A stated link to an undrafted stem can only be shown as a placeholder.\n")

    # ---- overlap between signals
    a("## 3. Overlap between the signals\n")
    nb = len(both)
    a("| Set | Node pairs | Predicate |")
    a("|---|---:|---|")
    a(f"| CCSS only | {len(cset - lset)} | in `cset`, not in `lset` |")
    a(f"| Lesson only | {len(lset - cset)} | in `lset`, not in `cset` |")
    a(f"| CCSS and lesson | {nb} | `cset & lset` (node-level, two independent signals) |")
    strong_b = [r for r in comb_rows if r[13] >= 2]
    a(f"| 2+ signals, counting the stated stem link | {len(strong_b)} | `n_signals >= 2` in `cross_stem_pairs_combined.csv`; the stated signal is stem-level (either node's stated target stem equals the other node's stem, confidence high/medium) |")
    a(f"| All three | {sum(1 for r in comb_rows if r[13] == 3)} | `n_signals = 3` |")
    a(f"| Union | {len(comb_rows)} | `cset | lset` |")
    a("")
    sg = lambda r: bool(r[8])
    a(f"Of {len(cset)} CCSS pairs, {sum(1 for r in comb_rows if 'ccss' in r[14] and r[8])} have both nodes in a shared grade; "
      f"of {len(lset)} lesson pairs, {sum(1 for r in comb_rows if 'lesson' in r[14] and r[8])} do "
      "(`node_grade` intersection non-empty). Pairs in different grades are still evidence of relatedness but are "
      "not co-placement candidates.\n")
    a("Stem-pair level (the granularity at which a stated link can be compared; stated links to stems with no nodes excluded): "
      f"{len(sp)} stem pairs have at least one signal. "
      f"CCSS {sum(1 for v in sp.values() if v['ccss'])}, lesson {sum(1 for v in sp.values() if v['lesson'])}, "
      f"stated {sum(1 for v in sp.values() if v['stated'])}.")
    only_stated = [k for k, v in sp.items() if v["stated"] and not v["ccss"] and not v["lesson"]]
    stated_and = [k for k, v in sp.items() if v["stated"] and (v["ccss"] or v["lesson"])]
    a(f"Stem pairs supported by a stated link **and** a computed signal: {len(stated_and)}. "
      f"Stated only (nothing else supports it): {len(only_stated)}"
      + (" (" + ", ".join("/".join(k) for k in sorted(only_stated)[:10]) + ")" if only_stated else "")
      + ".\n")
    a(f"A further {len(L['placeholder_pairs'])} stated stem pairs point at a stem with no drafted ladder "
      f"({', '.join('/'.join(k) for k in sorted(L['placeholder_pairs'])[:8])}); they are left out of the counts above "
      "because there are no nodes to pair.\n")
    a("Stem pairs with the most node pairs: "
      + "; ".join(f"{k[0]}/{k[1]} ccss {v['ccss']}, lesson {v['lesson']}, stated {v['stated']}"
                  for k, v in sorted(sp.items(), key=lambda kv: -(kv[1]['ccss'] + kv[1]['lesson']))[:6]) + ".\n")

    # ---- G2
    a("## 4. Grade 2 examples\n")
    g2 = [r for r in comb_rows if "2" in (r[8] or "").split(",")]
    a(f"Predicate: node pairs whose `node_grade` sets both contain grade 2 (`shared_grades` contains '2'): "
      f"{len(g2)} pairs, {sum(1 for r in g2 if r[13] >= 2)} with 2+ signals. Top by signals, then shared codes + lessons:\n")
    a("| Nodes | Stems | Signals | Shared CCSS | Shared lessons | Jaccard | Text A / Text B |")
    a("|---|---|---|---:|---:|---:|---|")
    for r in g2[:10]:
        ta, tb = nodes[r[0]][3][:55], nodes[r[1]][3][:55]
        a(f"| {r[0]} / {r[1]} | {r[2]} / {r[3]} | {r[14]} | {r[9]} | {r[10]} | {r[11]} | {ta} / {tb} |")
    a("")

    # ---- recommendation
    a("## 5. Is stated-link parsing worth doing in v1? (brief §8 Q2)\n")
    n_nodes_stated = len({r[1] for r in kept if r[9]})
    in_ccss_stems = len(stated_and)
    a(recommendation(L, kept, resolved, hm, n_nodes_stated, only_stated, stated_and))
    a("")
    a("## 6. Choices made\n")
    for t in (
        "`node_links` rows are classified, not trusted: the run of real mentions ends at the first noise row in that node's rows (insertion order).",
        "Stem resolution is by recorded names only; no fuzzy string matching on stem names. Concept/skill and node candidates are content-word overlap (crude stemming) and are labelled as guesses.",
        "A node naming its own stem or a stem on the same ladder file is `same_ladder`, not a cross-stem link.",
        "The stated signal is stem-level, so it is reported beside the node-pair signals, never merged into the pair count as if it were pair-level evidence.",
        "Lesson pairs exclude `product='TX BB'` and refs mentioning TX, Bluebonnet, Maryland or Catalyst. References with no product marker are kept and flagged `includes_unstated_product`; the ladder column is EM2 by convention but `lesson_id` itself does not record the product.",
        "CCSS pairs use every relation value (aligned, partial, exceeds); `all_shared_relations_aligned` marks the strict subset.",
        "Module-only lesson references have `lesson_id IS NULL` and cannot contribute to lesson overlap.",
    ):
        a(f"- {t}")
    a("")
    a("## 7. Files\n")
    for f, d in (("cross_stem_links_stated.csv", "every stated row and reparse mention, with status, targets, match_confidence, candidates"),
                 ("cross_stem_links_stated_unresolved.csv", "mentions that reached no stem"),
                 ("cross_stem_links_shared_ccss.csv", "one row per cross-stem node pair sharing CCSS codes; both nodes' grades; shares_grade"),
                 ("cross_stem_links_shared_lesson.csv", "one row per cross-stem node pair sharing EM2 lessons; Jaccard over lesson sets"),
                 ("cross_stem_pairs_combined.csv", "union of the signals per node pair, sorted by number of signals")):
        a(f"- `{f}`: {d}.")
    (config.REPORTS / "cross_stem_links_summary.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def recommendation(L, kept, resolved, hm, n_nodes_stated, only_stated, stated_and) -> str:
    n_nl = L["n_links"]
    real_nl = sum(1 for r in kept if r[0] == "node_links")
    txt = [
        f"**Recommendation (derived from the figures above, to be overruled by John):** "
        f"do the small, bounded version in v1, not a general parser.\n",
        f"- The signal is thin. Only {n_nodes_stated} nodes carry a stated cross-stem mention that "
        f"reaches a stem, and {len(hm)} mentions resolve at high/medium confidence. The ingest's "
        f"{n_nl} rows hold {real_nl} real mentions; the rest is noise it should not be trusted for.",
        f"- It is almost entirely stem-level (\"Whole Numbers and Base Ten Structure\"), so it can "
        "badge a stem-to-stem relationship but cannot say which nodes pair.",
        f"- It does add information the computed signals lack: {len(only_stated)} stem pairs are "
        f"supported only by a stated link, against {len(stated_and)} that a computed signal also supports.",
        "- Cost is small because resolution needs no model: the alias table above already resolves "
        "the common cases. The real work is fixing the capture (`ASSOC_RE` takes everything after "
        "the heading, and misses `Related concepts:` / `Related to:`), not the matching.",
        "- **Do in v1:** tighten the ingest capture to stop at the first non-concept line, add the "
        "two missing headers, and resolve to stem only. Show the result as a stem-level badge and "
        "as corroboration on pairs the CCSS or lesson signals already found.",
        "- **Defer:** concept/skill and node resolution of stated text. The candidates here are "
        "overlap guesses; a wrong pairing shown as \"stated by the writer\" would misattribute authorship.",
        "- **Do not** treat stated links as the pairing engine. CCSS and lesson overlap produce far more "
        "node-level pairs and are derived from structured tags.",
    ]
    return "\n".join(txt)


if __name__ == "__main__":
    main()
