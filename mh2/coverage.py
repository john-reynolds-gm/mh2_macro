"""
coverage.py — the PK-5 / 6-9 coverage query layer (rev 4 handoff, Session B).

Builds both grains in one pass:

  row grain (StandardRow)  one per standard, carrying the rolled-up color.
  tag grain (Tag)          one per (standard, node) pair, with grade_match
                           and a note -- the evidence a row's color rests on.

Tab membership comes from standard_tag_status.sheet, not standards.source --
that is the table the loader work (mh2.load_layer1.load_tag_status) made
carry all five tabs, including 'California-not CCSS' as distinct from
'California-All'. 'California-All' is simply not one of TABS below and is
never looked up; it drops out by construction, not by an exclusion clause.

grade_match is containment, not equality: is the standard's own grade inside
the node's declared range? Color and flags are independent axes -- an
off-grade tag never downgrades a row that also has an on-grade or leaf tag,
and never suppresses the flag on a row that does. See StandardRow.color and
StandardRow.flagged.

Alias resolution happens exactly once, through the same
mh2.normalize.resolve_standard_alias every loader uses, so there is a single
Red/Yellow/Green number -- not the two the retired eval/coverage_audit.py
used to print (its coverage table was exact-match-only; its content-area
block ran post-resolution).

Reasons are a list, not an enum (rev 4 §2 D4): v1 Yellow means "off-grade
tags only", and v2's scope check will want to add a reason without
redefining the color. StandardRow.reasons is exactly that list.

Session B.2 adds stem attribution -- which stem owns a standard, and whether
that stem has a drafted ladder (StandardRow.stem_id/stem_name/stem_ids/
ladder_status, built by build_stem_attribution()). Ladder status is a filter
and summary dimension only, never a color: undrafted content still rolls up
Red/Yellow/Green exactly as it did before this session. See
build_stem_attribution's docstring for the precedence rules.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.normalize import resolve_standard_alias

_WHITESPACE_RUN = re.compile(r"\s+")


def _collapse_ws(text: str) -> str:
    """Collapse any run of whitespace (including embedded newlines) to a
    single space and strip. Applied uniformly to every text field
    node_detail returns rather than special-cased per column -- see
    module note on the §1 whitespace survey."""
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _casefold_ws(text: str | None) -> str:
    """Collapse whitespace, then casefold. The join key for every stem-name
    and category match in stem attribution (§B.2 3.1/3.2): raw strings
    disagree on case (title case in the CSV vs a partially-lowered tail in
    `leaves.category`) and on embedded-newline whitespace (`stem_map`
    verbatim cells)."""
    return _WHITESPACE_RUN.sub(" ", text or "").strip().casefold()

# The five UI tabs. 'California-All' deliberately absent -- see module
# docstring. Order matches the handoff's §4/§5 tables.
TABS = (
    "CCSS",
    "California-not CCSS",
    "Florida",
    "Texas",
    "Other State Standards Gaps",
)

GREEN_MATCH = {"on-grade", "n/a - leaf"}
FLAGGED_MATCH = {"off-grade", "unresolved"}


def is_ccss_leaf(code: str) -> bool:
    """
    Four or more dot-separated segments with a digit in the fourth.
    'K.CC.A.1' and 'K.CC.B.4.a' are leaves; 'K.CC.A' and 'K.CC' are not.
    Only the CCSS sheet carries headings -- this predicate is never applied
    to the other four tabs.
    """
    segs = code.split(".")
    return len(segs) >= 4 and any(ch.isdigit() for ch in segs[3])


@dataclass(frozen=True)
class Tag:
    """One (standard, node) evidence record -- the tag grain."""
    node_id: str
    raw_code: str          # exactly as node_standards wrote it, never rewritten
    tier: str              # 'exact' | 'punct' | 'nocluster'
    node_grades: tuple[str, ...]
    is_leaf: bool
    grade_match: str       # 'on-grade' | 'off-grade' | 'n/a - leaf' | 'unresolved'
    note: str


@dataclass
class StandardRow:
    """One standard -- the row grain, carrying the rolled-up color."""
    code: str
    sheet: str
    grade: str | None
    band: str | None       # 'PK5' | '6_9' | None -- None is meaningful, not missing
    text: str
    claim: str             # 'Yes' | 'blank'
    tags: list[Tag] = field(default_factory=list)
    # Stem attribution (Session B.2, §5/§7). Plain dataclass fields, not
    # @property -- see the module note by StemAttribution: a property would
    # silently vanish under dataclasses.asdict(), the trap Session C would
    # otherwise inherit.
    stem_id: str | None = None        # single owning stem, or None if 0 or >1
    stem_name: str | None = None      # stems.name for stem_id, whitespace-collapsed
    stem_ids: list[str] = field(default_factory=list)  # every owning stem, always
    ladder_status: str = "unattributed"  # 'drafted' | 'undrafted' | 'unattributed'

    @property
    def color(self) -> str:
        if not self.tags:
            return "Red"
        if any(t.grade_match in GREEN_MATCH for t in self.tags):
            return "Green"
        return "Yellow"

    @property
    def reasons(self) -> list[str]:
        """
        Why this row is what it is, as a list a v2 reason (e.g. a scope
        check) can append to -- never a color to redefine.
        """
        if not self.tags:
            return ["no tags in any ladder"]
        if self.color == "Yellow":
            return [f"{t.node_id}: {t.note}" for t in self.tags]
        return []

    @property
    def flagged(self) -> bool:
        """
        Independent of color: a well-covered Green row can still carry a
        tag on a node whose grade does not support it.
        """
        return any(t.grade_match in FLAGGED_MATCH for t in self.tags)

    @property
    def tagged_in_sheet(self) -> bool:
        """D7: 'Tagged in sheet' -- the workbook's own claim, which can be wrong."""
        return self.claim == "Yes"


