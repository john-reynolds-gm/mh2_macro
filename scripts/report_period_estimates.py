"""
report_period_estimates.py -- how dense is the instructional-time axis?

Design brief rev 0 §8 Q4: "Do period estimates exist consistently enough across
ladders to drive the guardrail readout, or is the time axis too sparse for v1?"
This script answers it from the ladders as ingested, not from the prose.

Writers record time estimates as free text in node_fields (field =
'additional_notes'), one paragraph per row, in three conventions that all
co-exist:

  1. inline phrase       "Likely 1-2 instructional periods"
                         "G4: Likely part of 1 instructional period"
  2. header + value rows "Instructional Period"  /  "1-2"  (or "PK: 1", "GK: 1")
  3. other time units    "1 day", "2 instructional days"  (6-9 ladders)
                         "1/2 lesson - combine with next node"  (Coordinate System)

The parser below is deliberately small and pure (strings in, tuples out, no
database) so it can move into mh2/ unchanged. The DB-facing part is main().

    python scripts/report_period_estimates.py

Writes to data/reports/:
    period_estimates.csv          one row per node that carries any time cue
    period_estimates_rows.csv     one row per parsed estimate (long form)
    period_estimates_summary.md   coverage + the v1 guardrail answer
"""
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

# ----------------------------------------------------------------------------
# Parser (pure)
# ----------------------------------------------------------------------------

QUALIFIERS = ("exact", "range", "part_of", "multiple", "not_fixed",
              "embedded", "unparsed")


class Estimate(NamedTuple):
    grade: Optional[str]      # grade_order token, or None = not stated / whole node
    low: Optional[float]
    high: Optional[float]     # None with a low = open ended ("1 or more")
    qualifier: str            # one of QUALIFIERS
    unit: Optional[str]       # 'period' | 'day' | 'lesson'
    note: str                 # tags: per_grade, open_ended, group_total, state:...
    snippet: str              # the raw text this came from


def usable(e: Estimate) -> bool:
    """A number a readout could add up: exact, range, or part_of with an upper
    bound. multiple / not_fixed / embedded / unparsed carry no number."""
    if e.qualifier in ("exact", "range"):
        return e.low is not None
    if e.qualifier == "part_of":
        return e.high is not None
    return False


_NUM = r"\d+(?:\.\d+)?(?:/\d+)?"
_UNIT = r"(?:instructional\s+)?(?:periods?|days?|lessons?)"
# "2", "1-2", "2 or 3", "1/2 to 1", "1+", "1 or more" followed by a unit word.
COUNT_UNIT = re.compile(
    r"(?<![\w/.])(?P<lo>" + _NUM + r")\s*(?P<plus>\+)?"
    r"(?:\s*(?:[-–—]|to|or)\s*(?P<hi>" + _NUM + r")\s*(?P<plus2>\+)?)?"
    r"(?P<more>\s+or\s+more)?\s*\??\s*(?:whole\s+|total\s+)?"
    r"(?P<unit>" + _UNIT + r")\b", re.I)
# Header-mode value row that starts with a bare count: "1-2 with ongoing ...".
COUNT_LEAD = re.compile(
    r"^(?P<lo>" + _NUM + r")\s*(?P<plus>\+)?"
    r"(?:\s*(?:[-–—]|to)\s*(?P<hi>" + _NUM + r"))?\s*\??(?!\d)", re.I)
PART_OF = re.compile(
    r"\bparts?\s+of\s+(?:(?P<art>an?|one)|(?P<n>\d+))\s+(?:instructional\s+)?"
    r"(?:period|day|lesson)", re.I)
EMBEDDED = re.compile(
    r"\bembedded\s+in\b|\bparts?\s+of\s+(?:other|multiple|the\s+other)\b", re.I)
MULTIPLE = re.compile(r"\bmultiple\s+(?:instructional\s+)?(?:periods?|days?)\b", re.I)
NOT_FIXED = re.compile(
    r"not\s+taught\s+in\s+a\s+fixed|\bdepends?\s+on\b|\bnot\s+fixed\b", re.I)
