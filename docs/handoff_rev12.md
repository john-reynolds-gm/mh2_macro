# Handoff rev 12 — auth + hosting prepared, not yet deployed; written ahead of a 2-week gap

Self-contained. Paste as the first message of a new chat.

Supersedes rev 11 as the current state document. Rev 9's rulings R1–R6, rev
10 §5's do-not-touch list, and rev 11 §4's additions all still stand and are
not relitigated here.

**Provenance warning, read first.** This session ran in Cowork with direct
read/write access to the actual repo (not a snapshot upload — the drift risk
rev 9/rev 11 both flag from snapshot-based sessions does not apply here),
but with **no outbound network access at all** — pip, GitHub, Docker Hub,
and Fly.io were all unreachable from its shell. Every figure that would
normally come from running `pytest` or a live deploy instead comes from
reading the code and reasoning about it. Nothing in §1 has been verified by
execution. `docs/HOSTING.md` §1–2 exist specifically to close that gap
before anything goes live.

---

## 0. Why this session happened

John is on PTO starting today, back in two weeks, with no one covering the
gap auditor while he's out. Rev 11 §2.3 and DEFERRED §3.4 both already
named hosting/auth as the next real item; this session's job was to get as
close to "writers have a URL and a login" as a network-isolated session
could, and to leave an exact, low-judgment runbook for the one machine that
does have internet access to finish it.

## 1. What changed this session

### 1.1 HTTP Basic auth added to `review_api.py`

Per-writer credentials via `MH2_AUTH_USERS` (`user:pass,user:pass,...`),
checked with `secrets.compare_digest` (stdlib, not hand-rolled crypto).
**Unset means auth is a complete no-op** — every existing test, and any
local `uvicorn --reload` run, behaves exactly as before with zero setup.
This was a deliberate design constraint, not laziness: rev 11's tests
(`test_review_api.py`) construct `TestClient(review_api.app)` directly with
no auth headers, and breaking them to add auth would have been a much
larger, riskier diff than a env-gated no-op.

Applied via `FastAPI(dependencies=[Depends(require_writer)])` at app
construction — gates **every** route including `/`, not an
allowlist/denylist per-route decision that could silently miss one.

New: `tests/test_review_api_auth.py`, 6 tests — auth-off passthrough,
401 with no credentials, 401 wrong password, 401 unknown user, 200 correct
credentials (two different users), and specifically that `/` (the static
render, not just the JSON API) is gated too.

**Not yet run.** `docs/HOSTING.md` §1 is that verification, to be run on a
machine with the existing macOS `.venv`.

### 1.2 Containerization + Fly.io deploy config, new files

- `Dockerfile` — `python:3.12-slim`, installs `requirements.txt` (not
  `requirements-ml.txt` — confirmed `rebuild.py` never touches torch;
  reranks load from the already-committed `data/reranks/cache.jsonl`,
  "cache-only, no API calls" per its own log line).
- `entrypoint.sh` — runs `rebuild.py --skip-review-reconcile` then
  `render_static.py` then `exec uvicorn`, every container start.
- `.dockerignore` — excludes `.venv/` (macOS-built, would not run in the
  Linux container anyway), `.git/`, `notebooks/`, docs, and
  `credentials.local.txt`.
- `fly.toml` — placeholder app name/region (flyctl rewrites these on
  `fly launch`), volume mount at `/app/data/build` so `mh2_seq.db` survives
  redeploys, `min_machines_running = 1` / `auto_stop_machines = false` so
  writers don't hit a cold-start login prompt.

**Why re-run `rebuild.py` on every container start, rather than bake
`mh2.db` into the image once:** `data/source` (workbooks, ladders,
standards CSVs) is already committed to git and therefore already baked
into the image regardless; running the rebuild at start keeps `mh2.db`
honest against whatever `data/source` state actually shipped, at the cost
of a rebuild on every restart. Confirmed this is fast and local — no
network calls, no ML dependency — so the tradeoff favors correctness. If
container restarts become frequent enough for this to matter, precomputing
`mh2.db` at build time and treating it as immutable is the fallback, but
that reopens "which rebuild ran into this image" as a question that doesn't
exist today.

