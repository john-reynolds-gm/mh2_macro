# MH2 Stems & Leaves

Tooling for the PK–8 + Algebra 1 mathematics curriculum framework. Pulls the
stem/leaf workbook, the standards-tagging workbook, the Learning Ladder docs,
and the state standards files into one database, then reports where they
disagree.

## Setup

Use a virtual environment. Not ceremony — `requirements-ml.txt` pulls in
PyTorch, which pins numpy and transformers versions and will happily conflict
with an existing model environment.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`.venv/` is already gitignored. You'll need to activate it in each new
terminal; if the `python` you're running isn't the one in `.venv`, imports
will fail in confusing ways. `which python` (Windows: `where python`) is the
quick check.

### Two requirements files

| file | who needs it |
|---|---|
| `requirements.txt` | everyone — pandas, openpyxl, python-docx, streamlit |
| `requirements-ml.txt` | only whoever generates alignment candidates |

The split is deliberate. Most people just rebuild the database and open the
UI; making them install a 2GB deep-learning stack to do that is a good way to
lose them at step one.

For the embedding step, the cleaner option is to skip `requirements-ml.txt`
entirely: run the fine-tuned model in the environment it already lives in,
write its output to `data/source/predictions/`, and let this project read that
file. Then nothing here ever depends on torch.

Copy source files into place (see **Folder layout** below), then:

```bash
python scripts/rebuild.py
```

Everything runs from the repo root. That matters — the `mh2` package is
imported by name, so `python scripts/rebuild.py` works and
`cd scripts && python rebuild.py` does not.

## Folder layout

```
mh2-ladders/
├── config.py              every path in the project, in one place
├── requirements.txt
│
├── data/
│   ├── source/            READ-ONLY originals. Scripts never write here.
│   │   ├── ladders/         MH2_*.docx
│   │   ├── workbooks/       H2_Stem_and_Leaf...xlsm, Standards_for_Stems...xlsx
│   │   └── standards/       all_states.csv, scored_alignments.csv, ...
│   ├── build/             GENERATED. Delete it any time; rebuild.py restores it.
│   │   └── mh2.db
│   └── reports/           output for humans: drift CSVs, unparsed codes
│
├── mh2/                   the library
│   ├── schema.sql           table definitions
│   ├── normalize.py         standard-code parsing
│   ├── load_standards.py    spreadsheets + CSVs -> database
│   ├── ingest_ladders.py    ladder .docx -> database
│   └── reconcile.py         workbook vs ladder drift
│
├── scripts/               things you run
│   ├── rebuild.py           wipe and rebuild everything
│   └── ingest_new.py        add/refresh ladders, with dry-run preview
│
├── app/                   Streamlit UI (not built yet)
├── notebooks/             scratch exploration
└── tests/
```

The one rule worth internalizing: **`data/source` is read-only, `data/build`
is disposable** — except `mh2_seq.db`, the authored review database, which is
tracked and cannot be regenerated. If anything looks wrong, delete
`data/build` and rebuild. You can never corrupt the originals, so there's no
state to be afraid of.

## Adding a new ladder

1. Drop the `.docx` into `data/source/ladders/`.
2. If the ladder covers a stem the tools haven't seen, add one line to
   `LADDER_TO_WORKBOOK_STEMS` in `mh2/reconcile.py`. A ladder can map to
   several workbook stems — the Comparing and Ordering ladder covers the
   workbook's separate `COM` and `ORD` stems.
3. Preview, then apply:

```bash
python scripts/ingest_new.py --file MyLadder.docx
python scripts/ingest_new.py --file MyLadder.docx --apply
```

The preview reports four things:

| | meaning |
|---|---|
| **new** | node text not seen before; a node record gets created |
| **matched** | node text unchanged; keeps its ID and all its alignments |
| **missing** | in the database, absent from this file — reworded or cut |
| **collision** | two rows in this ladder share node text; they merge into one node |

**Nodes are matched by their text.** A node whose wording was edited shows as
one `missing` and one `new`. That is deliberate: quietly carrying approved
alignments across a reworded node would put alignments in the database that
nobody actually approved. Pairing them back up is a manual call today; automatic
rename suggestions are a planned addition.

## Reports

After a rebuild, `data/reports/` contains:

* `tier1.csv` — standards tagged in the workbook but not the ladder, and
  vice versa. Exact set comparison, no text matching.
* `tier2.csv` — standards whose stem changed during drafting while the
  workbook still points at the old one. Highest-value findings.
* `tier3.csv` — concept-to-node matching. Fuzzy and advisory; the placeholder
  similarity function should be swapped for the fine-tuned model.
* `unparsed_codes.txt` — cell lines no standard code could be read from.
  Mostly legitimate prose sitting in a code column.

## Tests

```bash
python tests/test_normalize.py
```

## Hosting the gap auditor (review_api.py)

See `docs/HOSTING.md` for the deploy runbook (Fly.io + HTTP Basic auth) and
`docs/handoff_rev12.md` for what's been prepared vs. actually deployed.

## Not built yet

Streamlit UI, automated alignment suggestions, leaf detection, coverage
dashboard, drift triage. See the project plan.
