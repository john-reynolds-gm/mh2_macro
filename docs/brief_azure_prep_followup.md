# Brief — follow-up to the pre-Azure hardening session

**Written:** 2026-09-29. **For:** a Claude Code session in this repo.
**Predecessor:** `docs/brief_azure_prep_hardening.md`, whose §1–§6 landed.
**Authority:** `DEFERRED.md` §8, including its "Landed 2026-09-29" subsection.

Three items, all small, all consequences of the previous session rather than
new scope. Nothing here is blocked on IT. Item 1 is worth doing before the
previous session's work is committed; items 2 and 3 are not.

**Still out of scope, unchanged from the predecessor brief:** the Graph /
SharePoint fetch step, any auth change, the worklist UI, and re-measuring the
Green-and-flagged and unattributed PK–5 figures.

---

## 1. `candidate_ruling` captures what the writer was looking at

`mh2/schema_seq.sql`'s `candidate_ruling` landed with `source_key`,
`standard_id`, `ruling`, `ruled_by`, `ruled_at` — exactly as the predecessor
brief specified, and the specification was incomplete. Add
`node_id_seen`, `node_text_seen` and `ladder_file_seen`, nullable, mirroring
`tag_review`'s naming and its schema comment's stated reason: captured at
write time for display and for locating the node in a document, none of them
a key, none trusted after a rebuild.

**Correct the rationale, because the predecessor brief's follow-up discussion
overstated it.** It was claimed that the deferred staleness reconciliation is
currently *unbuildable* without these columns. That is wrong, and the
reasoning matters for what you build:

`source_key` is `stem_id` + normalized node text (`mh2/ingest_ladders.py:423`,
`mh2/schema.sql:79`), so editing a node's wording produces a *new*
`source_key` and retires the old one. Staleness is therefore fully
**detectable** by membership against `node_lookup.build_source_key_lookup`,
with no new columns at all — which is exactly how
`reconcile_tag_reviews` (`mh2/reconcile_review.py:93-101`) already works.

What is missing is not detection but **actionable reporting.** Compare the
report `reconcile_review.py` writes for `tag_review`
(`mh2/reconcile_review.py:167-172`): each stale row prints its
`ladder_file_seen` and `node_text_seen`, so a human can find the affected
node in a Word document. A stale `candidate_ruling` row can currently print
only a `standard_id` and an opaque `source_key`, which tells a writer that
something they ruled on has changed while giving them no way to find it.

**The asymmetry that makes this worth fixing now.** An `accepted` ruling
already preserves its anchor, because `write_ruling()` raises a
`tag_proposal` carrying `node_text_seen` and `ladder_file`
(`app/db.py:373-379`). A `rejected` ruling writes nothing but the ruling
row — and rejections are precisely the rows that persist silently and
suppress a suggestion from ever resurfacing. The half of the table with no
anchor is the half whose whole purpose is suppression.

**Implementation is two arguments.** `write_ruling()` already holds
`info` from `node_lookup.build_node_lookup(con)` (`app/db.py:366`) and the
`node_id` it was passed; the `accepted` branch already forwards both fields
to `create_tag_proposal`. Extend `review_store.set_candidate_ruling` to
accept and store them, and pass `info.node_text`, `info.source_file` and
`node_id` on both branches. No new query, no new lookup.

`candidate_ruling` currently has **0 rows** — verified: the table does not
yet exist in `data/build/mh2_seq.db` at all, since `ensure_schema` creates
it on first use and no writer has exercised the new path. So this is a
schema line today versus a migration against live writer judgment later.

**Do not build the reconciliation itself.** That deferral keeps its trigger
in `DEFERRED.md` §8. This item only ensures the data it will need exists by
then.

**Also amend that deferral's wording**, which describes the failure mode as
a ruling being "stranded silently rather than reported." Given the paragraph
above, detection was never the gap. Restate it as: stale rulings are
detectable but not yet detected, and until item 1 lands they could not be
reported in a form a writer can act on.

---