TIME_WORD = re.compile(
    r"instructional\s+(?:period|day)|\bperiods?\b|\bdays?\b|\blessons?\b", re.I)
INSTR_WORD = re.compile(r"instructional\s+(?:period|day)s?", re.I)
HEADER = re.compile(
    r"^instructional\s+(?P<u>period|day)s?\s*(?:[:\-–—]\s*)?(?P<rest>.*)$",
    re.I)
GRADE_PREFIX = re.compile(
    r"^(?P<g>(?:PK|GK|K|G\d)(?:\s*/\s*(?:PK|GK|K|G?\d))*)\s*"
    r"(?:\((?P<st>[^)]*)\))?\s*[:\-–—]\s*", re.I)
GRADE_SUFFIX = re.compile(r"\bin\s+(?P<g>G[K\d]|GK|PK)\b", re.I)
PER_GRADE = re.compile(r"\b(?:per|in\s+each)\s+grade\b", re.I)
# The estimate covers a run of nodes, not this one: do not count it per node.
GROUP_TOTAL = re.compile(
    r"\baltogether\b|\b(?:for\s+all|with\s+(?:the\s+)?next|first)\s+\d+\s+nodes\b", re.I)

MAX_HEADER_ROWS = 6


def grade_token(raw: str) -> Optional[str]:
    """'GK'->'K', 'G4'->'4', 'PK'->'PK'. Tokens match grade_order."""
    r = raw.strip().upper()
    if r in ("PK", "GK", "K"):
        return "PK" if r == "PK" else "K"
    m = re.fullmatch(r"G?(\d)", r)
    return m.group(1) if m else None


def _grades(prefix: str) -> List[str]:
    out = []
    for part in re.split(r"\s*/\s*", prefix):
        g = grade_token(part)
        if g and g not in out:
            out.append(g)
    return out


def _num(s: str) -> float:
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def _strip_parens(t: str) -> str:
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\([^()]*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace(" ", " ")).strip()


def _count_estimate(m, body, grade, unit_default, note, snippet) -> Estimate:
    lo = _num(m.group("lo"))
    hi_s = m.groupdict().get("hi")
    hi = _num(hi_s) if hi_s else None
    open_ended = bool(m.groupdict().get("plus") or m.groupdict().get("plus2")
                      or m.groupdict().get("more"))
    if re.search(r"at\s+least\s*$", body[:m.start()], re.I):
        open_ended = True
    unit = m.groupdict().get("unit")
    if unit:
        unit = unit.lower().split()[-1].rstrip("s")
    else:
        unit = unit_default
    notes = [note] if note else []
    if open_ended:
        notes.append("open_ended")
        return Estimate(grade, lo, hi, "range", unit, ";".join(notes), snippet)
    if hi is None or hi == lo:
        return Estimate(grade, lo, lo, "exact", unit, ";".join(notes), snippet)
    return Estimate(grade, min(lo, hi), max(lo, hi), "range", unit,
                    ";".join(notes), snippet)


