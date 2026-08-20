"""
load_predictions.py — step 8. Path C ingestion, from the offline model run.

    python -m mh2.load_predictions --db data/build/mh2.db

**This is the critical path, not the final polish.** TX and FL route exclusively
through Path C at PK-5 (§2, §3 Path C): TX is absent from all three
deterministic sources and FL from both crosswalk files, appearing in
all_states only at grades 6-9. Until this loads, those two states generate
nothing. Measured after it loads, against the ladder hand tags, at §3's top-10
contract and in the direction that survives it (see reach_report):

    TX   98% of truth codes reachable    (was: no path at all)
    FL  100%                             (was: no path at all)
    CA    8%                             — Path 0 already covers CA at 92%

The files are JSONL, not the CSV contract §3 wrote
--------------------------------------------------
One object per ANCHOR, carrying a `predictions` array. §3's contract named
`{state}_ccss_topk.csv` with a `model_version` column; what the offline session
produced is six .jsonl files and no version field at all. The differences are
handled here rather than by rewriting the files, because data/source is
read-only.

    anchor object   anchor_id, anchor_text, anchor_grade, anchor_strand,
                    state, tier, predictions[]
    prediction      rank, ccss_id, ccss_text, bi_score, bi_rank

Four traps in these files, all measured
---------------------------------------
1. **`ccss_id` holds the TARGET, in every direction** — including the three
   files where the target is a state code and the field is named after the
   thing it is not. Direction comes from the file, never from the field name.

2. **The `state` label is unreliable; the code prefix is not.** Three spellings
   coexist -- 'CA'/'fl'/'tx' as codes, the rest as full names ('South
   Carolina'), and one 'Virginia ' with a trailing space. Two Virginia anchors
   disagree with their own label. Every state in this loader is read off the
   code prefix and the label is used only to report the mismatch.

3. **`tier` was calibrated for one direction and applied to all six.** The
   state->CCSS run spreads Strong 91 / Moderate 726 / Weak 1081 / No Match 384;
   every other file is 100% `No Match`. That is not the model finding nothing --
   ccss_to_tx_k100 reaches 99% of Texas' truth codes. Its top-1 scores just live
   in a different range (median 0.190 against state_to_ccss's 0.604). The tier
   is carried verbatim and MUST NOT be used as a cross-direction filter.

4. **`rank` and `bi_rank` are identical in all 257,238 predictions**, so only
   one is stored. A handful of anchors list the same target twice (96 across
   all six files); the best rank wins and the count is reported.

Two tables, because the state->state run has no CCSS side
---------------------------------------------------------
    model_predictions        CCSS <-> state. What Path C in §3 means.
    model_state_predictions  state -> state. §3.5.1's run, which the spec says
                             "does not exist yet" -- it does now. Ingested and
                             reported here; wiring it into the generator is the
                             §3.5.1 line item, not step 8.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

from mh2.normalize import US_STATES, normalize_code  # noqa: E402

STATE_TO_CCSS = "state_to_ccss"
CCSS_TO_STATE = "ccss_to_state"
STATE_TO_STATE = "state_to_state"

# Which way each file points. Read from this table, never inferred from the
# `ccss_id` field name -- that field holds the target in every direction and is
# actively misleading in three of these six files.
FILES = {
    "state_to_ccss_k50.jsonl": STATE_TO_CCSS,
    "ccss_to_ca_k50.jsonl": CCSS_TO_STATE,
    "ccss_to_fl_k50.jsonl": CCSS_TO_STATE,
    "ccss_to_tx_k100.jsonl": CCSS_TO_STATE,
    "big_three_to_big_three_k25.jsonl": STATE_TO_STATE,
    "state_gaps_to_big_three_k50.jsonl": STATE_TO_STATE,
}


def model_version(path: Path) -> str:
    """
    §6 makes model_version part of the primary key. The offline run did not
    stamp one.

    Rather than invent something that reads like a real version, record the
    absence: 'unstamped:<file stem>@<content hash>'. It is stable across
    reloads, distinct per file, and it changes the moment the file does -- so a
    re-run with new predictions cannot silently overwrite the old ones under a
    key that claims they are the same model.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return f"unstamped:{path.stem}@{digest}"