@dataclass(frozen=True)
class NodeDetail:
    """Presentation detail for one node -- the row-detail expansion's view
    of a node a Tag points at. Everything already on Tag (grades, is_leaf,
    grade_match) is deliberately not repeated here; callers already have it."""
    node_id: str
    stem_name: str
    domain: str
    concept_skill: str
    node_text: str
    goal: str
    raw_grade_cell: str
    fields: dict[str, list[str]]


def _grade_context(con: sqlite3.Connection):
    node_grades: dict[str, set[str]] = {}
    for node_id, grade in con.execute("SELECT node_id, grade FROM node_grade"):
        node_grades.setdefault(node_id, set()).add(grade)
    is_leaf = dict(con.execute(
        "SELECT node_id, is_leaf FROM node_grade_ruling"))
    ord_ = dict(con.execute("SELECT grade, ord FROM grade_order"))
    return node_grades, is_leaf, ord_


def _grade_match(node_id, standard_grade, node_grades, is_leaf, ord_) -> str:
    """
    is_leaf wins unconditionally (schema.sql / §15): a leaf node's grades, if
    any, are annotation and must never fail containment.

    A node with no grade ruling at all, or a standard grade absent from
    grade_order (e.g. band = NULL standards), reports 'unresolved' rather
    than guessing.
    """
    if node_id not in is_leaf:
        return "unresolved"
    if is_leaf[node_id]:
        return "n/a - leaf"
    grades = node_grades.get(node_id)
    if not grades or standard_grade not in ord_:
        return "unresolved"
    lo, hi = min(ord_[g] for g in grades), max(ord_[g] for g in grades)
    return "on-grade" if lo <= ord_[standard_grade] <= hi else "off-grade"


def _tag_note(tier, raw_code, grade_match, node_grades, standard_grade) -> str:
    grades = ", ".join(node_grades) if node_grades else "(no grade ruling)"
    if grade_match == "n/a - leaf":
        base = "leaf node -- grade-match not applicable"
    elif grade_match == "on-grade":
        base = f"on-grade (node declares {grades})"
    elif grade_match == "off-grade":
        base = f"off-grade (node declares {grades}; standard is grade {standard_grade})"
    else:
        base = "unresolved -- no grade ruling for this node"
    if tier != "exact":
        base = f"resolved via {tier} alias from {raw_code!r}; {base}"
    return base


