# Hosting the gap auditor — runbook

Written 2026-09-11, for the writer pilot while John is on PTO. Answers rev
11 §2.3 / DEFERRED §3.4's open "hosting, HTTPS, auth" item: HTTP Basic with
per-writer env-var credentials (code), Fly.io free/hobby tier (host),
Fly's own HTTPS (transport). Nothing here touches `mh2/coverage.py`, the
rollup, `app/`, or any other do-not-touch item from rev 11 §4.

**Update, same day:** steps 1–2 below are confirmed — 251 passed locally,
and the auth gate returns 401 with no credentials / 200 with correct ones.
Steps 3 onward originally assumed a local `flyctl` install, which turned
out to be blocked by John's corporate-managed Mac (the install script gets
intercepted, likely the same policy that made IT hesitant about Homebrew).
Steps 3+ now run through GitHub Actions instead, using Fly's remote
builder — no `flyctl` or Docker on any local machine at all. The rest of
this file (§0–2) is unchanged and still applies.

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

## 3. Get a Fly API token (browser only, no CLI)

Log into `fly.io/dashboard`. Find your account's **access tokens** page
(under your account or organization settings — Fly's exact wording has
moved around over the years; look for "Tokens"). Create a new personal
access token with full account access (not one scoped to a single app —
the setup workflow below needs to *create* the app and volume, so an
app-scoped token doesn't yet have anything to scope to). Copy it
immediately; Fly won't show it again.

## 4. Add two GitHub repo secrets

On GitHub: this repo → **Settings → Secrets and variables → Actions →
New repository secret**. Add two:

| Name | Value |
|---|---|
| `FLY_API_TOKEN` | the token from step 3 |
| `MH2_AUTH_USERS` | the long `debbie:...,katie:...,...` line from `credentials.local.txt` |

Both are secrets (masked in logs), never committed to the repo.

## 5. Run the one-time setup workflow

GitHub → **Actions** tab → **Fly one-time setup** → **Run workflow**.

This runs `.github/workflows/fly-setup.yml`: creates the Fly app
(`greatminds-mh2-gap-auditor`, matching `fly.toml`), creates the
`mh2_data` volume that `mh2_seq.db` lives on, and sets `MH2_AUTH_USERS` as
a Fly secret on the app. **Watch the log for the volume-creation step
before it runs** — it prints any existing volumes first specifically so
you can catch a mistaken second run before it creates a duplicate. Run
this workflow exactly once. If a step fails partway, check the Fly
dashboard for what already exists before triggering it again, rather than
re-running blind.

To change writer credentials later (add/remove/rotate), use the separate
**"Fly update writer credentials"** workflow instead
(`.github/workflows/fly-secrets.yml`) — safe to re-run any time. Don't
re-run this setup workflow just to update a password; its volume-creation
step isn't idempotent.

## 6. Deploy

Either push this branch to `main` (deploy triggers automatically), or
GitHub → **Actions** → **Fly deploy** → **Run workflow** to trigger it by
hand without waiting on a push.

This runs `.github/workflows/fly-deploy.yml`, which installs `flyctl` on
GitHub's runner (not your machine) and runs `flyctl deploy --remote-only`
— the Docker image is built on Fly's own infrastructure, so nothing about
this step touches your laptop or its network restrictions. Watch the
Action's log the same way you'd watch a local `fly deploy`:
`entrypoint.sh` runs `rebuild.py` (rebuilds `mh2.db` from the committed
`data/source`) then `render_static.py`, then starts `uvicorn`. A failure
here names the pipeline step, same as it would locally.

## 7. Verify the live deployment

Get the URL from the Fly dashboard (your app's page shows it, something
like `https://greatminds-mh2-gap-auditor.fly.dev`). From here, plain
`curl` against your *own* app is a normal HTTPS request to a domain you
control — unrelated to the install-script block from step 3's old
attempt, so this should work fine from your Mac:

```bash
curl -i https://greatminds-mh2-gap-auditor.fly.dev/          # expect 401 (no creds)
curl -i -u debbie:<her password> https://greatminds-mh2-gap-auditor.fly.dev/          # expect 200, HTML
curl -i -u debbie:<her password> https://greatminds-mh2-gap-auditor.fly.dev/api/audit  # expect 200, JSON, 2,987ish rows
```

Then the persistence check that actually matters — write something,
restart the machine, confirm it's still there. Restart from the Fly
dashboard (your app → Machines → the one machine → Restart), no CLI
needed:

```bash
curl -s -u debbie:<her password> -X POST \
  https://greatminds-mh2-gap-auditor.fly.dev/api/standards/K.CC.A.1/review \
  -H 'content-type: application/json' \
  -d '{"outcome":"confirmed","reviewed_by":"smoke-test"}'
```

Restart the machine from the dashboard, wait for it to come back (the
dashboard shows machine state), then:

```bash
curl -s -u debbie:<her password> https://greatminds-mh2-gap-auditor.fly.dev/api/standards/K.CC.A.1 \
  | python3 -m json.tool | grep -A3 standard_review
```

If `standard_review` comes back non-null after the restart, the volume is
correctly wired and this pilot survives redeploys. Then clear the smoke
test so it doesn't sit in `mh2_seq.db` alongside real writer judgments:

```bash
curl -s -u debbie:<her password> -X DELETE \
  https://greatminds-mh2-gap-auditor.fly.dev/api/standards/K.CC.A.1/review
```

## 8. Hand off to writers

Give each writer, individually, **their own line** from
`credentials.local.txt` plus the URL from the Fly dashboard. Their browser
will show a native login prompt on first visit — no separate signup, no
account system. Point them at the tool exactly as rev 11 §2.1 describes:
start from the 82 computed-Green-and-flagged rows, and see whether the
grouped node picker (§1.2) is enough to propose a tag without opening the
ladder document.

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

The Fly dashboard's **Logs** view for the app shows the running
container's stdout, including `rebuild.py`'s own step-by-step output — the
same log a local run would produce, no CLI needed to read it. The volume
(`mh2_data`) holds the only irreplaceable state; everything else (`mh2.db`,
`coverage.html`) regenerates from what's already committed to git. Worst
case, delete the app from the dashboard and re-run the **Fly one-time
setup** and **Fly deploy** workflows — that loses nothing except writer
judgments already in `mh2_seq.db` on that volume, which is exactly why
step 7's restart-and-check matters before handing out logins.

If anyone on the team ever wants `flyctl` itself for something this
runbook doesn't cover, it doesn't have to be John's laptop — a GitHub
Codespace (github.com → this repo → **Code → Codespaces**) is a normal
Linux environment in the browser with unrestricted internet, so the
original curl-based install works there without going near corporate
network policy at all.