def state_of(code: str) -> str | None:
    """The two-letter jurisdiction, or None for a CCSS code."""
    head = code.split(".")[0].upper()
    return head if head in US_STATES else None


def read(path: Path):
    """Anchor objects, with the line number so a bad row can be found again."""
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if line:
                yield lineno, json.loads(line)


def load_file(path: Path, direction: str, stats: Counter,
              quarantine: frozenset = frozenset()) -> tuple[list, list]:
    """
    One file to (ccss_rows, state_rows).

    Deduping is per (anchor, target) keeping the best rank: a few anchors list
    the same target twice, and INSERT OR IGNORE would keep whichever arrived
    first rather than whichever the model ranked highest.

    `quarantine` holds states whose TARGET corpus failed the Path 0 gate. Rows
    predicting INTO those states are dropped and counted. Rows anchored ON them
    are kept -- a CA anchor in the state->CCSS run is scoring against the CCSS
    corpus, which is intact, and that direction is Run 2's declared scope.
    """
    version = model_version(path)
    best: dict[tuple[str, str], dict] = {}

    for lineno, obj in read(path):
        anchor = normalize_code(str(obj.get("anchor_id") or ""))
        if not anchor:
            stats[f"{path.name}: anchor unreadable"] += 1
            continue

        # The label is checked, then discarded. See trap 2 in the docstring.
        label = (obj.get("state") or "").strip().upper()
        anchor_state = state_of(anchor)
        if len(label) == 2 and anchor_state and label != anchor_state:
            stats[f"{path.name}: state label != code prefix"] += 1
        if obj.get("state") != (obj.get("state") or "").strip():
            stats[f"{path.name}: state label has stray whitespace"] += 1

        tier = obj.get("tier")
        for pred in obj.get("predictions") or []:
            target = normalize_code(str(pred.get("ccss_id") or ""))
            if not target:
                stats[f"{path.name}: target unreadable (line {lineno})"] += 1
                continue
            try:
                rank = int(pred["rank"])
                score = float(pred["bi_score"])
            except (KeyError, TypeError, ValueError):
                stats[f"{path.name}: rank or score unreadable"] += 1
                continue

            key = (anchor, target)
            prev = best.get(key)
            if prev is not None:
                stats[f"{path.name}: target repeated within an anchor"] += 1
                if prev["rank"] <= rank:
                    continue
            best[key] = {"anchor": anchor, "target": target, "rank": rank,
                         "score": score, "tier": tier}

    ccss_rows, state_rows = [], []
    for row in best.values():
        anchor, target = row["anchor"], row["target"]

        if direction == STATE_TO_CCSS:
            state_code, ccss_code = anchor, target
        elif direction == CCSS_TO_STATE:
            state_code, ccss_code = target, anchor
        else:
            a_state, t_state = state_of(anchor), state_of(target)
            if not a_state or not t_state:
                stats[f"{path.name}: state_to_state row without two states"] += 1
                continue
            # 'FL.K.NSO.1.1 -> FL.K.GR.1.2'. A state standard corresponding to
            # another standard in its OWN state is not what §3.5.1 asks for.
            if a_state == t_state:
                stats[f"{path.name}: same-state pair dropped"] += 1
                continue
            if t_state in quarantine:
                stats[f"{path.name}: QUARANTINED target state {t_state}"] += 1
                continue
            state_rows.append((a_state, anchor, t_state, target, direction,
                               row["rank"], row["score"], row["tier"], version))
            continue

        state = state_of(state_code)
        if direction == CCSS_TO_STATE and state in quarantine:
            stats[f"{path.name}: QUARANTINED target state {state}"] += 1
            continue
        if state is None or state_of(ccss_code) is not None:
            # Either side landing in the wrong column means the direction table
            # is wrong for this file, which is worth failing loudly on.
            stats[f"{path.name}: pair does not match the declared direction"] += 1
            continue
        ccss_rows.append((state, state_code, ccss_code, direction, row["rank"],
                          row["score"], row["tier"], version))

    return ccss_rows, state_rows