def _parse_segment(body: str, grade, header_mode, note: str,
                   snippet: str) -> List[Estimate]:
    """One clause, grade already decided. Earliest cue wins, so 'Likely part of
    other instructional periods, could be 1-2' is embedded, not a range."""
    flat = _strip_parens(body)
    if not flat:
        return []
    if not header_mode and not TIME_WORD.search(flat):
        return []
    # header_mode is False, or the unit named by the header ('period'/'day').
    grouped = bool(GROUP_TOTAL.search(body))
    unit_default = (header_mode if isinstance(header_mode, str) else "period") \
        if header_mode else None
    if grouped:
        note = (note + ";" if note else "") + "group_total"
    # Two counts in one comma-separated line ("Identify numerals: 1 period,
    # Writing numerals 1 period") is a sum nobody wrote down. Do not guess.
    pieces = [p for p in re.split(r"[,;]", flat)
              if COUNT_UNIT.search(p) and INSTR_WORD.search(p)]
    if len(pieces) > 1:
        return [Estimate(grade, None, None, "unparsed", None, note, snippet)]

    cues = []  # (position, kind, match)
    m = COUNT_UNIT.search(flat)
    if m is None and header_mode:
        m = COUNT_LEAD.match(flat)
    if m:
        cues.append((m.start(), "count", m))
    for kind, rx in (("part_of", PART_OF), ("embedded", EMBEDDED),
                     ("multiple", MULTIPLE), ("not_fixed", NOT_FIXED)):
        mm = rx.search(flat)
        if mm:
            cues.append((mm.start(), kind, mm))
    if not cues:
        return []
    cues.sort(key=lambda c: (c[0], c[1] != "part_of"))
    _, kind, mm = cues[0]
    if PER_GRADE.search(flat):
        note = (note + ";" if note else "") + "per_grade"
    unit_m = re.search(r"\b(period|day|lesson)s?\b", flat, re.I)
    unit = unit_default or (unit_m.group(1).lower() if unit_m else None)
    if kind == "count":
        if grouped:
            return [Estimate(grade, None, None, "part_of", unit, note, snippet)]
        return [_count_estimate(mm, flat, grade, unit, note, snippet)]
    if kind == "part_of":
        if grouped:
            return [Estimate(grade, None, None, "part_of", unit, note, snippet)]
        n = 1.0 if mm.group("art") else float(mm.group("n"))
        return [Estimate(grade, None, n, "part_of", unit, note, snippet)]
    return [Estimate(grade, None, None, kind, unit, note, snippet)]


def parse_line(text: str, header_mode=False) -> List[Estimate]:
    """Parse one paragraph. header_mode=True means the line is a value row under
    an 'Instructional Period' header, so a bare '1-2' or 'PK: 1' counts and the
    unit is 'period'."""
    t = _clean(text)
    if not t:
        return []
    grades: List[str] = []
    note = ""
    pm = GRADE_PREFIX.match(t)
    if pm:
        grades = _grades(pm.group("g"))
        t = t[pm.end():]
        if pm.group("st"):
            note = "state:" + _clean(pm.group("st"))
    flat = _strip_parens(t)
    # "1 instructional period in G4 and 2 instructional periods in G5"
    if len(GRADE_SUFFIX.findall(flat)) >= 2:
        segs = re.split(r"\s+and\s+(?=\d|part\b)", t, flags=re.I)
        out: List[Estimate] = []
        for seg in segs:
            sm = GRADE_SUFFIX.search(_strip_parens(seg))
            g = grade_token(sm.group("g")) if sm else None
            out += _parse_segment(seg, g, header_mode, note, _clean(text))
        return out
    sm = GRADE_SUFFIX.search(flat)
    if sm and not grades:
        grades = [grade_token(sm.group("g"))]
    if not grades:
        return _parse_segment(t, None, header_mode, note, _clean(text))
    out = []
    for g in grades:
        out += _parse_segment(t, g, header_mode, note, _clean(text))
    return out


