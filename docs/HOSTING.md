# Hosting the gap auditor — runbook

Written 2026-09-11, for the writer pilot while John is on PTO. Answers rev
11 §2.3 / DEFERRED §3.4's open "hosting, HTTPS, auth" item: HTTP Basic with
per-writer env-var credentials (code), Fly.io free/hobby tier (host),
Fly's own HTTPS (transport). Nothing here touches `mh2/coverage.py`, the
rollup, `app/`, or any other do-not-touch item from rev 11 §4.

This file assumes commands run on a real machine with normal internet
access (your laptop, or a Claude Code session with shell access) — the
Cowork session that wrote this file has none, so none of the steps below
have been executed or verified by running them. Steps 1–2 exist specifically
to catch anything that doesn't hold up before you're unreachable for two
weeks.

## 0. One thing to decide first

`git status` shows an **uncommitted** rewrite of `scripts/render_static.py`
(a visual redesign — color dots instead of a color column, restyled chips
and shadows, plus a real behavior change: the grade filter now shows all
grades by default instead of hiding GEO/A2/HS). Docker's build copies your
working tree, not `git HEAD`, so **this uncommitted version is what ships**
unless you `git stash` it first. If that's not what you want live for the
pilot, stash or revert before step 3.

## 1. Verify locally, before anything else

```bash
cd ~/mh2_macro
source .venv/bin/activate      # existing macOS venv already has deps
python -m pytest -q
```

Expect **249 passed** (rev 11's 243 + 6 new in `tests/test_review_api_auth.py`,
added today). If `httpx` is missing (FastAPI's `TestClient` needs it and it
isn't in `requirements.txt`), `pip install httpx` and re-run. Do not
proceed to deployment on a red test run.

## 2. Confirm the auth gate works before it's the only thing between
   the internet and your data

```bash
python scripts/render_static.py
MH2_AUTH_USERS="test:testpass" uvicorn review_api:app --port 8080 &
curl -i http://127.0.0.1:8080/api/audit                     # expect 401
curl -i -u test:testpass http://127.0.0.1:8080/api/audit     # expect 200
kill %1
```

If either check fails, stop — do not deploy. `MH2_AUTH_USERS` unset (the
default) leaves every route open, exactly as it always has; this is only
a no-op-by-default gate, so it's easy to convince yourself it's wired up
when it silently isn't.

## 3. Install flyctl and log in (one-time)

```bash
curl -L https://fly.io/install.sh | sh
fly auth login       # opens a browser; sign up if you don't have an account
```

## 4. Launch, using the fly.toml already in the repo

```bash
cd ~/mh2_macro
fly launch --no-deploy
```

Say **yes** to reusing the existing `fly.toml`/`Dockerfile` when asked.
Pick an app name (the placeholder `mh2-gap-auditor` may be taken) and a
region close to your writers. flyctl will rewrite `fly.toml`'s `app` /
`primary_region` lines to match — that's expected and fine.

## 5. Create the persistent volume

`mh2_seq.db` (every writer's reviews, overrides, and proposals) lives in
`data/build/`, which `fly.toml` mounts to a volume — without this step
every redeploy silently wipes all writer judgments.

```bash
fly volumes create mh2_data --size 1 --region <the region from step 4>
```

## 6. Set the writer credentials

The generated passwords are in `credentials.local.txt` (repo root,
git-ignored — never commit it). Set them as one secret:

```bash
fly secrets set MH2_AUTH_USERS="$(grep -A1 'live in one Fly.io secret' credentials.local.txt | tail -1)"
```

(Or just copy the long `debbie:...,katie:...,...` line out of that file by
hand into the command — either way, confirm with `fly secrets list`, which
shows names only, never values.)

## 7. Deploy

```bash
fly deploy
```

Watch the build log. `entrypoint.sh` runs `rebuild.py` (rebuilds `mh2.db`
from the committed `data/source` — takes a bit, it's parsing 17 `.docx`
ladders and two Excel workbooks) then `render_static.py`, then starts
`uvicorn`. If `rebuild.py` fails, the deploy log will say which pipeline
step and why — same failure mode as running it locally.

## 8. Verify the live deployment

```bash
fly status                        # confirms the machine is running
curl -i https://<your-app>.fly.dev/          # expect 401 (no creds)
curl -i -u debbie:<her password> https://<your-app>.fly.dev/          # expect 200, HTML
curl -i -u debbie:<her password> https://<your-app>.fly.dev/api/audit  # expect 200, JSON, 2,987ish rows
```

Then the persistence check that actually matters — write something,
restart the machine, confirm it's still there:

```bash
curl -s -u debbie:<her password> -X POST \
  https://<your-app>.fly.dev/api/standards/K.CC.A.1/review \
  -H 'content-type: application/json' \
  -d '{"outcome":"confirmed","reviewed_by":"smoke-test"}'

fly machine restart $(fly machine list --json | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")

curl -s -u debbie:<her password> https://<your-app>.fly.dev/api/standards/K.CC.A.1 \
  | python3 -m json.tool | grep -A3 standard_review
```

If `standard_review` comes back non-null after the restart, the volume is
correctly wired and this pilot survives redeploys. Then clear the smoke
test so it doesn't sit in `mh2_seq.db` alongside real writer judgments:

```bash
curl -s -u debbie:<her password> -X DELETE \
  https://<your-app>.fly.dev/api/standards/K.CC.A.1/review
```

## 9. Hand off to writers

Give each writer, individually, **their own line** from
`credentials.local.txt` plus the URL from `fly status`. Their browser will
show a native login prompt on first visit — no separate signup, no account
system. Point them at the tool exactly as rev 11 §2.1 describes: start from
the 82 computed-Green-and-flagged rows, and see whether the grouped node
picker (§1.2) is enough to propose a tag without opening the ladder
document.

## What this deliberately does not do

- No React UI, no Session E edit-pass surface (§2.2) — unbuilt, per rev 11.
- No export/worklist beyond `GET /api/worklist`, which has no consuming UI
  yet — §3.1's open question, unchanged.
- `run_reranks.py` is still blocked on API credits — unrelated to hosting,
  unaffected by any of this.
- Writer identity is now a real login (this session's whole point), but
  `reviewed_by`/`proposed_by` fields are still client-supplied text in the
  request body, not automatically bound to the authenticated username.
  Worth tightening in a later session; flagged here rather than silently
  left inconsistent.

## If something breaks while John is out

`fly logs` shows the running container's stdout, including `rebuild.py`'s
own step-by-step output — the same log a local run would produce. The
volume (`mh2_data`) holds the only irreplaceable state; everything else
(`mh2.db`, `coverage.html`) regenerates from what's already committed to
git. Worst case, `fly apps destroy` and redo steps 4–7 loses nothing except
writer judgments already in `mh2_seq.db` on that volume — which is exactly
why step 8's restart-and-check matters before handing out logins.
