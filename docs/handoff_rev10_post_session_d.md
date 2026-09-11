# Handoff rev 10 — after Session D, with the repo in version control

Self-contained. Paste as the first message of a new chat.

Supersedes rev 9 as the current state document. Rev 9's rulings R1–R6 all
still stand and are not relitigated here — this document records what has
since been built, what was measured, and what is next.

Every figure below was measured against the working tree, not carried
forward from an earlier document. Predicates are given so they can be re-run
rather than trusted.

---

## 0. State of the repo, measured

| Predicate | Result |
|---|---|
| `git log --oneline \| head -3` | `f446d86` repo brought up to date · `31b3716` un-ignore `mh2_seq.db` · `38da775` initial commit |
| `git ls-files \| wc -l` | 126 |
| `git status --porcelain` (modified) | clean |
| `git status --porcelain -uall \| grep "^??"` | one file — `notebooks/diff_all_states.py` |
| `len(coverage.build_rows(con))` | 2,987 |
| Green / Yellow / Green-with-flag | 682 / 102 / 82 |
| `SELECT COUNT(*) FROM node_standards` | 1,995 |
| `SELECT COUNT(*) FROM nodes` | 353 |
| `SELECT COUNT(reviewed_by), COUNT(reviewed_at) FROM node_standards` | 0, 0 |
| `mh2_seq.db` row counts | `standard_review` 0 · `standard_color_override` 0 · `tag_review` 0 · `tag_proposal` **1** |
| `grep -rhn "^def test_\|^    def test_" tests/*.py \| wc -l` | 236 |
| `grep -c "@app\." review_api.py` | 12 — 4 GET, 8 writes (5 POST, 3 DELETE) |
| `git ls-files --error-unmatch mh2/coverage.py review_api.py scripts/render_static.py mh2/review_store.py mh2/reconcile_review.py mh2/node_lookup.py mh2/schema_seq.sql app/db.py data/build/mh2_seq.db` | all tracked |
| `git check-ignore -q data/build/mh2_seq.db` | not ignored (i.e. tracked, as intended) |
| `git check-ignore -q data/build/mh2.db` | ignored |

Re-run the coverage figures with:

```sh
python -c "import sqlite3; from mh2 import coverage
con=sqlite3.connect('file:data/build/mh2.db?mode=ro',uri=True)
rows=coverage.build_rows(con)
print(len(rows), sum(1 for r in rows if r.color=='Green'),
      sum(1 for r in rows if r.color=='Yellow'),
      sum(1 for r in rows if r.color=='Green' and r.flagged))"
```

The one `tag_proposal` row is Session D's QA artifact — `K.CC.A.1` /
`COM:df4004fa423035ff`, state `withdrawn`, `proposed_by` `qa-session-d`. It is
correct to keep: rev 9's schema deliberately preserves proposal history, so a
withdrawal remains a recorded event. Because of that, `mh2_seq.db` will never
legitimately return to empty.

**Not verified by execution in this session:** the test suite was not run.
`.venv` is a macOS build and `pytest` was unavailable in the environment this
was written in. The 236 figure is a static count of test functions. Re-run
predicate: `.venv/bin/python -m pytest -q`. Do this first in any new session,
before changing anything, so a pre-existing failure is not mistaken for a new
one.

---

## 1. What is now built

**Session C — the write path (rev 9 R1/R2).** `mh2/schema_seq.sql`,
`mh2/node_lookup.py`, `mh2/review_store.py`, `mh2/reconcile_review.py`,
`review_api.py` (FastAPI, no SQL in routes — `grep -cin "select \|insert
\|update \|delete " review_api.py` → 0), plus `config.SEQ_DB` /
`config.SCHEMA_SEQ` and a reconcile step in `scripts/rebuild.py` behind
`--skip-review-reconcile`. `rebuild.py:278` unlinks `config.DB`; `:285`
creates `SEQ_DB` and never unlinks it.

**Session D — the audit pass wired to it.** `scripts/render_static.py` gates
a full write UI on `var ONLINE = location.protocol !== 'file:'` (`:344`).
Opened as `file://` the page still renders all 2,987 rows read-only with no
network activity; served over `uvicorn review_api:app` it fetches
`GET /api/audit` once, `GET /api/standards/{id}` on row expand, and
re-fetches only that one standard after each write. Writer identity is a
header name field backed by `localStorage` under key `mh2_writer_name`
(`:359`, `:363`). Verified in the emitted artifact, not just the source:
`data/reports/coverage.html` has the `ONLINE` gate before its first `fetch(`,
2,987 rows in its payload, and zero `<script src>`, `<link href>`, or
`http(s)://` anywhere in the file.