def parse_period_notes(lines: List[str]) -> List[Estimate]:
    """Parse one node's additional_notes paragraphs (in ordinal order) into
    Estimate rows. Returns [] when the node says nothing about time."""
    out: List[Estimate] = []
    n = len(lines)
    i = 0
    primary_ungraded = False
    while i < n:
        line = _clean(lines[i])
        hm = HEADER.match(line)
        if hm:
            hunit = hm.group("u").lower()
            rest = _strip_parens(hm.group("rest"))
            inline = (COUNT_UNIT.search(rest) or COUNT_LEAD.match(rest)) if rest else None
            if inline:
                out += parse_line(rest, header_mode=hunit)
                i += 1
                continue
            got: List[Estimate] = []
            used = [line]
            j = i + 1
            while j < n and j < i + 1 + MAX_HEADER_ROWS:
                nxt = _clean(lines[j])
                if HEADER.match(nxt):
                    break
                rows = parse_line(nxt, header_mode=hunit)
                if not rows:
                    break
                got += rows
                used.append(nxt)
                j += 1
            if got:
                snip = " | ".join(used)
                out += [e._replace(snippet=snip,
                                   note=(e.note + ";" if e.note else "") + "header_style")
                        for e in got]
            else:
                out.append(Estimate(None, None, None, "unparsed", hunit,
                                    "header_without_value", line))
            i = j
            continue
        rows = parse_line(line)
        if rows:
            # A later "1 day for ..." under "2 instructional days" is a
            # breakdown of the same total, not a second estimate.
            if (all(r.grade is None for r in rows) and primary_ungraded
                    and not INSTR_WORD.search(line)):
                i += 1
                continue
            if all(r.grade is None for r in rows):
                primary_ungraded = True
            out += rows
        elif INSTR_WORD.search(line):
            out.append(Estimate(None, None, None, "unparsed", None, "", line))
        i += 1
    return out


# ----------------------------------------------------------------------------
# Report (DB-facing)
# ----------------------------------------------------------------------------

def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.0f}%" if b else "n/a"


def fmt_row(e: Estimate) -> str:
    lo = "" if e.low is None else f"{e.low:g}"
    hi = "" if e.high is None else f"{e.high:g}"
    return f"{e.grade or '-'}|{lo}|{hi}|{e.qualifier}|{e.unit or '-'}"


