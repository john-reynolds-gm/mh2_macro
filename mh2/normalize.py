"""
normalize.py — standard-code parsing shared by every loader.

The source data writes standards a dozen different ways:

    K.CC.A.1                         plain CCSS
    CA.K.CC.4.a                      state, mirrors CCSS
    TX.1.5A-This goes to 120, ...    code + inline annotation, single hyphen
    CA.K.CC.3--added MF 5/29         code + provenance note, double hyphen
    SC.1.NR.2.2—note this standard   code + caveat, em dash
    ME.PK.MELDS.M.CCC.PS.1           long state code
    PTKLF 1.4                        not a state code at all
    CA.NBT.A.1                       missing grade segment (source typo)

parse_code_cell() splits a multi-line cell into (code, annotation) pairs and
reports anything it could not parse rather than silently dropping it. The
unparsed list is a QA output, not an error — read it.
"""

import re
from typing import NamedTuple

US_STATES = {
    "AK","AL","AR","AZ","CA","CO","CT","DE","FL","GA","HI","IA","ID","IL","IN",
    "KS","KY","LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND","NE","NH",
    "NJ","NM","NV","NY","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VA",
    "VT","WA","WI","WV","WY","DC",
}

# A code is a dotted token. Either state-prefixed (TX.K.2A) or bare CCSS
# (K.CC.A.1 / 2.NBT.A.1.a / HSA.REI.B.3).
# Segments may contain internal hyphens (SD.PK.CD-4.g, PA.M03.A-1.1.3), so a
# hyphen is only an annotation separator when followed by a LETTER-initial word
# of 3+ chars. A hyphen followed by a digit stays part of the code.
_SEG = r"[A-Za-z0-9]+(?:-(?:\d[A-Za-z0-9]*|[A-Za-z]{1,2}(?![A-Za-z])))*"
_CODE_RE = re.compile(
    r"^[\s*•\-–]*("
    rf"[A-Z]{{2}}\.{_SEG}(?:\.{_SEG})*"                      # state-prefixed
    rf"|(?:PK|K|HS[A-Z]?|\d{{1,2}})\.{_SEG}(?:\.{_SEG})*"    # CCSS
    r")"
)

# Separators that introduce a human note after a code.
_ANNOT_RE = re.compile(r"\s*(?:--|—|–|\u2014)\s*|(?<=[A-Za-z0-9])-(?=[A-Za-z]{2})")

_GRADE_RE = re.compile(r"^(PK|K|HS[A-Z]?|\d{1,2})$", re.I)


def normalize_code(code: str) -> str:
    """Uppercase the jurisdiction prefix, strip stray punctuation and spaces."""
    code = code.strip().strip(".,;:()[]").replace(" ", "")
    parts = code.split(".")
    if parts and len(parts[0]) == 2 and parts[0].upper() in US_STATES:
        parts[0] = parts[0].upper()
    return ".".join(p for p in parts if p)


def jurisdiction_of(code: str) -> str:
    """'TX.K.2A' -> 'TX';  'K.CC.A.1' -> 'CCSS'."""
    head = code.split(".")[0].upper()
    if head in US_STATES:
        return head
    return "CCSS"


def grade_of(code: str) -> str | None:
    """Best-effort grade from the code itself. Returns None when unclear."""
    parts = code.split(".")
    if parts and parts[0].upper() in US_STATES:
        parts = parts[1:]
    if parts and _GRADE_RE.match(parts[0]):
        g = parts[0].upper()
        return "K" if g == "K" else g
    return None


