# Sequencer v1 prototype: start here

This is branch `seq/v1-prototype`, built unattended on 2026-09-30.
**Read `docs/seq_v1_README.md` first.** It covers what the tool does, how to
run it, what is provisional or unverified, and the next steps to pilot.

Nothing has been pushed or merged, and `main` is untouched. The branch is in
the session clone at:

```
~/Library/Application Support/Claude/local-agent-mode-sessions/77ae91bf-cef1-402f-b540-783f45078aff/634284a1-0b67-4608-a522-4271f8d7fe2d/84eb3f33/outputs/wt/v1
```

The branch already contains `seq/grade_type`, `seq/analysis` and
`seq/prototype`, so you do not need to merge those three separately. That
replaces the merge loop in `docs/seq_START_HERE_2026-09-30.md` §1.

## 1. Bring the branch into your repo and test it (about 10 minutes)

```sh
cd ~/mh2_macro
git status                                   # should be clean
git fetch "$HOME/Library/Application Support/Claude/local-agent-mode-sessions/77ae91bf-cef1-402f-b540-783f45078aff/634284a1-0b67-4608-a522-4271f8d7fe2d/84eb3f33/outputs/wt/v1" \
  seq/v1-prototype:seq/v1-prototype
git switch seq/v1-prototype

.venv/bin/python scripts/rebuild.py          # adds node_grade_kind (from seq/grade_type)
.venv/bin/python -m pytest -q                # whole suite; watch tests/test_seq_api_fastapi.py (first real FastAPI run)
node tests/js/seq_app_test.js                # optional, needs node
node tests/js/seq_e2e_test.js                # optional, needs node: real HttpApi -> HTTP -> service
```

After the rebuild, `SELECT kind, COUNT(*) FROM node_grade_kind GROUP BY 1`
should give core 155, span 213 and unconfirmed 143.

## 2. Try it

- **Offline:** double-click `docs/seq_demo/seq_demo_g2.html` (or `_g4`, `_g6`).
- **Server, on a copy of your data:**

  ```sh
  mkdir -p ~/mh2_seq_trial/build
  cp data/build/mh2.db data/build/mh2_seq.db ~/mh2_seq_trial/build/
  MH2_DATA_DIR=~/mh2_seq_trial .venv/bin/uvicorn seq_api:app --reload --port 8001
  ```

  Then open http://127.0.0.1:8001/seq/. The click-through checklist is in the README, §7 step 2.

## 3. Merge, only when you are happy

```sh
git switch main
git merge --no-ff seq/v1-prototype
.venv/bin/python -m pytest -q
```

**Do not push `main` until you mean to deploy.** A push to `main` triggers
the Fly deploy workflow (`docs/HOSTING.md` §6).

The sequencer does not change the deployed audit:

- `seq_api` is a separate app;
- `review_api.py`, `config.py`, `Dockerfile`, `fly.toml` and `entrypoint.sh` are byte-identical to `main`.

A push would still ship everything on the branch, including the
`seq/grade_type` loader and schema changes, and `entrypoint.sh` runs
`rebuild.py` on deploy. To keep a copy off your machine, push the branch
instead: `git push origin seq/v1-prototype`. That does not deploy.