def main() -> None:
    con = sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True)
    cur = con.cursor()

    nodes = {r[0]: r[1:] for r in cur.execute(
        "SELECT node_id, source_key, stem_id, concept_skill FROM nodes")}
    grades = defaultdict(set)
    for nid, g in cur.execute("SELECT node_id, grade FROM node_grade"):
        grades[nid].add(g)
    ord_of = dict(cur.execute("SELECT grade, ord FROM grade_order"))
    is_leaf = dict(cur.execute("SELECT node_id, is_leaf FROM node_grade_ruling"))
    lines = defaultdict(list)
    for nid, v in cur.execute(
            "SELECT node_id, value FROM node_fields WHERE field='additional_notes' "
            "ORDER BY node_id, ordinal"):
        lines[nid].append(v)

    parsed = {nid: parse_period_notes(ls) for nid, ls in lines.items()}
    parsed = {k: v for k, v in parsed.items() if v}

    def sort_grades(gs):
        return sorted(gs, key=lambda g: ord_of.get(g, 999))

    # ---------------------------------------------------------------- CSVs
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    with open(config.REPORTS / "period_estimates.csv", "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["node_id", "source_key", "stem_id", "concept_skill",
                    "node_grades", "parsed_rows (grade|low|high|qualifier|unit)",
                    "has_usable", "raw_snippet"])
        for nid in sorted(parsed):
            sk, stem, cs = nodes[nid]
            es = parsed[nid]
            snips = []
            for e in es:
                if e.snippet not in snips:
                    snips.append(e.snippet)
            w.writerow([nid, sk, stem, cs, ",".join(sort_grades(grades.get(nid, ()))),
                        "; ".join(fmt_row(e) for e in es),
                        int(any(usable(e) for e in es)), " // ".join(snips)[:600]])
    with open(config.REPORTS / "period_estimates_rows.csv", "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["node_id", "stem_id", "grade", "low", "high", "qualifier",
                    "unit", "usable", "note", "snippet"])
        for nid in sorted(parsed):
            for e in parsed[nid]:
                w.writerow([nid, nodes[nid][1], e.grade or "", e.low if e.low is not None else "",
                            e.high if e.high is not None else "", e.qualifier,
                            e.unit or "", int(usable(e)), e.note, e.snippet[:300]])

    # ------------------------------------------------------------- figures
    total_nodes = len(nodes)
    n_ip = cur.execute(
        "SELECT COUNT(DISTINCT node_id) FROM node_fields WHERE field='additional_notes' "
        "AND lower(value) LIKE '%instructional period%'").fetchone()[0]
    n_any = len(parsed)
    all_rows = [e for es in parsed.values() for e in es]
    by_q = Counter(e.qualifier for e in all_rows)
    by_unit = Counter(e.unit for e in all_rows if e.qualifier != "unparsed")
    nodes_only_unparsed = sum(1 for es in parsed.values()
                              if all(e.qualifier == "unparsed" for e in es))
    nodes_usable = {nid for nid, es in parsed.items() if any(usable(e) for e in es)}
    nodes_usable_period = {nid for nid, es in parsed.items()
                           if any(usable(e) and e.unit == "period" for e in es)}
    n_open = sum(1 for e in all_rows if "open_ended" in e.note)
    n_hdr = sum(1 for es in parsed.values() if any("header_style" in e.note for e in es))

    in_grade = [nid for nid in nodes if grades.get(nid) and not is_leaf.get(nid, 0)]
    in_grade_leaf_incl = [nid for nid in nodes if grades.get(nid)]

    def attributed(nid, g, need_unit=None):
        """Strict: a usable row naming grade g, or an ungraded usable row on a
        node that sits in exactly one grade."""
        for e in parsed.get(nid, ()):
            if not usable(e) or (need_unit and e.unit != need_unit):
                continue
            if e.grade == g or (e.grade is None and len(grades[nid]) == 1):
                return True
        return False

    def has(nid, unit=None):
        return any(usable(e) and (unit is None or e.unit == unit)
                   for e in parsed.get(nid, ()))

    # by stem
    stem_name = dict(cur.execute("SELECT stem_id, name FROM stems"))
    stem_band = dict(cur.execute("SELECT stem_id, band FROM stems"))
    by_stem = defaultdict(lambda: [0, 0, 0])
    for nid in in_grade:
        s = nodes[nid][1]
        by_stem[s][0] += 1
        by_stem[s][1] += has(nid)
        by_stem[s][2] += has(nid, "period")
    # by grade (node_grade rows, not nodes: a two-grade node counts in each)
    by_grade = defaultdict(lambda: [0, 0, 0, 0, 0])
    for nid in in_grade:
        for g in grades[nid]:
            r = by_grade[g]
            r[0] += 1
            r[1] += has(nid)
            r[2] += has(nid, "period")
            r[3] += attributed(nid, g)
            r[4] += attributed(nid, g, "period")
    # stem x grade, strict, any unit
    sg = defaultdict(lambda: [0, 0])
    for nid in in_grade:
        for g in grades[nid]:
            k = (nodes[nid][1], g)
            sg[k][0] += 1
            sg[k][1] += attributed(nid, g)

    # unit by band of stem
    unit_by_stem = defaultdict(Counter)
    for nid, es in parsed.items():
        for e in es:
            if usable(e):
                unit_by_stem[nodes[nid][1]][e.unit] += 1

    L = dict(locals())
    L["verdict"] = verdict_text(L)
    write_summary(L)
    print(f"nodes {total_nodes}; mention 'instructional period' {n_ip}; any time cue {n_any}; "
          f"usable {len(nodes_usable)} (period unit {len(nodes_usable_period)})")
    print("qualifiers:", dict(by_q))
    print("wrote", config.REPORTS / "period_estimates.csv")


GO, CAVEAT = 0.80, 0.50   # share of a slice's nodes with a strict estimate


