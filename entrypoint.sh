#!/usr/bin/env bash
# Container entrypoint. Rebuilds mh2.db fresh from data/source every start
# (deterministic, local, no API calls -- see rebuild.py), renders the
# static coverage page (git-ignored, must exist before review_api.py's "/"
# will serve anything), then hands off to uvicorn.
#
# mh2_seq.db (durable writer state) lives in the same data/build directory,
# which is a mounted volume in production (see fly.toml) -- rebuild.py
# creates it if absent and never unlinks it, so re-running this on every
# restart is safe by design, not by luck.
set -euo pipefail
cd /app

echo "=== rebuilding mh2.db from data/source ==="
python scripts/rebuild.py --skip-review-reconcile

echo "=== rendering coverage.html ==="
python scripts/render_static.py

echo "=== starting uvicorn on port ${PORT:-8080} ==="
exec uvicorn review_api:app --host 0.0.0.0 --port "${PORT:-8080}"
