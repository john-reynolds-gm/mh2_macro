"""
ingest_ladders.py — read Learning Ladder docs into the node tables.

Two readers, one normalizer:

  read_docx()      real .docx, via python-docx. Adapted from the team's
                   existing parse_lesson_ladder.py (document-order walk,
                   heading-to-table binding).
  read_markdown()  markdown rendition of a ladder. Same table structure,
                   pipe-delimited. Useful for review exports and for any
                   pipeline where the .docx has already been converted.

Both emit the same list-of-dicts, so downstream code never branches on format.

Node identity
-------------
node_id is assigned by the database and never derived from node text. Nodes
are matched across re-ingests on source_key = stem_id + normalized node text.
Consequences, stated plainly so nobody is surprised later:

  * Re-running ingest on an edited ladder MATCHES unchanged nodes and keeps
    their alignments.
  * A node whose wording was edited looks like a NEW node and the old one
    looks DELETED. The run reports both so a human can merge them. This is
    deliberate — silently transferring review decisions across a reworded
    node is how you end up with alignments nobody actually approved.

Run: python ingest_ladders.py --db mh2.db --glob "/mnt/project/MH2_PK5_*.docx"
"""

import argparse
import glob as globmod
import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path

from mh2.normalize import (extract_codes_inline, parse_code_cell,
                           parse_lesson_refs)

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def full_text(element) -> str:
    """
    A docx paragraph or table-cell element's text, INCLUDING equation text.

    python-docx's own `.text` walks only `w:t` runs and silently skips
    `m:oMath`/`m:t` -- Word's equation-editor math zones. A cell reading
    'Solve equations of the form [ax=d, equation-editor object].' comes back
    as 'Solve equations of the form .' with the equation dropped. Node
    identity is a hash of this text (source_key), so every DIFFERENT
    equation form on the same ladder collapsed into the SAME node --
    observed as six distinct rows reporting as one collision on
    MH2_6A1_One-Variable Equations.docx (and twelve on the Inequalities
    ladder). Walking w:t and m:t together, in document order, is the fix.

    A table cell holding several standard codes as separate paragraphs (one
    per line) has no `w:br`/`w:cr` between them -- those only mark a line
    break INSIDE a paragraph, not the boundary between two `w:p` siblings.
    Without a separator there, e.g. "7.SP.C.5" and "7.SP.C.7.a" on their own
    lines came back as one glued "7.SP.C.57.SP.C.7.a", corrupting the CCSS
    anchor for every code that follows the first on a multi-line cell.
    Emitting "\\n" at each new `w:p` (besides the element itself, so a
    single-paragraph call isn't given a leading blank line) fixes it.
    """
    parts = []
    for node in element.iter():
        if node.tag == _W_NS + "p" and node is not element and parts:
            parts.append("\n")
        elif node.tag == _W_NS + "t" or node.tag == _M_NS + "t":
            parts.append(node.text or "")
        elif node.tag == _W_NS + "tab":
            parts.append("\t")
        elif node.tag in (_W_NS + "br", _W_NS + "cr"):
            parts.append("\n")
    return "".join(parts)


# ---------------------------------------------------------------- field map

FIELD_MAP = {
    "knowledge graph node": "_node_names",
    "knowledge graph nodes": "_node_names",
    "rough lesson objectives": "_node_names",
    "goal": "goal",
    "goals": "goal",
    "student-facing example": "student_facing_example",
    "terminology": "terminology",
    "mathematical models": "mathematical_models",
    "strategies": "strategies",
    "specifications": "specifications",
    "leaves to include": "leaves_to_include",
    "considerations": "considerations",
    "required skills/cases": "required_skills_cases",
    "misconceptions": "misconceptions",
    "existing product reference, if any": "product_reference",
    "grade (or leaf)": "grade_or_leaf",
    "notes related to standards, grade, or leaf": "standards_notes",
    "notes for intervention, extension, or optional activities": "intervention_notes",
    "additional notes": "additional_notes",
}

SCALAR_FIELDS = {"goal", "student_facing_example", "grade_or_leaf"}

FOOTNOTE_RE = re.compile(r"\[\^[a-z0-9]+\]", re.I)
ASSOC_RE = re.compile(
    r"Associated\s+Concepts?\s+from\s+Other\s+Stems?\s*:?\s*(.*)", re.I | re.S)