def build_tags_by_standard(con: sqlite3.Connection) -> dict[str, list[Tag]]:
    """
    Every node_standards row, resolved through the one shared alias path
    (mh2.normalize.resolve_standard_alias). Resolving here, once, is what
    gives the rollup a single Red/Yellow/Green number for a given standard,
    regardless of how many differently-spelled ladder codes point at it.
    """
    cur = con.cursor()
    node_grades, is_leaf, ord_ = _grade_context(con)
    grade_of_standard = dict(con.execute("SELECT standard_id, grade FROM standards"))

    by_standard: dict[str, list[Tag]] = {}
    for node_id, raw_code in con.execute(
            "SELECT node_id, standard_id FROM node_standards"):
        resolved, tier = resolve_standard_alias(cur, raw_code)
        if resolved is None:
            continue
        sgrade = grade_of_standard.get(resolved)
        gm = _grade_match(node_id, sgrade, node_grades, is_leaf, ord_)
        grades = tuple(sorted(node_grades.get(node_id, ()),
                              key=lambda g: ord_.get(g, len(ord_))))
        tag = Tag(
            node_id=node_id, raw_code=raw_code, tier=tier,
            node_grades=grades, is_leaf=bool(is_leaf.get(node_id)),
            grade_match=gm,
            note=_tag_note(tier, raw_code, gm, grades, sgrade),
        )
        by_standard.setdefault(resolved, []).append(tag)
    return by_standard


def _denominator_rows(con: sqlite3.Connection):
    """
    Yield (code, sheet, grade, text, tagged_to_stem, band) for every
    denominator standard -- standard_tag_status.sheet in TABS, the CCSS
    heading predicate applied to the CCSS tab only (module docstring).

    The one walk of the denominator: build_rows() and build_stem_attribution()
    both drive off this generator rather than each re-deriving the five-tab
    set, so there is a single definition, not two that could drift apart.
    """
    grade_and_text = {
        r[0]: (r[1], r[2])
        for r in con.execute("SELECT standard_id, grade, text FROM standards")
    }
    band_of = dict(con.execute("SELECT grade, band FROM grade_order"))
    for code, sheet, tagged_to_stem in con.execute(
            "SELECT standard_code, sheet, tagged_to_stem FROM standard_tag_status"):
        if sheet not in TABS:
            continue
        if sheet == "CCSS" and not is_ccss_leaf(code):
            continue
        grade, text = grade_and_text.get(code, (None, None))
        yield code, sheet, grade, text, tagged_to_stem, band_of.get(grade)


# ------------------------------------------------------- stem attribution

@dataclass(frozen=True)
class StemAttribution:
    """
    One standard's resolved owning stem(s) -- the outcome of
    build_stem_attribution()'s precedence rules (§5).

    stem_ids is the FULL set, always: empty means neither source reached the
    standard, more than one means the row is 'multi' -- more than one stem
    claims it and nobody picked a winner (§5 rules 3-4, and see
    build_stem_attribution's docstring for why `concept_standards` itself is
    sometimes multi-valued too). `source` names which route won when the set
    is non-empty; it is not exposed on StandardRow (not asked for in the
    brief's field list) but every caller building the conflict report needs
    it, so it lives here.

    stem_name is populated only when stem_ids has exactly one member (same
    convention as StandardRow.stem_id) -- resolved from `stems.name`, falling
    back to `stem_map.masterlist_name`/`workbook_stem` when no `stems` row
    exists (Step 1 §2.1: existence in `stems` is irrelevant to attribution,
    and a name is never minted). None whenever stem_id itself would be None.
    """
    stem_ids: tuple[str, ...]
    source: str | None          # 'concept_standards' | 'category' | None
    ladder_status: str          # 'drafted' | 'undrafted' | 'unattributed'
    stem_name: str | None = None