# ------------------------------------------------------- preflight and gate

# Declared shape of each run, from the step 8 brief. `anchors` is the count the
# brief states; `k` is the top-k the filename claims. Deviation from either is
# reported, never worked around -- a short file means silent truncation
# upstream, and quietly loading it would put a number in front of John that
# looks like a model result and is actually a missing half of the corpus.
EXPECTED = {
    "ccss_to_tx_k100.jsonl": {"anchors": 656, "k": 100},
    "ccss_to_fl_k50.jsonl": {"anchors": 656, "k": 50},
    "ccss_to_ca_k50.jsonl": {"anchors": 656, "k": 50},
    "state_to_ccss_k50.jsonl": {"anchors": None, "k": 50},
    "big_three_to_big_three_k25.jsonl": {"anchors": None, "k": 25},
    "state_gaps_to_big_three_k50.jsonl": {"anchors": 1493, "k": 50},
}


def preflight(directory: Path) -> list[dict]:
    """Row-count arithmetic per file, before anything is inserted."""
    out = []
    for name, spec in EXPECTED.items():
        path = directory / name
        if not path.exists():
            out.append({"file": name, "missing": True})
            continue
        anchors, k_counts, preds = set(), Counter(), 0
        lines = 0
        for _, obj in read(path):
            lines += 1
            anchors.add(obj.get("anchor_id"))
            n = len(obj.get("predictions") or [])
            k_counts[n] += 1
            preds += n
        out.append({
            "file": name, "missing": False, "lines": lines,
            "anchors": len(anchors), "predictions": preds,
            "k_counts": k_counts,
            "expected_anchors": spec["anchors"], "expected_k": spec["k"],
            "expected_rows": (spec["anchors"] * spec["k"]
                              if spec["anchors"] else None),
        })
    return out


# The gate's trip point. This is NOT the §5 acceptance bar -- that stays unset
# until John writes rev 5. This is a misconfiguration tripwire on a set of pairs
# whose correct answer is known by construction, so a pass is near-trivial for
# any correctly configured run and a failure means the run is not what it says
# it is.
GATE_MEDIAN_RANK = 3
GATE_TOP10_SHARE = 0.80


def path0_pairs(con) -> list[tuple[str, str]]:
    """
    The CA hand tags that are exact CCSS transforms (§3 Path 0).

    A workbook concept row carrying both a CCSS tag and a CA tag whose canon()
    forms agree: `1.MD.B.3` and `CA.1.MD.3` on the same row is a deterministic,
    verified pairing. Both sides must have standard text, which is §5's
    exclusion of the concept tags referencing codes with no text.
    """
    from eval.paths import canon

    text = {code: (txt or "").strip() for code, txt in con.execute(
        "SELECT standard_id, text FROM standards")}

    rows = defaultdict(list)
    for concept_id, code, bucket in con.execute(
            "SELECT concept_id, standard_id, bucket FROM concept_standards"):
        rows[concept_id].append((code, bucket))

    pairs = set()
    for items in rows.values():
        ccss = defaultdict(set)
        for code, bucket in items:
            if bucket == "ccssm" and text.get(code):
                ccss[canon(code)].add(code)
        for code, _ in items:
            if not code.upper().startswith("CA.") or not text.get(code):
                continue
            for anchor in ccss.get(canon(code), ()):
                pairs.add((anchor, code))
    return sorted(pairs)