def verdict_text(L: dict) -> str:
    """The v1 answer, written from the numbers so a rerun cannot contradict
    them. Thresholds are this script's choice, not a ruling: >= 80% strict
    coverage in a grade = readout usable; 50-79% = show it only with an
    'N of M placed nodes have an estimate' caveat; < 50% = do not show a total."""
    bg = L["by_grade"]
    band = {g: b for g, b in L["cur"].execute("SELECT grade, band FROM grade_order")}
    tiers = {"go": [], "caveat": [], "no": []}
    for g in sorted(bg, key=lambda g: L["ord_of"].get(g, 999)):
        n, u, p, s, sp = bg[g]
        if not n:
            continue
        share = sp / n
        tiers["go" if share >= GO else "caveat" if share >= CAVEAT else "no"].append(
            f"{g} ({sp}/{n}, {pct(sp, n)})")
    any_unit = ", ".join(
        f"{g} {pct(bg[g][3], bg[g][0])}" for g in sorted(bg, key=lambda g: L["ord_of"].get(g, 999))
        if band.get(g) == "6_9" and bg[g][0])
    head = ("**Short answer: not as a total, and not yet for 6-9. For the grades in the "
            "caveat tier a readout is feasible only if it always shows how many placed "
            "nodes it could not count.**" if not tiers["go"] else
            "**Short answer: usable in the grades listed first; everywhere else only "
            "with a visible coverage caveat.**")
    lines = [
        head + "\n",
        f"Rule used (this script's choice, not a ruling): per grade, strict coverage in "
        f"periods (section 5, last column) >= {GO:.0%} = usable, {CAVEAT:.0%}-{GO:.0%} = usable "
        f"with a visible 'N of M nodes counted' caveat, < {CAVEAT:.0%} = do not show a total.\n",
        "- Usable: " + (", ".join(tiers["go"]) or "none"),
        "- With caveat: " + (", ".join(tiers["caveat"]) or "none"),
        "- Not usable: " + (", ".join(tiers["no"]) or "none") + "\n",
        "Caveats on reading these numbers:\n",
        "- **The estimate is per node, not per placement.** A node that spans "
        "two grades usually carries one un-graded figure; only the strict column "
        "attributes it, and only when the node sits in one grade.",
        "- **Bounds, not points.** " + str(by_range(L)) + " of the usable rows are "
        "ranges, open-ended (\"1 or more\") or part_of (an upper bound only); a "
        "readout must carry low and high, or pick one and say so.",
        "- **`part_of` and `embedded` are not free.** They say the time lives "
        "inside other nodes' periods. A sum over placed nodes must not add "
        "them as if they were whole periods.",
        "- **Group estimates** (\"2+ days for the first 6 nodes altogether\") are "
        "deliberately unusable per node.",
        "- **Units differ by band.** 6-9 ladders use days and lessons, PK-5 use "
        "instructional periods. Until someone rules what a day or lesson is worth "
        "in periods, a single cross-band figure would be invented. Even if days and "
        "lessons were ruled equal to periods, strict any-unit coverage in 6-9 would "
        "be only: " + any_unit + ".\n",
        "Recommendation: build the level (Deep / Functional / Illuminating) "
        "distribution by node count first, which needs no time data. Add the time "
        "distribution as a second panel that reports its own coverage "
        "('time known for N of M placed nodes') and shows low-high bounds. "
        "Do not render a time percentage against the 40/45/15 guardrail for a "
        "grade whose coverage falls in the 'not usable' list.",
    ]
    return "\n".join(lines)


def by_range(L: dict) -> int:
    return sum(1 for es in L["parsed"].values() for e in es
               if usable(e) and e.qualifier in ("range", "part_of"))


