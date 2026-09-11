# Hosts the gap auditor (review_api.py) for the writer pilot. See
# docs/HOSTING.md for the full deploy runbook -- this file alone is not
# meant to be read in isolation.
#
# data/source (workbooks, ladders, standards CSVs) is committed to git and
# baked into the image below -- see the repo's .gitignore, which tracks
# data/source deliberately. data/build is where the regenerable mh2.db and
# the durable, writer-authored mh2_seq.db both live; in production that
# directory is a mounted volume (see fly.toml) so mh2_seq.db survives
# restarts and redeploys. entrypoint.sh rebuilds mh2.db from data/source on
# every start (fast, no network calls -- reranks load from the committed
# cache.jsonl) and never touches mh2_seq.db beyond ensuring its schema
# exists, which schema_seq.sql's CREATE TABLE IF NOT EXISTS makes safe to
# repeat.

FROM python:3.12-slim

WORKDIR /app

# build-essential: some transitive deps (e.g. pandas) may need it on slim
# base images without a matching prebuilt wheel. Harmless if unused.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