## 2. A test that actually exercises the rerouted read paths

Acceptance criterion 8 of the predecessor brief — the Streamlit sidebar's
ruled/total tally unchanged across a rebuild — **passed trivially and
verifies nothing.** `node_standards` holds 0 rows with `source='human'`, so
the comparison was 0 against 0. The regression §6's read-path rerouting
existed to prevent is still unguarded by any test.

Add to `tests/test_review_ruling_durability.py` a test that writes and then
observes, against a small fixture `mh2.db` carrying at least one `candidates`
row that survives `_ruled_totals_by_stem`'s filter (`auto_surface = 1`,
`state IN ('CA','TX','FL')`, and no matching `node_standards_parsed` row —
see `app/db.py:74-92`, and read that filter before building the fixture
rather than after):

1. `_ruled_totals_by_stem` reports `ruled = 0` for the stem.
2. `write_ruling(..., "rejected", ...)` on that candidate.
3. `_ruled_totals_by_stem` now reports `ruled = 1`, `total` unchanged.
4. `suggested_pool` reports `ruled_status == "rejected"` for that row.
5. `known_reviewers()` includes the reviewer name.
6. The tally is identical after `reconcile_review` runs — the durability
   claim, now made against a non-zero number.

Assert the literal counts the fixture produces. Do not assert a total
derived from real data.

While you are there: confirm a `rejected` ruling raises **no**
`tag_proposal`, and that a second `accepted` on the same candidate still
raises only one (`open_proposal_exists`, `mh2/review_store.py:223-232` — a
guard the previous session added beyond its brief, and a good one).

---

## 3. `CATEGORY_TO_STEMS_CSV` comes out of the rebuild pre-flight

The previous session reported this correctly and complied with the brief
anyway, which was the right call on its part and the wrong outcome. The
brief was in error: `mh2_category_to_stems.csv` is read only by `app/db.py`,
live, and `config.py`'s own comment says the app reads it live rather than
hardcoding the mapping. `rebuild.py` never opens it.

A pre-flight that fails on a file the pipeline never reads couples the
rebuild to the Streamlit app's inputs: a missing app-only file blocks a
rebuild that would otherwise succeed, on a host where the two may not even
be deployed together. Remove it from the pre-flight set, and remove the
comment justifying its inclusion.

It does still move to SharePoint under R-H2 — as the app's input, not the
pipeline's. If you want a check, put it where the app reads it, so the
failure names the tool that actually needs the file. A check in neither
place is also acceptable and is the smaller change; say which you chose.

Leave the other ten pre-flight entries alone.

---

## 4. Housekeeping

- **Correct the stale framing in the predecessor brief.** Its §4 argues
  from "twelve minutes into a rebuild is the wrong place to learn a file is
  absent." A real full rebuild is ~14 seconds. Pre-flight still earns its
  place for the coming fetch step, where a missing file means a failed
  download rather than a typo — rewrite the sentence to say that, since the
  file stays in `docs/` as the record of why this work happened.
- **Do not commit `data/build/mh2_seq.db` with this work.** It shows
  modified, but `candidate_ruling` is absent from it — that modification
  belongs to the in-flight `standard_status` work
  (`review_api.py`, `scripts/render_static.py`, two test files), not to the
  hardening session. It is a tracked binary (§7 FLAG A), so committing it
  here bundles two unrelated changes into one opaque diff.

---

## 5. Standing norms

Unchanged from the predecessor brief §8, and the predecessor session honored
them — five reported discrepancies, two of which were errors in the brief
itself and one of which (item 1 above) was an error in the follow-up
discussion of it. Keep doing that:

- Report discrepancies; do not search for a predicate that fits a stated
  number.
- Acceptance criteria carry the literal predicate that produced the number,
  or carry no number at all.
- Every deferral needs a trigger, or it is abandonment.
- Do not touch `mh2/coverage.py`, `docs/HOSTING.md`'s auth section, the
  in-flight `standard_status` work, or anything under `app/` other than
  `app/db.py`.