_NO_ATTRIBUTION = StemAttribution((), None, "unattributed")


def _concept_standards_stems(con: sqlite3.Connection) -> dict[str, set[str]]:
    """
    standard_id -> owning stem_id(s) via concept_standards -> concepts.stem_id
    (§4). Already in the short-ID namespace `nodes.stem_id` uses -- no name
    matching, no alias resolution, a clean join.

    Multi-valued for 229 standards project-wide (e.g. 'K.CC.A.1' -> {COM, COU,
    ORD, STR}): one row per (concept, standard) pair, and a standard can sit
    under concepts in more than one stem. Not an error -- see
    build_stem_attribution's docstring -- and not collapsed here.
    """
    stem_of_concept = dict(con.execute("SELECT concept_id, stem_id FROM concepts"))
    by_code: dict[str, set[str]] = {}
    for concept_id, standard_id in con.execute(
            "SELECT concept_id, standard_id FROM concept_standards"):
        stem_id = stem_of_concept.get(concept_id)
        if stem_id:
            by_code.setdefault(standard_id, set()).add(stem_id)
    return by_code


def _load_category_to_stems(csv_path) -> dict[str, list[tuple[str, str]]]:
    """
    Casefolded category -> [(band, stem name), ...] target cells, read live
    from `csv_path` (config.CATEGORY_TO_STEMS_CSV: "John expects to revise
    this; the app reads it live rather than hardcoding the mapping") every
    call -- up to 8 stem columns per category row (§3).
    """
    targets: dict[str, list[tuple[str, str]]] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cat = _casefold_ws(row.get("category", ""))
            cells = []
            for key, val in row.items():
                if key == "category" or not val or not val.strip():
                    continue
                parts = [p.strip() for p in val.split("|")]
                if len(parts) != 3:
                    continue
                band_raw, _domain, name = parts
                band = "PK5" if band_raw.upper().startswith("PK") else "6_9"
                cells.append((band, name))
            targets[cat] = cells
    return targets


def _stem_map_name_lookup(con: sqlite3.Connection) -> dict[tuple[str, str], set[str]]:
    """
    (band, casefolded stem name) -> resolved stem key(s) -- workbook_stem_id
    when the ladder has a short code, else stem_map.stem_id itself (§3.2/3.3;
    §3.3 recipe: nodes.stem_id uses workbook_stem_id for PK-5 and the long
    stem_map.stem_id directly for 6-9, so this key IS the node-count join
    key for both bands).

    Both masterlist_name and workbook_stem are indexed -- the CSV's pipe-path
    stem name matches either one, and stems.name alone misses 71 of 128
    references, including drafted stems (MUL, SUB).

    More than one stem_map row can share a (band, name) key BY DESIGN
    (schema.sql: "masterlist_name repeats by design (NSS_COM and NSS_ORD are
    both 'Comparing and Ordering')") -- PK5 'Comparing and Ordering' names
    both NSS_COM (-> COM, 14 nodes) and NSS_ORD (-> ORD, 0 nodes). Both are
    kept in the set; nobody picks a winner.
    """
    lookup: dict[tuple[str, str], set[str]] = {}
    for stem_id, band, masterlist, workbook_stem, wb_id in con.execute(
            "SELECT stem_id, band, masterlist_name, workbook_stem,"
            " workbook_stem_id FROM stem_map"):
        key = wb_id or stem_id
        for name in (masterlist, workbook_stem):
            if name:
                lookup.setdefault((band, _casefold_ws(name)), set()).add(key)
    return lookup