# Leading markdown heading hashes, stripped before we look for the label.
MD_HASH_RE = re.compile(r"^\s*#{1,6}\s*")

# Deliberately NOT anchored with `$`. Several headings are a single Word
# paragraph carrying two lines:
#
#     Concept/Skill: Identify and use ordinal numbers.
#     D+J: First-Second Ordinal Counter
#
# `.` does not cross a newline, so `(.*)$` cannot match the whole paragraph and
# the heading was silently missed entirely — the Comparing and Ordering ladder
# read as 3 concept/skills instead of 7 because six of its seven headings look
# like this. Dropping the anchor takes the first line as the label and leaves
# the `D+J:` product cross-reference out of it.
CONCEPT_LABEL_RE = re.compile(r"^Concept/Skills?\s*:\s*(.*)", re.I)

# Authors mark drafting progress in the label slot itself: some headings read
# only `Concept/Skill: (Megan—DONE)`, with the real label in the NEXT paragraph.
# Treat a label that is nothing but such a marker as empty so continuation
# absorption fires. Matched only when it is the WHOLE label — a label that
# merely ends with '(Megan-DONE)' is left alone, because node and concept text
# feed source_key and rewriting them would make every affected node look new
# on the next ingest.
AUTHOR_STATUS_RE = re.compile(
    r"^\(\s*[A-Z][a-z]+\s*(?:[-–—]\s*)?(?:done)?\s*\)$", re.I)


def parse_concept_heading(raw, require_hash: bool = False) -> str | None:
    """
    Read a paragraph (or markdown line) as a Concept/Skill heading.

    Returns the label, "" when the heading carries no usable label (the caller
    should absorb the next paragraph as a continuation), or None when the text
    is not a Concept/Skill heading at all.

    Cleaning happens FIRST. Real headings in the source include
    `Concept[^c3]/Skill[^c4]:` and `C[^c63][^c64][^c65]oncept/Skill:` — comment
    anchors split the label across runs. On the .docx path those anchors live
    outside Paragraph.text so they never appear here, but on the markdown path
    they are inline and the match fails without stripping them.
    """
    text = clean(raw)
    if require_hash:
        if not MD_HASH_RE.match(text):
            return None
        text = MD_HASH_RE.sub("", text)
    else:
        text = MD_HASH_RE.sub("", text)

    m = CONCEPT_LABEL_RE.match(text)
    if not m:
        return None
    label = m.group(1).strip()
    if AUTHOR_STATUS_RE.match(label):
        return ""
    return label


