# Sequencer v1, Implementer W notes

## What was built

| File | Role |
|---|---|
| `mh2/schema_placement.sql` | Data model §3 DDL plus `slot_update`/`slot_move`/`slot_merge` event actions (contract §3.4). Applied only by `seq_store.ensure_schema`. |
| `mh2/seq_store.py` | All SQL on the placement tables: revisions (409), append-only `placement_event`, sparse order keys (1024, local renumber), soft delete, co-place / ungroup / merge / move, reattach / acknowledge, saved views. |
| `mh2/seq_reconcile.py` | `derive_status` (pure) and the report CLI (`python -m mh2.seq_reconcile`; writes a text report, no rows, both DBs opened `mode=ro`). |
| `mh2/seq_guardrail.py` | §5.8 arithmetic (pure). |
| `mh2/seq_auth.py` | `configured_writers` (byte copy of `review_api._configured_writers`), `check_basic`, `resolve_writer`. |
| `mh2/seq_service.py` | One function per route; builds `SequenceView` from R's facts + the store; attaches the current view to every `StaleRevision`. |
| `seq_api.py` | FastAPI app + router (33 routes, each one `return _run(seq_service.<fn>, CTX, ...)`), app-level auth gate, explicit static routes. |
| `tests/test_seq_{store,reconcile,guardrail,auth,api_routes,service}.py` | Plain `test_*` functions with `__main__` runners. |

## Running

```
MH2_DATA_DIR=... uvicorn seq_api:app --reload --port 8001     # open /seq/
MH2_AUTH_USERS="a:x,b:y" uvicorn seq_api:app ...              # turn auth on
python -m mh2.seq_reconcile                                   # report to data/reports/placement_reconcile.txt
MH2_DATA_DIR=... python3 tests/test_seq_store.py              # likewise the other five test files
```

With auth off the writer is the `X-MH2-User` header if valid, else `local`.
`seq_static/` comes from F; until it exists `/seq/` returns 404.
The placement tables are created on first use of each service call (`ensure_schema` has a fast path when they exist), so no change to `scripts/rebuild.py` is needed.

## Decisions where the contract is silent (please confirm)

1. **Archived sequences** accept no special handling: writes against an archived sequence are not refused (no error code exists for it). Unarchiving a grade that already has an active sequence gives 409 `sequence_exists`.
2. **Sequence/module/attribute PATCH that changes nothing** is a no-op: no rev bump, no event (keeps "one event per changed field"). A PATCH with no fields at all is 422 `invalid`.
3. **`place` with `slot_id`** uses that slot's module and ignores a mismatching `module_id` (lenient, avoids breaking F on a stale module id). `after_slot_id` must be in `module_id` (else 422 `invalid`).
4. **`place_group` where every key is already placed** writes nothing (no rev bump, no event) and returns `slot_id: null`, `placement_ids: []`, `skipped: [...]`. Confirmation is required only for the nodes that would actually be placed.
5. **`co_place` into the slot the placement is already in** is 422 `invalid`. `merge_slot` of a slot into itself likewise.
6. **`reattach` to the placement's own current key** is refused as `reattach_target_placed` (it is active in the sequence).
7. **A NULL `*_seen` value is never a relabel** (nothing was captured to compare).
8. **`expected_rev` must be an integer**, else 422 `invalid`.
9. **Validation before revision:** empty title / bad direction / bad attribute values raise 422 before the revision check in a few functions (`create_module`, `set_attributes`, views). Existence and revision order is kept for everything the contract lists.
10. **`update_sequence`** maps `archived: null` to "not supplied". The action is `sequence_archive` only when archiving, otherwise `sequence_update`.
11. **Report CLI stem ratio** is per `stem_id_seen` over the active placements (W may not query mh2.db directly, so it cannot tell "stem has zero nodes" from "all its placements orphaned"). The "check ingest before acting" note is printed when every placement of that stem is orphaned.
12. **`grades()`** takes `kind_source` from `mh2.grade_kind.kind_source` (R's contract §3.1), since `seq_read.list_grades` does not return it.
13. **Guardrail JSON fixture:** the contract names no shared file, so the G0-G5 cases live in `tests/test_seq_guardrail.py` only.

## Deviations from the contract

None intended. `seq_store.sequence_id_of(con, kind, id)` was added (not in §3.5) so the service can find the owning sequence of a module/slot/placement without SQL; it is additive.

## Not verifiable in the sandbox

- Everything FastAPI: app import, pydantic models, `Depends` wiring, 401 behaviour, static `FileResponse`, `/seq` redirect. Verified only by `py_compile` and the ast route test. First real check is `uvicorn seq_api:app` on Python 3.14 with current FastAPI/pydantic v2.
- R's real `mh2.seq_read` / `mh2.grade_kind`. `tests/test_seq_service.py` and `tests/test_seq_reconcile.py` inject a small fake (only when R's modules are not importable) and print `[read modules: fake]`. With R present they run against the real modules on the same synthetic `mh2.db` (built from `config.SCHEMA`). One assertion (`placed_elsewhere` in the slice) already handles R's nested slice shape but has not been run against it.
- Python 3.14 (tests ran on 3.10).
- Multi-process write contention (two uvicorn workers). The store uses `BEGIN IMMEDIATE` and SQLite's default 5 s busy timeout; only a two-connection stale-revision test was run.