def _stem_map_key_info(con: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    """
    stem key (workbook_stem_id, falling back to stem_map.stem_id -- the same
    key both attribution routes and node_counts use, §3.3) -> (display name,
    band).

    Backs the Step 1 §2.1 call-2 reversal: a stem_map row counts as a real,
    owning, nameable stem whether or not `stems` has a row for it. Name
    prefers `stems.name` (the ingested ladder title) and falls back to
    `stem_map.masterlist_name`/`workbook_stem` -- never minted -- for stems
    with no `stems` row (MD_LENGTH/AREA/VOLUME/DATA and the 6-9 counterparts).
    Band backs §2.2's band-aware precedence.
    """
    info: dict[str, tuple[str, str | None]] = {}
    stems_name = dict(con.execute("SELECT stem_id, name FROM stems"))
    for stem_id, band, masterlist, workbook_stem, wb_id in con.execute(
            "SELECT stem_id, band, masterlist_name, workbook_stem,"
            " workbook_stem_id FROM stem_map"):
        key = wb_id or stem_id
        name = stems_name.get(key) or masterlist or workbook_stem
        info[key] = (_collapse_ws(name) if name else None, band)
    return info


def _category_route_by_code(con: sqlite3.Connection, code_band: dict[str, str | None],
                             csv_path=None):
    """
    standard_id -> owning stem_id(s) via the category route (§3, §5), plus a
    diagnostics dict for whatever a category cell could not resolve.

    `leaves.category` exists only for gaps_sheet-origin rows, so this route
    only ever attributes the 'Other State Standards Gaps' tab -- no other tab
    populates `leaves`. A code can carry more than one category: the two
    welded-code rows (handoff §8/§12.5 -- 'WI.PK.B.EL.5', 'PA.PK.2.4.PK.A.1',
    each two source cells collapsed into one code by strip_trailing_prose)
    each carry two DIFFERENT categories, both unioned in here rather than one
    discarded.

    Resolution is band-scoped against `code_band` (from _denominator_rows): a
    category cell naming a 6-9 stem never attributes a PK5-graded standard,
    even when the same category ALSO lists a PK5 target -- a category's
    targets straddle both runs (§3, "this work carries into the 6-9 run").

    A resolved stem_map row counts as an owning stem whether or not a `stems`
    row exists for it (Step 1 §2.1, reversing the prior session's call): some
    stem_map rows (PK5 MD_LENGTH/MD_AREA/MD_VOLUME/MD_DATA, several 6-9 rows)
    name a workbook stem with NO ladder file on disk at all, but a resolved
    stem name is still an ownership claim -- `ladder_status` is what tells
    'undrafted' (routed, nobody has drafted it) apart from 'unattributed'
    (nothing to route this to), not whether `stems` happens to carry a row.
    Existence in `stems` is a byproduct of ingestion, never consulted here.

    Returns (by_code, diagnostics) where diagnostics is
    {'unresolved_targets': [(category, band, name), ...]   -- named no
         stem_map row at all,
     'no_ladder_yet': [(category, band, name, [stem_ids]), ...]  -- resolved
         to a stem_map row with no `stems` entry -- informational only,
         no longer excluded from `by_code`}.
    """
    if csv_path is None:
        csv_path = config.CATEGORY_TO_STEMS_CSV
    targets = _load_category_to_stems(csv_path)
    name_lookup = _stem_map_name_lookup(con)
    stems_exist = {r[0] for r in con.execute("SELECT stem_id FROM stems")}

    route: dict[tuple[str, str], set[str]] = {}
    unresolved: list[tuple[str, str, str]] = []
    no_ladder_yet: list[tuple[str, str, str, list[str]]] = []
    for cat, cells in targets.items():
        for band, name in cells:
            hit = name_lookup.get((band, _casefold_ws(name)), set())
            if not hit:
                unresolved.append((cat, band, name))
                continue
            if not (hit & stems_exist):
                no_ladder_yet.append((cat, band, name, sorted(hit)))
            route.setdefault((cat, band), set()).update(hit)

    leaves_cats: dict[str, set[str]] = {}
    for standard_id, category in con.execute(
            "SELECT standard_id, category FROM leaves"):
        if category:
            leaves_cats.setdefault(standard_id, set()).add(_casefold_ws(category))

    by_code: dict[str, set[str]] = {}
    for code, cats in leaves_cats.items():
        band = code_band.get(code)
        if band not in ("PK5", "6_9"):
            continue
        stems = set()
        for cat in cats:
            stems |= route.get((cat, band), set())
        if stems:
            by_code[code] = stems

    return by_code, {"unresolved_targets": unresolved, "no_ladder_yet": no_ladder_yet}


def build_stem_attribution(con: sqlite3.Connection, csv_path=None):
    """
    §3-§6: resolve every denominator standard's owning stem(s) and whether
    that stem has a drafted ladder.

    Two independent sources:
      - concept_standards -> concepts.stem_id (§4): workbook attribution,
        already in the short-ID namespace. EPISTEMIC WRINKLE (§6): the
        workbook is the very artifact this whole tool audits -- a prior
        session measured 410 standards tagged in a ladder but attached to a
        DIFFERENT stem in the workbook. For bucketing a standard into
        "content area with a ladder" vs. "without" that imprecision barely
        matters; it is not corrected here.
      - the category route (_category_route_by_code): leaves.category
        (gaps-sheet standards only) -> mh2_category_to_stems.csv -> stem_map,
        per §3.1-§3.3.

    Precedence (Step 1 §2.2, band-aware; supersedes the old band-blind "cs
    always wins"): a same-band candidate beats a cross-band candidate
    regardless of route; within the same band, `concept_standards` beats the
    category route. A route's band-blind candidate set is compared against
    the standard's own band (`code_band`, from `grade_order`) using each
    candidate stem's own band (`stem_map.band`, via `_stem_map_key_info`).
    Precedence only demotes when there is a same-band alternative to demote
    to -- a cross-band-only hit still wins outright, since band-scoping it
    away would turn an attributed row unattributed for no gain (see
    deferred_s5_band_aware_precedence.md). Every standard whose `stem_ids` or
    `ladder_status` changes under this rule vs. the old band-blind one is
    recorded in `category_diagnostics['band_flips']`, listing both
    candidates with their bands.

    Neither source is forced to a single winner when it names more than one
    same-band stem (§5 rule 3, and symmetrically for concept_standards -- see
    _concept_standards_stems). `ladder_status` is 'drafted' if ANY owning
    stem has >=1 row in `nodes` (§5 rule 4: "a writer on any drafted owning
    stem can act on the row"), 'undrafted' if the set is non-empty but none
    do, 'unattributed' if the set is empty (§5 rule 6). Existence of a row in
    `stems` is irrelevant to either the owning-stem set or `ladder_status`
    (Step 1 §2.1: reverses the prior session's stems-exist gate in
    _category_route_by_code) -- a resolved stem name is an ownership claim
    whether or not a ladder file exists for it yet.

    Returns (attribution, conflicts, category_diagnostics):
      attribution: dict[code, StemAttribution], covering every denominator code.
      conflicts:   [(code, concept_standards stem_ids, category stem_ids), ...]
                   for every standard where the two sources disagree (§5 rule 2,
                   band-blind -- unrelated to the band-aware precedence above).
      category_diagnostics: see _category_route_by_code, plus 'band_flips'.
    """
    code_band = {
        code: row_band
        for code, _sheet, _grade, _text, _tagged, row_band in _denominator_rows(con)
    }
    node_counts = dict(con.execute("SELECT stem_id, COUNT(*) FROM nodes GROUP BY stem_id"))
    stem_info = _stem_map_key_info(con)
    band_of = {key: band for key, (_name, band) in stem_info.items()}
    cs_by_code = _concept_standards_stems(con)
    cat_by_code, category_diagnostics = _category_route_by_code(con, code_band, csv_path)

    def status_of(stem_ids: tuple[str, ...]) -> str:
        if not stem_ids:
            return "unattributed"
        if any(node_counts.get(s, 0) > 0 for s in stem_ids):
            return "drafted"
        return "undrafted"

    attribution: dict[str, StemAttribution] = {}
    conflicts: list[tuple[str, list[str], list[str]]] = []
    band_flips: list[dict] = []
    for code, sband in code_band.items():
        cs = cs_by_code.get(code, set())
        cat = cat_by_code.get(code, set())

        if cs and cat and cs != cat:
            conflicts.append((code, sorted(cs), sorted(cat)))

        old_final, old_source = (
            (cs, "concept_standards") if cs else
            (cat, "category") if cat else (set(), None))

        cs_same = {s for s in cs if band_of.get(s) == sband}
        cat_same = {s for s in cat if band_of.get(s) == sband}
        if cs_same:
            final, source = cs_same, "concept_standards"
        elif cat_same:
            final, source = cat_same, "category"
        elif cs:
            final, source = cs, "concept_standards"
        elif cat:
            final, source = cat, "category"
        else:
            final, source = set(), None

        stem_ids = tuple(sorted(final))
        status = status_of(stem_ids)
        old_stem_ids = tuple(sorted(old_final))
        if stem_ids != old_stem_ids or status != status_of(old_stem_ids):
            band_flips.append({
                "code": code, "band": sband,
                "concept_standards": sorted((s, band_of.get(s)) for s in cs),
                "category": sorted((s, band_of.get(s)) for s in cat),
                "old": {"source": old_source, "stem_ids": list(old_stem_ids),
                        "status": status_of(old_stem_ids)},
                "new": {"source": source, "stem_ids": list(stem_ids),
                        "status": status},
            })

        single = stem_ids[0] if len(stem_ids) == 1 else None
        stem_name = stem_info[single][0] if single else None
        attribution[code] = StemAttribution(stem_ids, source, status, stem_name)

    category_diagnostics["band_flips"] = band_flips
    return attribution, conflicts, category_diagnostics


def build_rows(con: sqlite3.Connection, band: str | None = None,
               csv_path=None) -> list[StandardRow]:
    """
    The query layer's single entry point: one StandardRow per denominator
    standard (standard_tag_status.sheet in TABS, CCSS heading predicate
    applied to the CCSS tab only), tags attached from build_tags_by_standard,
    stem attribution attached from build_stem_attribution.

    band: None keeps every band (PK5 / 6_9 / NULL, NULL never coalesced);
    'PK5' or '6_9' filters to that band only.

    csv_path: overrides config.CATEGORY_TO_STEMS_CSV -- for tests only, so a
    fresh in-memory DB isn't joined against the real, unrelated production
    CSV.
    """
    tags_by_standard = build_tags_by_standard(con)
    stem_attrib, _conflicts, _category_diagnostics = build_stem_attribution(con, csv_path)

    rows = []
    for code, sheet, grade, text, tagged_to_stem, row_band in _denominator_rows(con):
        if band is not None and row_band != band:
            continue
        attrib = stem_attrib.get(code, _NO_ATTRIBUTION)
        stem_ids = list(attrib.stem_ids)
        single = stem_ids[0] if len(stem_ids) == 1 else None
        rows.append(StandardRow(
            code=code, sheet=sheet, grade=grade, band=row_band,
            text=text or "", claim="Yes" if tagged_to_stem else "blank",
            tags=tags_by_standard.get(code, []),
            stem_id=single,
            stem_name=attrib.stem_name,
            stem_ids=stem_ids,
            ladder_status=attrib.ladder_status,
        ))
    return rows


def node_detail(con: sqlite3.Connection, node_ids: Iterable[str]) -> dict[str, NodeDetail]:
    """
    Presentation detail for the nodes a row's tags point at: stem name and
    domain, concept/skill, node text, goal, the raw grade cell, and every
    node_fields block -- everything the row-detail expansion needs beyond
    what Tag already carries (node_grades, is_leaf, grade_match).

    Batched: one query per table for the whole `node_ids` set, not one round
    trip per node -- a row can carry six tags and the caller should make one
    call here for all of them.

    node_fields.field is the label; node_fields.value is the content (the
    handoff's §3 table names `field` for this, which is wrong). All field
    names present are returned, grouped by `field` and ordered by `ordinal`
    within each group -- no hardcoded subset, since the ladder template's set
    of field names is expected to grow.

    Every text value returned is whitespace-collapsed (see _collapse_ws): a
    NULL goal comes back as '' rather than being omitted, and a node with no
    node_fields rows comes back with an empty `fields` dict rather than being
    dropped from the result.
    """
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))

    rows = con.execute(
        f"""
        SELECT n.node_id, s.name, s.domain, n.concept_skill, n.node_text,
               n.goal, n.grade_or_leaf
        FROM nodes n
        JOIN stems s ON s.stem_id = n.stem_id
        WHERE n.node_id IN ({placeholders})
        """,
        ids,
    ).fetchall()

    fields_by_node: dict[str, dict[str, list[str]]] = {node_id: {} for node_id in ids}
    for node_id, field_name, value in con.execute(
            f"""
            SELECT node_id, field, value FROM node_fields
            WHERE node_id IN ({placeholders})
            ORDER BY node_id, field, ordinal
            """,
            ids,
    ):
        fields_by_node.setdefault(node_id, {}).setdefault(field_name, []).append(
            _collapse_ws(value))

    out: dict[str, NodeDetail] = {}
    for node_id, stem_name, domain, concept_skill, node_text, goal, raw_grade in rows:
        out[node_id] = NodeDetail(
            node_id=node_id,
            stem_name=_collapse_ws(stem_name),
            domain=_collapse_ws(domain or ""),
            concept_skill=_collapse_ws(concept_skill or ""),
            node_text=_collapse_ws(node_text),
            goal=_collapse_ws(goal or ""),
            raw_grade_cell=_collapse_ws(raw_grade or ""),
            fields=fields_by_node.get(node_id, {}),
        )
    return out