**FLAG A — closed (`31b3716`).** `mh2_seq.db` is authored state that
`rebuild.py` deliberately preserves, but it lived in `data/build/`, which
`.gitignore` excluded under a comment blessing `rm -rf data/build` as always
safe. It is now tracked in place. The glob is `data/build/*` followed by
`!data/build/mh2_seq.db` — **the trailing `/*` is load-bearing**, because git
cannot re-include a file whose parent directory is itself excluded. Do not
"simplify" it back to `data/build/`; that silently re-ignores the file with no
error. `README.md:84` now names the exception.

**Version control caught up (`f446d86`).** Until this commit, `mh2/coverage.py`
— the module DEFERRED §6 names as the owner of the denominator and the rollup
— plus `review_api.py`, `scripts/render_static.py`, all of Session C's
modules, ten test files, and the whole `app/` package had never been in git.
126 files are now tracked and the working tree is clean.

---

## 2. FLAG C — three Excel lock files were committed, and the ignore rule meant to stop them matches nothing

Reported per DEFERRED.md's standing discrepancy principle. Small, but fix it
before more commits land on top.

Measured:

```
git ls-files data/source/workbooks/ | grep '~\$'
  data/source/workbooks/~$H2 Stem and Leaf Spreadsheet G6 to Alg1.xlsm
  data/source/workbooks/~$H2_Stem_and_Leaf_Spreadsheet_K_to_G5.xlsm
  data/source/workbooks/~$Standards_for_Stems_Tagging.xlsx
```

All three are tracked. These are Excel's transient lock files — they exist
only while a workbook is open, so they will churn as modified-then-deleted
every time someone opens or closes those workbooks, and a fresh clone will
arrive carrying phantom lock files.

The `.gitignore` rule added to prevent this matches nothing:

```gitignore
data/sources/workbooks/**/~$*.xlsx
```

Three faults, each independently fatal:

1. `data/sources/` — the directory is `data/source/`, singular.
2. `.xlsx` — two of the three lock files are `.xlsm`.
3. `**/` requires an intervening directory level; the files sit directly in
   `workbooks/`.

Confirmed: `git check-ignore -v "data/source/workbooks/~\$test.xlsm"` exits 1
(no match).

Note also that `.gitignore` already carries a working rule for the ladders
directory, `data/source/**/~$*.docx`, which is the pattern to copy.

**The fix, in one commit:**

```gitignore
# Office lock files (~$*) left anywhere under data/source by Word or Excel
data/source/**/~$*
```

Then untrack the three that already landed — `.gitignore` does not apply to
tracked files:

```sh
git rm --cached "data/source/workbooks/~\$H2 Stem and Leaf Spreadsheet G6 to Alg1.xlsm" \
                "data/source/workbooks/~\$H2_Stem_and_Leaf_Spreadsheet_K_to_G5.xlsm" \
                "data/source/workbooks/~\$Standards_for_Stems_Tagging.xlsx"
```

Acceptance: `git ls-files data/source/workbooks/ | grep '~\$'` returns
nothing; `git check-ignore -q "data/source/workbooks/~\$anything.xlsm"` exits
0; the three real workbooks (`H2 Stem and Leaf Spreadsheet G6 to Alg1.xlsm`,
`H2_Stem_and_Leaf_Spreadsheet_K_to_G5.xlsm`,
`Standards_for_Stems_Tagging.xlsx`) are still tracked.

Also loose, decide either way: `notebooks/diff_all_states.py` is the only
untracked file in the tree. `notebooks/` has nothing tracked in it. Commit the
file or ignore the directory; leaving one stray untracked file is what makes a
real one easy to miss next time.

---

## 3. Next items, in order

### 3.1 FLAG C (above)

One commit. No brief needed beyond §2.

### 3.2 A writer pilot on the audit surface — not a code session

Nothing about how writers actually use this surface is known. `mh2_seq.db`
holds zero real judgments. Rev 9's R6 action set (mark reviewed, override
color, propose a tag) and its three tag outcomes (`confirmed`,
`insufficient`, `wrong_node`) have never been exercised by anyone but QA.

Suggested shape: one or two writers, a couple of hours, starting from the 82
computed-Green-and-flagged rows — rev 9 §4's entry point, and still the
highest-yield filter because it is where the rollup and the flags disagree.

What to collect: which outcomes writers actually reach for, whether "override
the color" gets used or avoided, how long a row takes, and whether the
proposal rationale field gets real text or gets skipped.