def path0_gate(con, directory: Path) -> dict:
    """
    Load-time gate on Run 1's CA leg.

    Every pair from path0_pairs() is a CCSS anchor whose correct CA answer is
    known exactly. In a correctly configured CCSS->CA run those answers sit at
    or very near rank 1. If they are scattered -- or absent -- the run is
    misconfigured (wrong corpus, wrong text column, wrong direction) and
    loading it would poison every downstream number, including the diagnostics
    that are the point of step 8. So this runs BEFORE the first INSERT.
    """
    path = directory / "ccss_to_ca_k50.jsonl"
    pairs = path0_pairs(con)

    best: dict[str, dict[str, int]] = {}
    corpus = set()
    for _, obj in read(path):
        anchor = normalize_code(str(obj.get("anchor_id") or ""))
        d = best.setdefault(anchor, {})
        for pred in obj.get("predictions") or []:
            target = normalize_code(str(pred.get("ccss_id") or ""))
            corpus.add(target)
            rank = int(pred["rank"])
            if target not in d or rank < d[target]:
                d[target] = rank

    ranks, absent, no_anchor = [], [], []
    for anchor, ca_code in pairs:
        a, t = normalize_code(anchor), normalize_code(ca_code)
        if a not in best:
            no_anchor.append((anchor, ca_code))
        elif t in best[a]:
            ranks.append(best[a][t])
        else:
            absent.append((anchor, ca_code))

    ranks.sort()
    n = len(pairs)
    median = ranks[len(ranks) // 2] if len(ranks) * 2 > n else None
    top10 = sum(1 for r in ranks if r <= 10)
    passed = (median is not None and median <= GATE_MEDIAN_RANK
              and top10 >= GATE_TOP10_SHARE * n)

    return {"pairs": n, "ranks": ranks, "absent": absent,
            "no_anchor": no_anchor, "median": median, "top10": top10,
            "corpus": corpus, "passed": passed}


def print_gate(g: dict) -> None:
    n = g["pairs"]
    print("\nPath 0 gate — CA exact-transform pairs in Run 1 (ccss_to_ca_k50)")
    print(f"  pairs tested                    {n:6,d}")
    print(f"  anchor absent from the run      {len(g['no_anchor']):6,d}")
    print(f"  CA target absent from top-k     {len(g['absent']):6,d}")
    print(f"  CA target ranked                {len(g['ranks']):6,d}")
    if g["ranks"]:
        r = g["ranks"]
        for cut in (1, 3, 10, 50):
            hit = sum(1 for x in r if x <= cut)
            print(f"    rank <= {cut:<3d}                  {hit:6,d}  "
                  f"({hit / n:.1%} of pairs)")
        print(f"  median rank (over all pairs)    "
              f"{g['median'] if g['median'] is not None else 'undefined — '
                 'over half the pairs never appear':>6}")
    print(f"  distinct CA codes in the run's target corpus: "
          f"{len(g['corpus']):,d}")
    print(f"\n  GATE: {'PASS' if g['passed'] else 'FAIL'}"
          f"  (requires median rank <= {GATE_MEDIAN_RANK} and "
          f"{GATE_TOP10_SHARE:.0%} of pairs at rank <= 10)")


def print_preflight(report: list[dict]) -> None:
    print("\nRow-count arithmetic (before any insert)")
    print(f"  {'file':34s} {'anchors':>14s} {'k':>12s} {'rows':>19s}")
    print("  " + "-" * 82)
    for r in report:
        if r["missing"]:
            print(f"  {r['file']:34s}  MISSING")
            continue
        ka = ", ".join(f"{k}x{v}" for k, v in sorted(r["k_counts"].items()))
        exp_a = r["expected_anchors"]
        a = (f"{r['anchors']:,}/{exp_a:,}" if exp_a else f"{r['anchors']:,}/—")
        rows = (f"{r['predictions']:,}/{r['expected_rows']:,}"
                if r["expected_rows"] else f"{r['predictions']:,}/—")
        flag = ""
        if exp_a and r["anchors"] != exp_a:
            flag = "  << short"
        elif set(r["k_counts"]) != {r["expected_k"]}:
            flag = "  << k mismatch"
        print(f"  {r['file']:34s} {a:>14s} {ka:>12s} {rows:>19s}{flag}")


SQL_CCSS = ("INSERT OR IGNORE INTO model_predictions (state, state_code,"
            " ccss_code, direction, rank, score, tier, model_version)"
            " VALUES (?,?,?,?,?,?,?,?)")
SQL_STATE = ("INSERT OR IGNORE INTO model_state_predictions (anchor_state,"
             " anchor_code, state, state_code, direction, rank, score, tier,"
             " model_version) VALUES (?,?,?,?,?,?,?,?,?)")


def load_all(con, directory: Path,
             quarantine: frozenset = frozenset()) -> tuple[Counter, dict]:
    stats = Counter()
    per_file = {}
    cur = con.cursor()

    for name, direction in FILES.items():
        path = directory / name
        if not path.exists():
            stats[f"MISSING: {name}"] += 1
            continue
        ccss_rows, state_rows = load_file(path, direction, stats, quarantine)
        cur.executemany(SQL_CCSS, ccss_rows)
        cur.executemany(SQL_STATE, state_rows)
        per_file[name] = {"direction": direction,
                          "ccss_rows": len(ccss_rows),
                          "state_rows": len(state_rows),
                          "model_version": model_version(path)}
    con.commit()
    return stats, per_file


# -------------------------------------------------------------------- report

def report(con, stats: Counter, per_file: dict) -> None:
    """
    Print, do not just load. The reach table below is the number §5 has been
    waiting for and the reason the acceptance bar for TX and FL was left unset.
    """
    print("\nfiles")
    for name, info in per_file.items():
        print(f"  {name:34s} {info['direction']:14s} "
              f"ccss {info['ccss_rows']:7,d}  state {info['state_rows']:7,d}")
        print(f"    {info['model_version']}")

    if stats:
        print("\nanomalies (counted, not silently dropped)")
        for label, n in sorted(stats.items()):
            print(f"  {n:6,d}  {label}")

    for table in ("model_predictions", "model_state_predictions"):
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"\n{table}: {n:,d} rows")
        for direction, state, rows in con.execute(
                f"SELECT direction, state, COUNT(*) FROM {table}"
                " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 8"):
            print(f"  {direction:16s} {state:4s} {rows:8,d}")

    # Tier by direction. This is the table that stops someone reading the 100%
    # `No Match` files as "the model found nothing".
    print("\ntier by direction — the thresholds were calibrated for"
          " state_to_ccss ONLY")
    for direction, tier, rows in con.execute(
            "SELECT direction, tier, COUNT(*) FROM model_predictions"
            " GROUP BY 1, 2 ORDER BY 1, 3 DESC"):
        print(f"  {direction:16s} {str(tier):10s} {rows:8,d}")

    reach_report(con)