def clean(text) -> str:
    """Strip markdown bold, footnote refs, NBSPs, and collapse whitespace."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = FOOTNOTE_RE.sub("", s)
    s = s.replace("**", "").replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def label_key(text: str) -> str | None:
    """Map a table row label to a canonical field name."""
    k = clean(text).lower().rstrip(":").strip()
    if k in FIELD_MAP:
        return FIELD_MAP[k]
    for label, field in FIELD_MAP.items():
        if k.startswith(label[:18]) and len(k) < len(label) + 12:
            return field
    return None


def split_items(value: str) -> list[str]:
    """Split a cell into list items on newlines and bullets."""
    if not value:
        return []
    parts = re.split(r"\n+|(?<=[a-z0-9)\.])\s{2,}(?=[A-Z])|\u2022", value)
    return [p.strip(" -•\t") for p in parts if p and p.strip(" -•\t")]


# ------------------------------------------------------------- md reader

def _md_tables(block: str) -> list[list[list[str]]]:
    """Extract pipe tables from a markdown block as row-lists of cells."""
    tables, current = [], []
    for line in block.splitlines():
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # separator row
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def read_markdown(path: Path, stem_name: str, issues: list | None = None) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    text = text.split("\n## Comments", 1)[0]  # comments handled separately

    # Split into concept sections, keeping the heading text.
    sections, heading, buf = [], None, []
    awaiting = False
    for line in text.splitlines():
        label = parse_concept_heading(line, require_hash=True)
        if label is not None:
            if heading is not None or buf:
                sections.append((heading, "\n".join(buf)))
            heading, buf = label, []
            awaiting = label == ""
            continue
        # Continuation heading: '### Concept/Skill:' with no label, followed by
        # a separate heading paragraph that holds it ('### Make and describe
        # turns)'). Absorb it rather than leaving the section unlabelled.
        if awaiting and MD_HASH_RE.match(line) and MD_HASH_RE.sub("", clean(line)).strip():
            heading = MD_HASH_RE.sub("", clean(line)).strip()
            awaiting = False
            continue
        buf.append(line)
    sections.append((heading, "\n".join(buf)))

    nodes = []
    for heading, block in sections:
        if heading is None:
            continue
        if heading == "":
            # A Concept/Skill heading with no label and no continuation. This is
            # a hole in the source document, not a parser failure — report it so
            # an author can fill it in.
            if issues is not None:
                issues.append({"file": Path(path).name, "kind": "unlabelled_concept_skill"})
        for table in _md_tables(block):
            fields: dict[str, list[str]] = {}
            for row in table:
                if len(row) < 2:
                    continue
                field = label_key(row[0])
                if field is None:
                    continue
                fields[field] = [clean(c) for c in row[1:]]
            if "_node_names" not in fields:
                continue  # auxiliary table, not a node table
            names = [n for n in fields["_node_names"] if n]
            if not names:
                names = [heading]
            for i, name in enumerate(names):
                node = {"node_text": name, "concept_skill": heading,
                        "stem_name": stem_name, "source_file": Path(path).name}
                for field, cells in fields.items():
                    if field == "_node_names":
                        continue
                    val = cells[i] if i < len(cells) else (cells[0] if len(names) == 1 else "")
                    node[field] = val
                nodes.append(node)
    return nodes


# ------------------------------------------------------------ docx reader

def _node_columns(row) -> list[tuple[int, str]]:
    """
    Node-name cells in a table row, as (grid column index, text).

    python-docx returns one entry per grid column, so a cell merged across two
    columns comes back TWICE with identical text. Taken at face value that reads
    as two nodes with the same name and the same standards cell — and because
    source_key is stem + normalized node text, the two then collide and merge
    again silently. Collapsing the merge here is what makes the node count
    match the document: 150 grid columns, 146 actual nodes.

    The grid index is returned, not just the text, because other rows in the
    same table (Goal, Specifications, the standards cell) are read positionally
    and their own merges have to line up with this column.
    """
    cols: list[tuple[int, str]] = []
    prev = None
    for i, cell in enumerate(row.cells):
        if cell._tc is prev:
            continue            # same merged cell, repeated for the next column
        prev = cell._tc
        if i == 0:
            continue            # column 0 is the row label
        text = clean(full_text(cell._tc))
        if text:
            cols.append((i, text))
    return cols


def _resolve_pending_label(pending: list[str], name_cols: list[tuple[int, str]],
                           filename: str, issues: list | None):
    """
    Decide what an empty 'Concept/Skill:' heading's continuation paragraphs mean.

    Two different authoring habits produce an empty label, and they mean
    different things:

      ONE continuation  — the author simply put the label on the next line.
          Angles and Whole Numbers do this; the table underneath can hold any
          number of nodes, all belonging to that one concept/skill.

      SEVERAL continuations, one per node column — the author consolidated
          several single-node concept/skills under one heading and one table to
          save space in the document. The Time ladder has the only instance:
          'Read and write moments to the nearest 5 minutes' and 'Use a.m. and
          p.m.', two bullets against two node columns. Counting them as one
          loses a concept/skill.

    Returns (heading, pending) where heading is a plain string, or a
    {node column -> label} dict for the consolidated case.
    """
    if not pending:
        # Reached the node table with no label at all. A hole in the source, not
        # a parser failure — report it so an author can fill it in.
        if issues is not None:
            issues.append({"file": filename, "kind": "unlabelled_concept_skill"})
        return None, None

    if len(pending) == 1:
        return pending[0], None

    if len(pending) == len(name_cols):
        return {col: label for (col, _), label in zip(name_cols, pending)}, None

    # Several continuations that do not line up with the node columns. Don't
    # guess a pairing — use the first and say so out loud.
    if issues is not None:
        issues.append({"file": filename, "kind": "ambiguous_continuation",
                       "detail": f"{len(pending)} labels, {len(name_cols)} node columns: "
                                 + " | ".join(pending)})
    return pending[0], None


def read_docx(path: Path, stem_name: str, issues: list | None = None) -> list[dict]:
    """Requires python-docx. Walks the body in document order."""
    from docx import Document
    from docx.table import Table

    doc = Document(str(path))
    body = doc.element.body
    heading = None
    # Continuation paragraphs collected after an empty 'Concept/Skill:' heading.
    # None means we are not waiting for a label.
    pending: list[str] | None = None
    nodes = []

    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            txt = clean(full_text(child))
            label = parse_concept_heading(txt)
            if label is not None:
                heading = label or None
                pending = [] if label == "" else None
            elif pending is not None and txt:
                pending.append(txt)
        elif tag == "tbl":
            table = Table(child, doc)
            fields: dict[str, list[str]] = {}
            name_cols: list[tuple[int, str]] | None = None
            for row in table.rows:
                cells = [clean(full_text(c._tc)) for c in row.cells]
                if len(cells) < 2:
                    continue
                field = label_key(cells[0])
                if field is None:
                    continue
                if field == "_node_names":
                    name_cols = _node_columns(row)
                fields[field] = cells[1:]
            if name_cols is None:
                continue        # auxiliary table (PROGRESSION), not a node table

            if pending is not None:
                heading, pending = _resolve_pending_label(
                    pending, name_cols, Path(path).name, issues)

            # Fall back to the concept/skill label when the table names no
            # nodes; column 1 then carries that single node's fields.
            columns = name_cols or ([(1, heading)] if heading else [])
            for col, name in columns:
                # heading is a dict when one heading covers several
                # concept/skills, keyed by node column — see
                # _resolve_pending_label.
                cs = heading.get(col) if isinstance(heading, dict) else heading
                node = {"node_text": name, "concept_skill": cs,
                        "stem_name": stem_name, "source_file": Path(path).name}
                for field, vals in fields.items():
                    if field == "_node_names":
                        continue
                    # vals is cells[1:], so grid column `col` sits at col - 1.
                    idx = col - 1
                    if idx < len(vals):
                        node[field] = vals[idx]
                    else:
                        node[field] = vals[0] if len(columns) == 1 else ""
                nodes.append(node)
    return nodes


# ---------------------------------------------------------------- persist

def source_key(stem_id: str, node_text: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", node_text.lower()).strip()
    return f"{stem_id}:{hashlib.sha1(norm.encode()).hexdigest()[:16]}"


# One token between underscores, NOT `[\w-]+` -- that class includes the
# underscore itself, so it greedily swallows every remaining segment up to
# the LAST underscore in the filename instead of just the grade-band token
# right after 'MH2_'. Generalized from a hardcoded 'MH2_PK5_' so it also
# strips 'MH2_6A1_', 'MH2_6-A1_', 'MH2_GA1_', and whatever the next grade
# band is named -- a hardcoded PK5-only strip is exactly what silently broke
# on the first 6-9 ladder filenames.
_GRADE_BAND_PREFIX_RE = re.compile(r"^MH2_[^_]+_")
# A leading 'WordsandWords_' category label some filenames carry ahead of the
# actual topic, e.g. 'NumberSystemsandStructures_ComparingandOrdering...'.
_LEADING_CATEGORY_RE = re.compile(r"^[A-Za-z]+(?:and|And)[A-Za-z]+_")
# Trailing "this is a ladder document" labels, and the '_new' browser-
# download-duplicate suffix that can ride along with them.
_TRAILING_LABEL_RE = re.compile(r"_(?:LessonLadder|Ladder|Stem)(?:_new)?$",
                                re.IGNORECASE)


def derive_stem_name(filename: str) -> str:
    """
    Filename -> a best-effort topic name, for stem resolution.

    Shared by ingest_ladders.main() and scripts/ingest_new.py so they can
    never drift into computing two different stem names for the same file --
    that drift is exactly what caused an already-correctly-ingested ladder to
    look 'new' under one caller and not the other.
    """
    name = re.sub(r"\.(docx|md)$", "", filename, flags=re.IGNORECASE)
    name = _GRADE_BAND_PREFIX_RE.sub("", name)
    name = _LEADING_CATEGORY_RE.sub("", name)
    name = _TRAILING_LABEL_RE.sub("", name)
    return name.replace("_", " ").strip()


def resolve_stem(cur, stem_name: str, *, allow_create: bool = True) -> str | None:
    """
    Match a ladder filename to a stem already loaded from the workbook.

    Word-overlap match first. Below that, `allow_create` controls what
    happens when NOTHING overlaps:

      True (default; ingest_ladders.main()'s production path, unchanged)
          derive a 3-letter code from the stem name and use it -- reusing it
          silently if that code already names a real stem (which is how
          'Comparing and Ordering' has always resolved to the pre-existing
          'COM' stem: pure coincidence of the derived prefix, not a real word
          match, and changing that here would break a file that already
          ingests correctly). If the code does NOT already exist, insert it.

      False (scripts/ingest_new.py's ad hoc path)
          the insert-a-new-stem branch never fires. A derived code that
          happens to already exist is still returned (preserves the
          'Comparing and Ordering' case above); a code that does not exist
          returns None instead of silently inventing one. Measured need for
          this: 7 distinct new topic names collapse to just 3 derived codes
          (NUM, ONE collide across genuinely different topics; EXP
          coincidentally collides with the unrelated existing 'Expressions
          and Equations' stem) -- there is no filename heuristic that makes
          auto-creation safe for a brand-new stem, so it must be a human
          decision, the same way stems.csv already treats this mapping as
          hand-maintained data everywhere else in the project.
    """
    words = set(re.findall(r"[a-z]+", stem_name.lower())) - {
        "mh2", "pk5", "lessonladder", "lesson", "ladder", "stem", "and",
        "of", "the"}
    best, best_score = None, 0
    for sid, name in cur.execute("SELECT stem_id, name FROM stems").fetchall():
        nw = set(re.findall(r"[a-z]+", name.lower()))
        score = len(words & nw)
        if score > best_score:
            best, best_score = sid, score
    if best:
        return best
    sid = re.sub(r"[^A-Z]", "", stem_name.upper())[:3] or "GEN"
    exists = cur.execute("SELECT 1 FROM stems WHERE stem_id = ?", (sid,)).fetchone()
    if not exists and not allow_create:
        return None
    cur.execute("INSERT OR IGNORE INTO stems (stem_id, name) VALUES (?,?)", (sid, stem_name))
    return sid


def bound_stem_of(cur, source_file: str) -> str | None:
    """
    The stem_id this exact FILE was already bound to, if any node from it has
    ever been persisted.

    This is the fix for a re-derive-every-time bug: resolve_stem() only ever
    looks at filename TEXT, so a file whose real stem_id was a human decision
    (scripts/ingest_new.py --stem-id) that doesn't happen to fall out of that
    text (e.g. 'IRR' for 'IrrationalRealNumbers', which the fallback would
    derive as 'NUM') becomes unresolvable again on the very next plain
    preview -- and worse, a file that DOES coincidentally re-derive to some
    unrelated pre-existing stem (e.g. 'ExpressionsGeneral' -> fallback 'EXP',
    which collides with the real, unrelated 'Expressions and Equations'
    stem) silently overrides the human's actual choice every time. Once any
    node exists for this source_file, that decision is settled; it must never
    be re-guessed.
    """
    row = cur.execute(
        "SELECT DISTINCT stem_id FROM nodes WHERE source_file = ?",
        (source_file,)).fetchall()
    return row[0][0] if len(row) == 1 else None


def stem_map_stem_of(cur, source_file: str) -> str | None:
    """
    The stem_id stems.csv's `ladder_file` column declares for this file, per
    the stem_map table load_stems.py builds (ladder_path is the file resolved
    on disk) -- band = '6_9' ONLY.

    stems.csv is the project's hand-reviewed file<->stem mapping everywhere
    else (see load_stems.py's module docstring); ingest_ladders() ignoring it
    and falling straight to resolve_stem()'s fuzzy word-overlap match is what
    let 'ExpressionsGeneral' and 'One-Variable Equations' both land on the
    coincidentally-existing 'EXP' stem instead of their real, distinct
    stem_ids -- a bulk `--glob` rebuild has no per-file --stem-id to catch it.

    Restricted to band = '6_9': the 10 PK5 ladders already have a
    `ladder_file` row here too (unused until now), but their nodes have been
    living under resolve_stem()'s short workbook codes (ANG, TIM, ...) all
    along -- consulting stem_map for them would rename every PK5 node_id on
    the next plain rebuild, silently invalidating the paid rerank cache
    (keyed on node_id) and any node-keyed review data. That migration was
    weighed and explicitly declined; if it's ever wanted, do it as its own
    deliberate step, not a side effect of onboarding new 6-9 ladders.
    """
    if not source_file:
        return None
    row = cur.execute(
        "SELECT stem_id, masterlist_name FROM stem_map"
        " WHERE ladder_path LIKE ? AND band = '6_9'",
        (f"%/{source_file}",)).fetchone()
    if not row:
        return None
    stem_id, masterlist_name = row
    # stem_map's stem_id may not exist in `stems` yet (it's stems.csv's own
    # catalog, not necessarily the workbook's) -- same as stem_id_override
    # below, a resolvable stem must actually be joinable everywhere else.
    cur.execute("INSERT OR IGNORE INTO stems (stem_id, name) VALUES (?,?)",
                (stem_id, masterlist_name or stem_id))
    return stem_id


def persist(cur, nodes: list[dict], run_id: str, *,
           stem_id_override: str | None = None,
           stem_name_override: str | None = None,
           allow_create: bool = True) -> dict:
    """
    Stem resolution, in order:

      1. This file already has nodes -> use their stem_id. Settled, not
         re-derived. See bound_stem_of()'s docstring for why this has to
         come first.
      2. `stem_id_override` -- the explicit, human-supplied answer for a
         file with no existing nodes (scripts/ingest_new.py --stem-id/
         --stem-name). Refuses loudly (raises) if that stem_id already names
         a DIFFERENT stem than `stem_name_override` says -- silently keeping
         the old name here is exactly the bug that let 'COOR_SYS' end up
         permanently mislabeled 'Probability'.
      3. stem_map (stems.csv's `ladder_file` column) -- the hand-reviewed
         mapping, when a row declares this exact file. See
         stem_map_stem_of()'s docstring: this is what makes a plain, no-flags
         `rebuild.py` safe for a file whose real stem doesn't fall out of its
         filename text, instead of silently re-deriving the wrong one every
         time the database is wiped and rebuilt.
      4. resolve_stem()'s fuzzy match, `allow_create` controlling whether an
         unresolvable file is refused (see resolve_stem's docstring).
    """
    stats = {"new": 0, "matched": 0, "standards": 0, "links": 0, "refs": 0,
             "refs_with_lesson": 0, "unresolved": 0}

    source_file = nodes[0]["source_file"] if nodes else None
    bound = bound_stem_of(cur, source_file) if source_file else None
    mapped = stem_map_stem_of(cur, source_file) if source_file else None

    if stem_id_override and not bound:
        existing_name = cur.execute(
            "SELECT name FROM stems WHERE stem_id = ?",
            (stem_id_override,)).fetchone()
        wanted_name = stem_name_override or stem_id_override
        if existing_name and existing_name[0] != wanted_name:
            raise SystemExit(
                f"--stem-id {stem_id_override} already names"
                f" {existing_name[0]!r}, not {wanted_name!r}. Pick a"
                f" different --stem-id, or omit --stem-name if"
                f" {existing_name[0]!r} is actually correct.")
        cur.execute("INSERT OR IGNORE INTO stems (stem_id, name) VALUES (?,?)",
                    (stem_id_override, wanted_name))

    for n in nodes:
        stem_id = bound or stem_id_override or mapped or resolve_stem(
            cur, n["stem_name"], allow_create=allow_create)
        if stem_id is None:
            stats["unresolved"] += 1
            continue
        key = source_key(stem_id, n["node_text"])
        row = cur.execute("SELECT node_id FROM nodes WHERE source_key=?", (key,)).fetchone()
        if row:
            node_id = row[0]
            cur.execute("UPDATE nodes SET last_seen=CURRENT_TIMESTAMP WHERE node_id=?", (node_id,))
            stats["matched"] += 1
        else:
            seq = cur.execute("SELECT COUNT(*) FROM nodes WHERE stem_id=?", (stem_id,)).fetchone()[0]
            node_id = f"{stem_id}-{seq + 1:04d}"
            cur.execute(
                "INSERT INTO nodes (node_id, stem_id, seq, source_key, node_text,"
                " concept_skill, goal, grade_or_leaf, source_file)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (node_id, stem_id, seq + 1, key, n["node_text"], n.get("concept_skill"),
                 n.get("goal"), n.get("grade_or_leaf"), n.get("source_file")))
            stats["new"] += 1

        cur.execute("DELETE FROM node_fields WHERE node_id=?", (node_id,))
        for field, value in n.items():
            if field in SCALAR_FIELDS or field in (
                    "node_text", "concept_skill", "stem_name", "source_file"):
                continue
            for i, item in enumerate(split_items(value)):
                cur.execute(
                    "INSERT INTO node_fields (node_id, field, ordinal, value) VALUES (?,?,?,?)",
                    (node_id, field, i, item))

        # Standards written into the ladder by hand.
        pairs, _ = parse_code_cell(n.get("standards_notes", ""))
        codes = {c: a for c, a in pairs}
        for c in extract_codes_inline(n.get("standards_notes", "")):
            codes.setdefault(c, None)
        for code, annot in codes.items():
            cur.execute(
                "INSERT OR IGNORE INTO node_standards (node_id, standard_id, relation,"
                " caveat, source, status) VALUES (?,?,?,?,?,?)",
                (node_id, code, "exceeds" if annot and re.search(
                    r"requir|beyond|to at least|goes to", annot, re.I) else "aligned",
                 annot, "ladder_doc", "proposed"))
            stats["standards"] += 1

        # Existing product references.
        for ref in split_items(n.get("product_reference", "")):
            product = "TX BB" if "TX BB" in ref else ("EM2" if "EM2" in ref else None)
            # One row per lesson named, so the column joins the alignment
            # guides directly. A reference naming two lessons is two rows; one
            # naming none (Math Catalyst, module-only) keeps its single row
            # with a NULL lesson_id, so nothing is lost from the record.
            lessons = parse_lesson_refs(ref) or [None]
            for lesson_id in lessons:
                cur.execute(
                    "INSERT INTO product_refs (node_id, product, raw_ref,"
                    " lesson_id) VALUES (?,?,?,?)",
                    (node_id, product, ref, lesson_id))
                stats["refs"] += 1
                if lesson_id:
                    stats["refs_with_lesson"] += 1

        # 'Associated Concepts from Other Stems', buried in Additional Notes.
        m = ASSOC_RE.search(n.get("additional_notes", "") or "")
        if m:
            for item in split_items(m.group(1))[:8]:
                if len(item) < 3 or item.lower().startswith("instructional period"):
                    continue
                cur.execute(
                    "INSERT INTO node_links (node_id, related_text, link_type)"
                    " VALUES (?,?,?)", (node_id, item, "stated"))
                stats["links"] += 1

        cur.execute(
            "INSERT INTO ingest_log (run_id, source_file, action, detail)"
            " VALUES (?,?,?,?)",
            (run_id, n.get("source_file"), "upsert_node", node_id))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="mh2.db")
    ap.add_argument("--glob", required=True)
    ap.add_argument("--format", choices=["auto", "docx", "md"], default="auto")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    run_id = hashlib.sha1(str(Path(args.glob)).encode()).hexdigest()[:8]
    totals = {"new": 0, "matched": 0, "standards": 0, "links": 0, "refs": 0,
              "refs_with_lesson": 0, "unresolved": 0}

    for path in sorted(globmod.glob(args.glob)):
        p = Path(path)
        if p.name.startswith("~$"):
            continue    # Word lock file for a document somebody has open
        stem_name = derive_stem_name(p.name)

        fmt = args.format
        if fmt == "auto":
            head = p.open("rb").read(4)
            fmt = "docx" if head[:2] == b"PK" else "md"
        try:
            nodes = read_docx(p, stem_name) if fmt == "docx" else read_markdown(p, stem_name)
        except Exception as exc:            # noqa: BLE001 — report, keep going
            print(f"  FAIL {p.name}: {type(exc).__name__}: {exc}")
            continue

        stats = persist(cur, nodes, run_id)
        for k in totals:
            totals[k] += stats[k]
        print(f"  {len(nodes):3d} nodes ({fmt})  {p.name}")

    con.commit()
    con.close()
    print("totals:", totals)


if __name__ == "__main__":
    main()