Why before 3.3: proposals created here are the only thing that will populate
the edit worklist. Build that surface first and it renders correctly and
empty.

Run it with:

```sh
python scripts/render_static.py       # if coverage.html needs rebuilding
uvicorn review_api:app
```

`data/reports/coverage.html` is now git-ignored (1.8MB, regenerated every
run), so a fresh clone must run `render_static.py` before `/` will serve
anything — `review_api.py:73-77` returns a 404 naming that script when the
file is absent.

### 3.3 Session E — the edit pass

`GET /api/worklist` works and has no consuming UI. It returns
`by_ladder_file` (grouped by ladder file, ordered by node `seq` within a
file) plus `standing_overrides`. Rev 9 §4 and R4 govern the surface:
**grouped by ladder file, not by stem**, because
`ComparingandOrdering` serves two stems and a stem grouping would make a
writer open that document twice. Rev 9 acceptance criterion 7 is the test.

This is the smaller of the two UI sessions and consumes an endpoint already
verified against real data. It will want a brief, and D1–D6 from
`docs/brief_session_d_audit_write_ui.md` mostly carry over unchanged — same
single-file progressive-enhancement design, same `ONLINE` gate, same
writer-name field, same re-fetch-one-thing-after-write rule.

### 3.4 Hosting, HTTPS, auth

Rev 9 R1's intended shape: HTTP Basic with per-writer credentials in
environment variables. This is the item that dissolves several others rather
than solving them:

- Writer identity (§4 below) stops being a self-declared text field.
- The `mh2_seq.db` distributed-copy merge problem in DEFERRED disappears
  under a single server-owned database.
- The binary-churn tradeoff accepted in `31b3716` stops mattering.

`review_api.py` was built for this — all state moves over HTTP, no page
touches SQLite, and nothing assumes localhost. Worth an architecture
conversation before it is a coding session.

### 3.5 Carried small items

- Retire `node_standards`' three unused review columns (`status`,
  `reviewed_by`, `reviewed_at`) as its own commit. Still measured at 0, 0 and
  labeled not-for-use in `mh2/schema.sql`'s comments. Note the existing
  Streamlit tool's `app/db.py:318 write_ruling()` **does** write these three
  columns, so retiring them is a change to `app/`, not just a schema drop —
  scope it deliberately.
- Out-of-scope grades (GEO, A2, ambiguous HS). Carried unruled since rev 8.
- DEFERRED §0's field table still spells the property `colour` while the code
  says `color`. A typo in a closed contract's table, not a rename. Fix in
  whatever commit next touches that file.

---

## 4. Open rulings

- **Writer identity (DEFERRED §2.2).** Session D's header name field is
  provisional and trusted entirely — not authentication, not per-stem
  identity. Superseded by 3.4, so this may never need its own ruling.
- **`/api/audit` and `/api/standards/{id}` have no cache.** Measured in
  Session D at ~114ms and ~15–25ms against the real 160MB database, which was
  judged fine. Both rebuild coverage from scratch per request. Revisit if
  `mh2.db` grows enough to degrade those figures, or if `/api/audit` starts
  being called more than once per page load.
- **FLAG C** (§2) until committed.

---

## 5. Do not touch

Unchanged from rev 9 §5 and the Session C/D briefs:

- `mh2/coverage.py` and the rollup computation. Color stays derived; a writer
  override is a separate authored layer.
- `ROW_DICT_KEYS` / `row_dict` / `tag_dict` field sets — DEFERRED §0 is
  CLOSED, and `tests/test_render_static.py:55` and `:149` enforce it.
- `mh2.db`'s schema and `scripts/rebuild.py`'s ingest order.
- `node_standards.status` / `.reviewed_by` / `.reviewed_at` — not-for-use.
- The `app/` package. MH2's existing Streamlit alignment-review tool; its one
  write path is `app/db.py:318 write_ruling()` → `node_standards`. It already
  does the full tag CRUD that R6 keeps out of scope for the new tool. Except
  as noted in 3.5, nothing here is a reason to open it.
- The `data/build/*` + `!data/build/mh2_seq.db` glob form (§1).
- SQL in routes; SQLite from the page.
- The static render's sheet-then-grade shape and existing filters. Rev 8 §1.2
  holds: no stem filter.
- The standards-coverage tool (standing project-wide restriction).
- **Ladders remain the source of truth for standards tagging.** The tagging
  workbook has drifted and is not the record. The tool must not become a
  third source of truth.