# --------------------------------------------------------------- §4 / §5 tables

def denominator_table(rows: list[StandardRow]) -> dict[str, dict[str, int]]:
    """§4: n per tab x band. Build `rows` with band=None (every band) first."""
    counts = {sheet: {"PK5": 0, "6_9": 0, "NULL": 0} for sheet in TABS}
    for r in rows:
        key = r.band if r.band in ("PK5", "6_9") else "NULL"
        counts[r.sheet][key] += 1
    return counts


def coverage_table(rows: list[StandardRow]) -> dict[str, dict]:
    """§5 main table: n / Green / Yellow / Red / has>=1-tag %, per tab."""
    out = {}
    for sheet in TABS:
        g = [r for r in rows if r.sheet == sheet]
        if not g:
            continue
        green = sum(1 for r in g if r.color == "Green")
        yellow = sum(1 for r in g if r.color == "Yellow")
        red = sum(1 for r in g if r.color == "Red")
        out[sheet] = dict(n=len(g), green=green, yellow=yellow, red=red,
                          pct_has_tag=100 * (green + yellow) / len(g))
    return out


def claim_table(rows: list[StandardRow]) -> dict[str, int]:
    """§5 claim axis: Yes/blank x has-tag/no-tag."""
    out = {"yes_tag": 0, "yes_notag": 0, "blank_tag": 0, "blank_notag": 0}
    for r in rows:
        has_tag = bool(r.tags)
        key = ("yes" if r.claim == "Yes" else "blank") + ("_tag" if has_tag else "_notag")
        out[key] += 1
    return out


def flags_summary(rows: list[StandardRow]) -> dict[str, int]:
    """
    §5 flags axis. flagged_rows and yellow_rows are expected to differ --
    that difference (Green-with-flag rows) is the point, not a bug.
    """
    return dict(
        flagged_rows=sum(1 for r in rows if r.flagged),
        yellow_rows=sum(1 for r in rows if r.color == "Yellow"),
        green_with_flag=sum(1 for r in rows if r.color == "Green" and r.flagged),
    )


def grade_match_distribution(rows: list[StandardRow]) -> dict[str, int]:
    """Tag-grain grade_match counts over the given rows."""
    out: dict[str, int] = {}
    for r in rows:
        for t in r.tags:
            out[t.grade_match] = out.get(t.grade_match, 0) + 1
    return out