def write_summary(L: dict) -> None:
    by_q, by_unit = L["by_q"], L["by_unit"]
    in_grade, in_grade_leaf_incl = L["in_grade"], L["in_grade_leaf_incl"]
    nodes_usable, nodes_usable_period = L["nodes_usable"], L["nodes_usable_period"]
    ig = set(in_grade)
    nu_ig = len(ig & nodes_usable)
    np_ig = len(ig & nodes_usable_period)
    n_rows = sum(by_q.values())
    out: List[str] = []
    a = out.append
    a("# Period estimates: coverage and the v1 guardrail answer\n")
    a("Generated by `scripts/report_period_estimates.py` against "
      f"`{config.DB.name}`. Every figure gives its predicate. The parser is "
      "`parse_period_notes()` in that script (Python, pure); the SQL is what "
      "feeds it or sets a denominator.\n")
    a("## 1. What the raw text looks like\n")
    a("Estimates live only in `node_fields` where `field='additional_notes'` "
      "(`SELECT field, COUNT(*) FROM node_fields WHERE lower(value) LIKE "
      "'%instructional period%' GROUP BY 1` returns that one field). Three "
      "conventions co-exist, and the third was not in the brief:\n")
    a("1. Inline phrase (\"Likely 1-2 instructional periods\", with optional "
      "`G4:` prefix or `in G5` suffix).")
    a(f"2. Header row + value rows (\"Instructional Period\" then \"1-2\" or "
      f"\"PK: 1\"). {L['n_hdr']} nodes.")
    a("3. **A different unit.** 6-9 ladders write \"1 day\", \"2 instructional "
      "days\" (Equations, Integers and Rationals, Inequalities, Probability, "
      "Irrational) and \"1/2 lesson\", \"1-2 lessons\" (Coordinate System). "
      "Three PK-5 nodes also say \"instructional days\" (Estimating, "
      "Fractions). Nothing in the repo says a day or a lesson equals an "
      "instructional period, so **the parser records the unit and never "
      "converts.**\n")
    a("## 2. Headline figures\n")
    a("| Figure | Value | Predicate |")
    a("|---|---|---|")
    a(f"| Nodes | {L['total_nodes']} | `SELECT COUNT(*) FROM nodes` |")
    a(f"| Nodes that say \"instructional period\" | {L['n_ip']} | `SELECT COUNT(DISTINCT node_id) FROM node_fields WHERE field='additional_notes' AND lower(value) LIKE '%instructional period%'` |")
    a(f"| Nodes with any time cue (parsed or not) | {L['n_any']} | `len(parsed)` where `parse_period_notes(additional_notes lines)` is non-empty |")
    a(f"| Parsed rows | {n_rows} | one `Estimate` per parsed phrase; a node can have several (per grade) |")
    a(f"| Nodes with >= 1 usable estimate (any unit) | {len(nodes_usable)} | `usable(e)`: qualifier exact or range with a low, or part_of with an upper bound |")
    a(f"| ... of which unit = period | {len(nodes_usable_period)} | as above and `e.unit == 'period'` |")
    a(f"| Unparsed rows | {by_q.get('unparsed', 0)} ({pct(by_q.get('unparsed', 0), n_rows)} of rows) | `qualifier == 'unparsed'` |")
    a(f"| Nodes whose only rows are unparsed | {L['nodes_only_unparsed']} ({pct(L['nodes_only_unparsed'], L['n_any'])} of nodes with a cue) | all rows of the node unparsed |")
    a(f"| Open-ended rows (\"1 or more\", \"2+\") | {L['n_open']} | `'open_ended' in e.note`; counted usable on their lower bound |")
    a("")
    a("Rows by qualifier (`Counter(e.qualifier)`): "
      + ", ".join(f"{q} {by_q.get(q, 0)}" for q in QUALIFIERS) + ".")
    a("")
    a("Usable-or-not rows by unit (`Counter(e.unit)`, unparsed excluded): "
      + ", ".join(f"{u or 'none'} {c}" for u, c in by_unit.most_common()) + ".\n")
    a("## 3. Denominator\n")
    a(f"In-grade nodes = {len(in_grade)}: "
      "`nodes` with at least one `node_grade` row and `node_grade_ruling.is_leaf = 0` "
      f"(`SELECT COUNT(*) FROM nodes n WHERE EXISTS (SELECT 1 FROM node_grade g WHERE g.node_id=n.node_id) "
      f"AND COALESCE((SELECT is_leaf FROM node_grade_ruling r WHERE r.node_id=n.node_id),0)=0`). "
      f"Leaves are outside the core sequence, so they are excluded; including them the count is {len(in_grade_leaf_incl)}. "
      f"Overall: {nu_ig} of {len(in_grade)} in-grade nodes ({pct(nu_ig, len(in_grade))}) have a usable estimate in any unit; "
      f"{np_ig} ({pct(np_ig, len(in_grade))}) have one in periods.\n")
    a("## 4. Coverage by stem\n")
    a("Predicate per row: in-grade nodes of that `stem_id` (denominator above) "
      "with `has(node)` (any usable row) / `has(node, 'period')`.\n")
    a("| Stem | Band | In-grade nodes | Usable, any unit | Usable, periods |")
    a("|---|---|---:|---:|---:|")
    for s in sorted(L["by_stem"], key=lambda k: (L["stem_band"].get(k) or "", k)):
        n, u, p = L["by_stem"][s]
        nm = " ".join((L["stem_name"].get(s) or s).split())
        a(f"| {nm} (`{s}`) | {L['stem_band'].get(s) or 'PK-5'} | {n} | {u} ({pct(u, n)}) | {p} ({pct(p, n)}) |")
    a("")
    a("(`stems.band` is NULL for PK-5 stems, hence the default label.)\n")
    a("## 5. Coverage by grade\n")
    a("A multi-grade node counts once in each of its grades (`node_grade` rows, "
      "not nodes). **Node-level** = the node has any usable row. **Strict** = "
      "the node has a usable row that names that grade, or an un-graded usable "
      "row and the node sits in exactly one grade. Strict is the number a "
      "per-grade readout can actually use.\n")
    a("| Grade | In-grade nodes | Node-level, any unit | Node-level, periods | Strict, any unit | Strict, periods |")
    a("|---|---:|---:|---:|---:|---:|")
    for g in sorted(L["by_grade"], key=lambda g: L["ord_of"].get(g, 999)):
        n, u, p, s, sp = L["by_grade"][g]
        a(f"| {g} | {n} | {u} ({pct(u, n)}) | {p} ({pct(p, n)}) | {s} ({pct(s, n)}) | {sp} ({pct(sp, n)}) |")
    a("")
    a("## 6. Is the time axis dense enough for a live guardrail readout in v1?\n")
    a(L["verdict"])
    a("")
    a("## 7. Parser rules and conservative choices\n")
    for line in (
        "Earliest cue in a clause wins: \"Likely part of other instructional periods, could be 1-2\" is `embedded`, not a range.",
        "Combination notes (\"combine with next node\", \"potentially combined\") do not change the number. \"Altogether\" / group totals become `part_of` with no bound (unusable) so a group total is not counted once per node.",
        "\"1 or this might also live in Practice/Fluency\" reads as exact 1. \"1 or part of 1\" reads as part_of 1.",
        "Parenthetical detail is stripped before counting: \"2 instructional periods (1 whole numbers, 1 decimals)\" is exact 2.",
        "Two counts in one comma-separated clause with no grade split is `unparsed`, not summed.",
        "A later \"1 day for ...\" under \"2 instructional days\" is a breakdown and is skipped.",
        "`PK/GK: Likely 4-5 instructional periods` (one node) emits one row per grade with the same range; it is not known whether 4-5 is each or together.",
        "\"per grade\" / \"per grade level\" rows keep grade None and carry `note=per_grade`; only one-grade nodes get credit in the strict column.",
        "State-specific rows (\"G1 (for FL and AR): ...\") keep the grade and carry `note=state:...`.",
        "Units are recorded, never converted: day, lesson and period are separate columns.",
    ):
        a(f"- {line}")
    a("")
    a("## 8. Files\n")
    a("- `period_estimates.csv`: one row per node with any time cue (node_id, source_key, stem_id, concept_skill, node_grades, parsed rows as grade|low|high|qualifier|unit, has_usable, raw snippet).")
    a("- `period_estimates_rows.csv`: long form, one row per parsed estimate.")
    (config.REPORTS / "period_estimates_summary.md").write_text(
        "\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