def normalize_grade(raw) -> str | None:
    """Normalize a grade cell ('Grade 1', 'Algebra I', 'K') to a short token."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    s = s.replace("Grade", "").replace("grade", "").strip()
    low = s.lower()
    if low.startswith("algebra 1") or low.startswith("algebra i") and "ii" not in low:
        return "A1"
    if low.startswith("algebra 2") or low.startswith("algebra ii"):
        return "A2"
    if low.startswith("geometry"):
        return "GEO"
    if low.startswith("pk") or low.startswith("pre"):
        return "PK"
    if s.upper() == "K":
        return "K"
    m = re.match(r"^(\d{1,2})$", s)
    return m.group(1) if m else s


def parse_code_cell(cell) -> tuple[list[tuple[str, str | None]], list[str]]:
    """
    Split a spreadsheet cell (or ladder table cell) into standard codes.

    Returns (pairs, unparsed) where pairs is [(normalized_code, annotation)]
    and unparsed is the list of lines no code could be recovered from.
    """
    pairs: list[tuple[str, str | None]] = []
    unparsed: list[str] = []
    if cell is None:
        return pairs, unparsed

    text = str(cell)
    if text.strip().lower() in ("", "nan", "none"):
        return pairs, unparsed

    # Cells separate codes by newline; some use commas or semicolons instead.
    text = text.replace("\\n", "\n")
    for chunk in re.split(r"[\n;]+", text):
        line = chunk.strip()
        if not line:
            continue
        m = _CODE_RE.match(line)
        if not m:
            unparsed.append(line)
            continue
        code = normalize_code(m.group(1))
        rest = line[m.end():].strip()
        if rest:
            rest = _ANNOT_RE.sub(" ", rest, count=1).strip().strip("-–—,. ")
        pairs.append((code, _trim_annotation(rest) or None))

        # Ladder cells often run several codes together on one line, because
        # the Word table cell used soft breaks. Recover the trailing ones
        # instead of leaving them inside the annotation text.
        tail = line[m.end():]
        for extra in _CODE_ANY_RE.finditer(tail):
            ec = normalize_code(extra.group(0))
            if ec and ec != code and not any(ec == c for c, _ in pairs):
                pairs.append((ec, None))
    return pairs, unparsed


_CODE_ANY_RE = re.compile(
    rf"\b(?:[A-Z]{{2}}\.{_SEG}(?:\.{_SEG})+"
    rf"|(?:PK|K|\d{{1,2}})\.(?:CC|OA|NBT|NF|MD|G|RP|NS|EE|SP|F)\.{_SEG}(?:\.{_SEG})*)"
)


def _trim_annotation(text: str) -> str:
    """
    Cut an annotation at the first embedded standard code. Everything from
    that point on is a code list, not a human note about the current code.
    """
    if not text:
        return ""
    m = _CODE_ANY_RE.search(text)
    if m:
        text = text[:m.start()]
    return text.strip().strip("-–—,.;:() ")


# ---------------------------------------------------------------------------
# §1 tokenizer — codes embedded in running prose, with positions.
#
# extract_codes_inline() below finds codes but throws the positions away, which
# is fine for the workbook. The ladder cells need more: residual prose has to be
# attached to the nearest PRECEDING code, so we need to know where each code
# started and stopped.
# ---------------------------------------------------------------------------

class CodeMatch(NamedTuple):
    """One standard code found in a block of text."""
    code: str                 # normalized, 'CCSS.' prefix stripped
    state: str | None         # None for CCSS
    grade: str | None
    start: int                # span in the ORIGINAL text
    end: int


_STATE_ALT = "|".join(sorted(US_STATES))

# PK, K, or 1-12. Deliberately NOT \d{1,2}: '99.NBT.1' and a page number are
# not standards, and the loose version matched decimals in prose.
_GRADE_ALT = r"PK|K|HS[A-Z]?|1[0-2]|[1-9]"

# A state-prefixed code may have a DIGIT-initial segment after the grade. This
# is the whole reason the §1 test list exists: TEKS writes TX.4.6A, and a
# tokenizer that requires a letter there returns zero TX codes and reports no
# error at all. Texas then looks absent from the ladders, which is wrong and
# looks like a data problem rather than a parser problem.
_STATE_CODE = (
    rf"(?P<state>{_STATE_ALT})\.(?P<sgrade>{_GRADE_ALT})"
    rf"\.(?P<srest>{_SEG}(?:\.{_SEG})*)"
)

# Same as _SEG but letter-initial, and note the '*' not '+' on the tail so a
# one-character domain still matches — 4.G.A.1 and K.G.B.4 are real codes.
_SEG_ALPHA = r"[A-Za-z][A-Za-z0-9]*(?:-(?:\d[A-Za-z0-9]*|[A-Za-z]{1,2}(?![A-Za-z])))*"

# A bare CCSS code must have a LETTER-initial domain right after the grade
# (4.G.A.1, K.CC.A.1, HSA.REI.B.3). Allowing a digit there would make every
# decimal in running prose ('round to 3.5') parse as a standard. The asymmetry
# with the state branch above is intentional, not an oversight.
#
# The trailing '+' (at least two segments after the grade) is what stops author
# shorthand from parsing as a standard. Cells write 'VA.3.NS.2.a and 2.b' and
# '5.NBT.A.3 and 3.b', meaning another breakout of the SAME parent — '2.b' on
# its own is not a code, and reading it as one invents a CCSS standard that
# does not exist. Every real CCSS code has a domain plus at least one more
# segment.
_CCSS_CODE = (
    rf"(?P<cgrade>{_GRADE_ALT})\.(?P<crest>{_SEG_ALPHA}(?:\.{_SEG})+)"
)

# The separator after the state prefix is missing or is a space: 'TX4.2H',
# 'FL3.GR.1.1', 'TX 4.4E', 'TX 4.4F'. Source typos, and unambiguous ones — the
# prefix is a real state and a grade digit follows it. Only ONE space is
# allowed, so a sentence that happens to end in a state abbreviation cannot
# reach across to a number on the far side of a line break.
_STATE_NODOT_CODE = (
    rf"(?P<state3>{_STATE_ALT})[ ]?(?P<s3grade>1[0-2]|[1-9])"
    rf"\.(?P<s3rest>{_SEG}(?:\.{_SEG})*)"
)

# A state code with NO grade segment. Pennsylvania puts the grade inside the
# domain (PA.M03.A-T.1.1.4 is grade 3) and VA.CD3.2e is a preschool framework
# code. §1's grammar requires a grade; these seven tags are real and dropping
# them silently is worse than carrying them with grade = NULL, so the grade
# prior in §4 simply does not apply to them.
_STATE_NOGRADE_CODE = (
    rf"(?P<state2>{_STATE_ALT})\.(?P<s2rest>{_SEG_ALPHA}(?:\.{_SEG})+)"
)

# Trailing range notation: '3.MD.C.5.a–b'. Only a single letter followed by a
# non-word character counts, so '-partially tagged' and '--added MF 5/29' stay
# annotations rather than becoming part of the code.
_RANGE_TAIL = r"(?P<range>\s*[-–—]\s*[A-Za-z](?![A-Za-z0-9]))?"

# Order matters. The grade-bearing state branch is tried first — that IS the §1
# disambiguation rule: a leading two-letter token is a state prefix when a grade
# FOLLOWS it (MD.1.GR.C.5 is Maryland) and a CCSS domain when a grade PRECEDES
# it (4.MD.C.5 is Measurement and Data). MD, NC and OK all collide, and getting
# this backwards silently reassigns standards between jurisdictions.
#
# The leading lookbehind refuses to start a match straight after a dot, so an
# unknown two-letter prefix cannot have its tail read as a bare CCSS code:
# 'VS.2.NS.1.g' (a typo for VA) must yield NOTHING and be reported, not become
# the CCSS standard '2.NS.1.g' under the wrong jurisdiction.
_TOKEN_RE = re.compile(
    # '[ \t]*' because two cells write 'CCSS. 3.MD.A.1' with a space after the
    # prefix. Not \s* — that would cross a newline and swallow the next line.
    rf"(?<![A-Za-z0-9.])\b(?:CCSS\.[ \t]*)?"
    rf"(?:{_STATE_CODE}|{_STATE_NOGRADE_CODE}|{_STATE_NODOT_CODE}|{_CCSS_CODE})"
    rf"{_RANGE_TAIL}", re.I)

_RANGE_SUFFIX_RE = re.compile(r"([A-Za-z])\s*[-–—]\s*([A-Za-z])$")


def _letters(first: str, last: str) -> list[str]:
    """['E','F','G'] for 'E','G'. Case is taken from `first`."""
    seq = [chr(c) for c in range(ord(first.lower()), ord(last.lower()) + 1)]
    return [c.upper() for c in seq] if first.isupper() else seq


def _expand_wide_range(code: str) -> list[str] | None:
    """
    Ranges written across whole codes rather than as a trailing letter.

    Two forms occur in the ladder cells, both keyed on what the text after the
    dash repeats from the text before it:

        TX.1.2E-1.2G   the tail '1.2' repeats, only the final letter differs
                       -> TX.1.2E, TX.1.2F, TX.1.2G   (TEKS breakouts)

        2.NBT.A.1-1.b  the segment before the dash repeats as the first segment
                       after it -> the parent plus its sub-parts
                       -> 2.NBT.A.1, 2.NBT.A.1.a, 2.NBT.A.1.b

    Returns None when neither form applies, which is the common case: most
    hyphens in these codes are part of a segment (SD.PK.CD-4.g, SC.PK.MTE-5p,
    PA.M03.A-T.1.1.4) and must be left completely alone.
    """
    if "-" not in code:
        return None
    left, _, right = code.rpartition("-")
    if not left or not right:
        return None

    # Form 1: same parent, differing final letter.
    if (left[-1].isalpha() and right[-1].isalpha()
            and left[-1].isupper() == right[-1].isupper()
            and len(right) > 1 and left[:-1].endswith(right[:-1])
            and ord(right[-1].lower()) > ord(left[-1].lower())):
        base = left[:-1]
        return [base + ch for ch in _letters(left[-1], right[-1])]

    # Form 2: parent, then the parent's own last segment plus a sub-part letter.
    right_head, _, right_tail = right.partition(".")
    if (right_head and right_tail and len(right_tail) == 1
            and right_tail.isalpha()
            and left.rpartition(".")[2] == right_head):
        return [left] + [f"{left}.{ch}" for ch in _letters("a", right_tail)]

    return None


def expand_ranges(code: str) -> list[str]:
    """
    '1.NBT.B.2.a-b' -> ['1.NBT.B.2.a', '1.NBT.B.2.b'].

    lesson_metadata writes sub-standard ranges instead of listing the members.
    Both the ASCII hyphen and the en dash occur. Codes with a hyphen INSIDE a
    segment (SD.PK.CD-4.g, PA.M03.A-1.1.3) are left alone — the range letters
    have to be the last thing in the code.
    """
    m = _RANGE_SUFFIX_RE.search(code)
    if not m:
        return _expand_wide_range(code) or [code]
    first, last = m.group(1), m.group(2)
    if first.isupper() != last.isupper():
        return [code]                      # 'A-b' is not a range, it is a typo
    if ord(last.lower()) <= ord(first.lower()):
        return [code]                      # 'a-a' or reversed: not a range
    head = code[:m.start()]
    # Case is preserved, not folded. TEKS breakouts are uppercase — TX.3.3F-G
    # must expand to TX.3.3F and TX.3.3G, and lowercasing them produces two
    # codes that match nothing in the standards table.
    letters = range(ord(first.lower()), ord(last.lower()) + 1)
    return [f"{head}{chr(c).upper() if first.isupper() else chr(c)}"
            for c in letters]


def find_codes(text: str) -> list[CodeMatch]:
    """
    Every standard code in a block of prose, in order, with its span.

    Ranges are expanded, so one '3.MD.C.5.a–b' in the text yields two matches
    sharing a span. Text that is not a code is simply not returned — the caller
    decides what the leftovers mean.
    """
    if not text:
        return []
    out: list[CodeMatch] = []
    for m in _TOKEN_RE.finditer(text):
        if m.group("state"):                         # TX.4.6A
            state = m.group("state").upper()
            grade = m.group("sgrade")
            base = f"{state}.{grade}.{m.group('srest')}"
        elif m.group("state2"):                      # PA.M03.A-T.1.1.4
            state = m.group("state2").upper()
            grade = None
            base = f"{state}.{m.group('s2rest')}"
        elif m.group("state3"):                      # TX4.2H -> TX.4.2H
            state = m.group("state3").upper()
            grade = m.group("s3grade")
            base = f"{state}.{grade}.{m.group('s3rest')}"
        else:                                        # 4.G.A.1
            state = None
            grade = m.group("cgrade")
            base = f"{grade}.{m.group('crest')}"

        # A range tail lives outside the code segments, so glue it back on
        # before expanding: 'MD.C.5.a' + '–b' -> 'MD.C.5.a-b' -> two codes.
        if m.group("range"):
            base += "-" + m.group("range").strip(" -–—")

        for code in expand_ranges(normalize_code(base)):
            out.append(CodeMatch(code, state,
                                 grade_of(code) or (grade.upper() if grade else None),
                                 m.start(), m.end()))
    return out


def extract_codes_inline(text: str) -> list[str]:
    """
    Pull every standard code out of a block of running prose.
    Used for ladder cells that mix codes and sentences freely.
    """
    if not text:
        return []
    pat = re.compile(
        r"\b(?:[A-Z]{2}\.[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)+"
        r"|(?:PK|K|\d{1,2})\.(?:CC|OA|NBT|NF|MD|G|RP|NS|EE|SP|F)\.[A-Za-z0-9.]+)"
    )
    seen, out = set(), []
    for m in pat.finditer(text):
        c = normalize_code(m.group(0))
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# --------------------------------------------------------------- lesson refs

# The ladder authors write the EM2 lesson a node draws on directly into the
# `existing product reference` cell -- 'EM2 G1 M6 L2'. The alignment guides key
# lessons as 'G1-M6-L2', so the two join once the reference is normalized, and
# that join is hand-written data on both sides: an author naming the lesson,
# and the guide naming the standards the lesson teaches.
#
# `product_refs.lesson_id` was NULL for all 428 rows until this existed.
#
# Only EM2 references resolve. 'Math Catalyst' is a different product with no
# lesson ids in the guides, and module-only references ('G1 M2') name a module
# rather than a lesson -- expanding those to every lesson in the module would
# manufacture links the author did not write, so they stay unresolved and
# counted.
_LESSON_GRADE = r"(?:G\s*PK|G\s*K|PK|K|G\s*\d)"
_LESSON_RE = re.compile(
    rf"\b(?P<grade>{_LESSON_GRADE})\s*"
    rf"M\s*(?P<module>\d+)\s*"
    rf"(?:T[A-Z]\s*)?"                       # optional topic, e.g. 'TA'
    rf"L\s*(?P<lesson>\d+)"
    rf"(?:\s*[-–—]\s*(?P<last>\d+))?",       # 'L3-4', 'Lessons 3–4'
    re.I)

# 'EM2 K M1, Topic D, Lesson 5' and 'EM2 PK M2, Topic C, Lessons 3-4'.
_LESSON_PROSE_RE = re.compile(
    rf"\b(?P<grade>{_LESSON_GRADE})\s*"
    rf"M\s*(?P<module>\d+)\s*,?\s*"
    rf"(?:Topic\s+[A-Z]\s*,?\s*)?"
    rf"Lessons?\s+(?P<lesson>\d+)"
    rf"(?:\s*(?:[-–—]|and|to)\s*(?P<last>\d+))?",
    re.I)


def _lesson_grade(raw: str) -> str:
    """'G1' -> 'G1', 'K' and 'GK' -> 'GK', 'PK' -> 'GPK'."""
    token = re.sub(r"\s+", "", raw).upper()
    if not token.startswith("G"):
        token = "G" + token
    return token


def parse_lesson_refs(raw: str) -> list[str]:
    """
    Every EM2 lesson id named in one product-reference cell.

    Ranges expand: 'Lessons 3-4' is two lessons, both of which the author is
    pointing at. Returns [] for anything that names no lesson, which includes
    Math Catalyst rows and module-only references.
    """
    if not raw:
        return []
    out = []
    for pattern in (_LESSON_RE, _LESSON_PROSE_RE):
        for m in pattern.finditer(raw):
            grade = _lesson_grade(m.group("grade"))
            module = int(m.group("module"))
            first = int(m.group("lesson"))
            last = int(m.group("last")) if m.group("last") else first
            if last < first or last - first > 20:
                last = first          # not a range; a stray adjacent number
            for n in range(first, last + 1):
                lesson = f"{grade}-M{module}-L{n}"
                if lesson not in out:
                    out.append(lesson)
    return out