def reach_report(con) -> None:
    """
    Truth-code reach per state, split by direction and by the top-10 cut.

    Split by direction because the split is the finding. The same model over
    the same pairs keeps almost all of its reach one way and loses half of it
    the other, once §3's top-10 contract is applied -- see the note printed
    below the table.
    """
    anchors = {r[0] for r in con.execute(
        "SELECT DISTINCT standard_code FROM node_standards_parsed"
        " WHERE state IS NULL")}
    truth = defaultdict(set)
    for state, code in con.execute(
            "SELECT state, standard_code FROM node_standards_parsed"
            " WHERE state IS NOT NULL"):
        truth[state].add(code)

    reach = defaultdict(set)          # (state, direction, cut) -> state codes
    for state, state_code, ccss_code, rank, direction in con.execute(
            "SELECT state, state_code, ccss_code, rank, direction"
            " FROM model_predictions"):
        if ccss_code not in anchors:
            continue
        reach[(state, direction, "k")].add(state_code)
        if rank <= 10:
            reach[(state, direction, "10")].add(state_code)

    def cell(state, direction, cut, codes):
        hit = len(codes & reach[(state, direction, cut)])
        return (f"{hit:4d} {hit / len(codes):4.0%}" if reach[(state, direction, "k")]
                else f"{'—':>9s}")

    print("\nPath C reach against the ladder hand tags (node grain)")
    print(f"  {'':6s} {'':>6s} {'state_to_ccss':>19s}   {'ccss_to_state':>19s}")
    print(f"  {'state':6s} {'truth':>6s} {'@k':>9s} {'@10':>9s}   "
          f"{'@k':>9s} {'@10':>9s}")
    print("  " + "-" * 56)
    for state, codes in sorted(truth.items(), key=lambda kv: -len(kv[1])):
        if not any(reach[(state, d, "k")] for d in (STATE_TO_CCSS, CCSS_TO_STATE)):
            continue
        print(f"  {state:6s} {len(codes):6d} "
              f"{cell(state, STATE_TO_CCSS, 'k', codes)} "
              f"{cell(state, STATE_TO_CCSS, '10', codes)}   "
              f"{cell(state, CCSS_TO_STATE, 'k', codes)} "
              f"{cell(state, CCSS_TO_STATE, '10', codes)}")

    print("\n  Read the two directions apart. At full k they agree — TX 99%,"
          " FL 100%\n  both ways. At §3's top-10 contract they do not:\n"
          "\n      direction        TX @10    FL @10\n"
          "      state_to_ccss       98%      100%\n"
          "      ccss_to_state       45%       58%\n"
          "\n  Same model, same pairs, opposite arrows. A state standard's own"
          " top-10\n  CCSS list is a short focused list; a CCSS standard's"
          " top-10 STATE list has\n  to choose ten out of that state's whole"
          " corpus, so it drops correct\n  answers to rank 11+. Prefer"
          " state_to_ccss, and do not read ccss_to_state's\n  weaker column as"
          " the model being weaker.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB))
    ap.add_argument("--predictions", default=str(config.PREDICTIONS))
    ap.add_argument("--strict", action="store_true",
                    help="abort entirely on a Path 0 gate failure instead of"
                         " quarantining the failing target state")
    args = ap.parse_args()

    directory = Path(args.predictions)
    if not directory.exists():
        sys.exit(f"No predictions directory: {directory}")

    con = sqlite3.connect(args.db)

    print_preflight(preflight(directory))

    gate = path0_gate(con, directory)
    print_gate(gate)

    quarantine = frozenset()
    if not gate["passed"]:
        if args.strict:
            con.close()
            sys.exit("\nABORTED — nothing was loaded (--strict).")
        quarantine = frozenset({"CA"})
        print(f"\n  QUARANTINE: {', '.join(sorted(quarantine))} as a TARGET"
              " state, in every direction.\n"
              "  The CA target corpus is the 14-code CA-not-CCSS set in all"
              " three runs, so\n  no CCSS->CA or *->CA prediction can be"
              " correct except by coincidence. John's\n  ruling (step 8):"
              " proceed with TX and FL; the CA-not-CCSS set is small enough"
              " to\n  tag by hand, and Path 0 already covers CA at 92%.\n"
              "  CA as an ANCHOR is kept — it scores against the intact CCSS"
              " corpus.")

    con.execute("DELETE FROM model_predictions")
    con.execute("DELETE FROM model_state_predictions")
    print("\n  Cleared both prediction tables — the rows in them predate the"
          " gate and\n  include the quarantined CA corpus.")

    stats, per_file = load_all(con, directory, quarantine)
    report(con, stats, per_file)
    con.close()


if __name__ == "__main__":
    main()