**Not yet run.** No `docker build`, no `fly deploy`, no live URL. Every
step from `fly launch` onward is unexecuted.

### 1.3 Per-writer credentials generated

Six random 16-character passwords (`secrets.choice`, stdlib) for `debbie`,
`katie`, `saffron`, `amanda`, `megan`, `john` — written to
`credentials.local.txt` (repo root, added to `.gitignore` this session,
confirmed not tracked). Contains both the per-person breakdown (for
distributing individually) and the combined `MH2_AUTH_USERS` value (for the
one `fly secrets set` command).

### 1.4 `docs/HOSTING.md` — the runbook

Nine steps, in order, from "verify locally" through "hand off to writers,"
each with the exact command. §0 of that file flags something found but not
acted on — see below.

## 2. Found, not fixed — needs a decision before deploy

**`scripts/render_static.py` has an uncommitted local diff.** `git status`
showed it modified, not staged, not committed. Read in full: it's a visual
redesign (color dots replacing the color column, restyled chips/shadows/
radii) plus one real behavior change — the grade filter now shows every
grade by default instead of hiding `GEO`/`A2`/`HS` (still labeled with the
dashed out-of-scope chip, just not hidden). This does not touch
`ROW_DICT_KEYS`/the row contract (DEFERRED §0, closed) or any do-not-touch
item.

**Why this matters for hosting specifically:** `docker build` copies the
working tree as it sits on disk, not `git HEAD`. Whatever is uncommitted
right now is what ships to the container. `docs/HOSTING.md` §0 names this
explicitly and tells John to `git stash` first if that's not what he wants
live. Left as a flagged decision rather than committed or reverted on his
behalf — this session has no way to know whether that redesign is
finished, half-finished, or someone else's in-progress work.

## 3. Next items, in order

1. **Run `docs/HOSTING.md` start to finish**, on a machine with real
   network access — decide §0 first.
2. **The writer pilot itself (rev 11 §2.1)**, now actually reachable by a
   URL instead of blocked on "someone needs to be at John's laptop." Same
   questions as rev 11: which outcomes writers reach for, whether the
   grouped node picker (§1.2 there) is sufficient, whether they expect to
   leave the tool to edit the ladder.
3. **Session E — the edit pass (rev 11 §2.2).** Unchanged, still no
   consuming UI for `GET /api/worklist`.
4. Everything in rev 11 §2.4's carried small items — unchanged, untouched
   this session.

## 4. Open rulings

All of rev 11 §3 carries forward unchanged. One addition:

- **`reviewed_by`/`proposed_by` are still client-supplied strings**, not
  bound to the now-real authenticated username from HTTP Basic. The login
  is real; the attribution field it could feed is not wired to it yet.
  Small, deliberate scope cut this session — flagged rather than silently
  left inconsistent. Worth a look once the pilot is running, not before.

## 5. Do not touch

Everything in rev 11 §4, unchanged, plus:

- **`review_api.py`'s auth gate construction** — `dependencies=[Depends(...)]`
  at the `FastAPI(...)` call, not a per-route decorator. Moving individual
  routes out of this list re-opens exactly the kind of one-route miss the
  whole-app gate was built to prevent.
- **The `MH2_AUTH_USERS`-unset-means-auth-off behavior.** This is not a
  bug to "fix" by requiring the variable — it is what keeps every existing
  test and local dev workflow working with zero setup. The deployed
  container is the only place this variable should ever be set.
- **`entrypoint.sh` running `rebuild.py` on every start.** Understood and
  deliberate (§1.2 above), not an oversight to "optimize" into a one-time
  build step without re-deciding the tradeoff.
