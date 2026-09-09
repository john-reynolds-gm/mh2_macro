# Brief — FLAG A: stop treating `mh2_seq.db` as a disposable build artifact

Small scoped session. One commit's worth of work. Self-contained; paste as the
first message of a new session.

Ruling: **un-ignore the file in place.** `mh2_seq.db` stays at
`data/build/mh2_seq.db` and becomes tracked; everything else in that directory
stays ignored.

---

## 1. Why this is now urgent rather than tidy

Measured in the repo root after Session D:

| Predicate | Result |
|---|---|
| `config.py:61` | `SEQ_DB = BUILD / "mh2_seq.db"` → `data/build/mh2_seq.db` |
| `.gitignore:11-12` | `# Disposable build artifacts (rm -rf data/build && rebuild.py is always safe)` / `data/build/` |
| `git check-ignore -q data/build/mh2_seq.db` | ignored |
| `SELECT COUNT(*) FROM tag_proposal` | **1** |
| that row | `(1, 'K.CC.A.1', 'COM:df4004fa423035ff', 'withdrawn', 'qa-session-d', 'MH2_PK5_NumberSystemsandStructures_ComparingandOrdering_LessonLadder.docx', 'qa verification cleanup')` |
| `standard_review` / `standard_color_override` / `tag_review` counts | 0 / 0 / 0 |
| `ls -a data/build/` | `mh2.db`, three `mh2.db.bak-*`, `mh2_seq.db`. No `-wal` / `-shm`. |
| `pragma journal_mode` on `mh2_seq.db` | `delete` |
| `scripts/rebuild.py:278` | `config.DB.unlink()` |
| `scripts/rebuild.py:285` | creates `SEQ_DB`, never unlinks it |

The contradiction: `rebuild.py` is deliberately careful never to delete
`mh2_seq.db` (R2 exists because `config.DB.unlink()` destroyed authored state
silently), but the repo's own documented safe-reset gesture — `rm -rf
data/build`, blessed in the `.gitignore` comment — deletes the directory
around it. `rebuild.py` then recreates an *empty* `mh2_seq.db` with no error
and no backup. R2's failure mode has reappeared one directory up, from the
ignore-file side instead of the code side.

Until Session D this was theoretical: all four tables held 0 rows. It no
longer is. And because rev 9's schema deliberately keeps proposal history
(`tag_proposal` is not keyed on the pair precisely so a withdrawal and a
later re-proposal remain two events), `mh2_seq.db` will never legitimately
return to empty. The exposure is permanent from here.

Three `mh2.db.bak-*` files in the same directory suggest the by-hand backup
instinct is already there.

---

## 2. FLAG B — the obvious one-line fix silently does nothing

**Verified, do not skip this.** Git cannot re-include a file whose parent
directory is itself excluded. So this form, which is what anyone would write
first, leaves the file ignored:

```gitignore
data/build/
!data/build/mh2_seq.db      # DOES NOT WORK — still ignored
```

Tested in a scratch repo:

```
FORM 1 (directory excluded, then negated)
  data/build/mh2.db      -> IGNORED
  data/build/mh2_seq.db  -> IGNORED     <-- the bug

FORM 2 (contents excluded, then negated)
  data/build/mh2.db                        -> IGNORED
  data/build/mh2.db.bak-20260826151220     -> IGNORED
  data/build/mh2_seq.db                    -> not ignored   <-- correct
```

The trailing `/*` is load-bearing: it excludes the directory's *contents*
rather than the directory, which leaves git free to descend and honour the
negation.

Reproduce:

```sh
cd /tmp && rm -rf gitig && mkdir -p gitig/data/build && cd gitig && git init -q .
touch data/build/mh2.db data/build/mh2_seq.db
printf 'data/build/\n!data/build/mh2_seq.db\n' > .gitignore
git check-ignore -q data/build/mh2_seq.db && echo "form 1: ignored (wrong)"
printf 'data/build/*\n!data/build/mh2_seq.db\n' > .gitignore
git check-ignore -q data/build/mh2_seq.db || echo "form 2: tracked (right)"
```

---

## 3. The change

Replace `.gitignore:11-12` with the contents-form plus a comment that names
the exception and says why:

```gitignore
# Build artifacts. `rm -rf data/build && rebuild.py` regenerates everything
# here EXCEPT mh2_seq.db, which is authored review state (rev 9 R2) and
# cannot be regenerated -- so it is tracked, and the glob is `data/build/*`
# rather than `data/build/` because git cannot re-include a file whose
# parent directory is excluded.
data/build/*
!data/build/mh2_seq.db
```

Then commit the file as it currently stands — one `withdrawn` proposal
included. That row is the Session D QA artifact and is correct to keep; it is
what the schema's history-preserving design intends.

**The README repeats the claim and must be amended in the same commit.**
Measured — `README.md:83-84`:

> The one rule worth internalizing: **`data/source` is read-only, `data/build`
> is disposable.** If anything looks wrong, delete `data/build` and rebuild.

That instruction is now wrong in one specific way, and it is phrased as the
one rule a new contributor should internalize. Amend it to carve out
`mh2_seq.db` explicitly — `data/build` is disposable *except* the authored
review database, which is tracked and cannot be regenerated. Keep the rule's
shape and brevity; do not turn it into a paragraph.

---

## 4. Tradeoff to accept, stated so it is a decision

`mh2_seq.db` is a 45KB SQLite binary. Tracking it means every writer session
produces an opaque binary diff, and concurrent writers will produce merge
conflicts git cannot resolve. This is accepted for now because:

- The alternative on offer was losing the file to a routine `rm -rf`.
- Hosting (R1) moves this to a single server-owned database and dissolves the
  concurrent-copy problem rather than solving it — the same reasoning rev 9
  applied to the `DEFERRED.md` distributed-copy item.
- Until hosting lands, the writer pilot is small enough that a conflict is a
  conversation, not a process.

Do not build a merge tool, a text-export sidecar, or a migration in this
session. If binary churn becomes painful before hosting, that is a new
DEFERRED entry, not this commit.

---

## 5. Do not touch

- `config.py`'s `SEQ_DB` path. The ruling is un-ignore in place, not relocate.
- `scripts/rebuild.py`. Its create-but-never-unlink handling at `:285` is
  already correct and is the behaviour this change protects.
- `mh2/schema_seq.sql`, `mh2/review_store.py`, `mh2/reconcile_review.py`.
- The `tag_proposal` row now in the file. Do not clean it out.
- `data/build/mh2.db` and the three `mh2.db.bak-*` files — these stay ignored.
- Everything on Session C's and D's do-not-touch lists, unchanged: `app/`,
  `mh2/coverage.py`, `ROW_DICT_KEYS`, `node_standards`' three unused columns,
  React, hosting, auth, the standards-coverage tool.

---

## 6. Acceptance criteria

1. `git check-ignore -q data/build/mh2_seq.db` exits **non-zero** (not
   ignored).
2. `git check-ignore -q data/build/mh2.db` exits **zero** (still ignored), and
   the same holds for each of the three `mh2.db.bak-*` files.
3. `git ls-files data/build/` lists exactly one path:
   `data/build/mh2_seq.db`.
4. `git status --porcelain data/build/` is empty after the commit.
5. `SELECT COUNT(*) FROM tag_proposal` in the committed file is 1, and the row
   is the `withdrawn` `K.CC.A.1` row above.
6. `python scripts/rebuild.py` still runs to completion, and afterwards
   `git status --porcelain data/build/` shows either nothing or a modification
   to `mh2_seq.db` alone — never a deletion, and never an untracked
   `data/build/mh2.db`.
7. `.venv/bin/python -m pytest -q` passes with no fewer passing tests than
   before the change (236 test functions at the time of writing).
8. `README.md:83-84`'s "`data/build` is disposable" rule names `mh2_seq.db`
   as the exception. Predicate: `grep -n "mh2_seq" README.md` returns at
   least one line.

---

## 7. DEFERRED.md edits this session owes

1. **Close FLAG A** in §7's carried-forward list: record the ruling
   (un-ignore in place), FLAG B's git-semantics gotcha so nobody later
   "simplifies" the glob back to `data/build/`, and §4's accepted binary-churn
   tradeoff with hosting as its resolution.
2. **Correct one sentence in §7.** It currently reads that `mh2_seq.db`
   "carries no session-D residue." Measured, `tag_proposal` holds 1
   `withdrawn` row. The row is correct and intended — `tag_proposal` keeps
   history by design — but the sentence overclaims, and a later session
   reading "no residue" will be surprised by a non-empty table. Replace it
   with the row's actual state and the schema's reason for keeping it.
