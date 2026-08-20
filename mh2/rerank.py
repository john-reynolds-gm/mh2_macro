"""
rerank.py — the reranker's shared core: prompt, schema, model call, cleaning.

Split out of eval/rerank.py so production (scripts/run_reranks.py,
mh2/load_reranks.py) and the eval harness (eval/rerank.py) share exactly one
prompt, one schema, and one cleaning pass. Nothing here is eval-specific and
nothing here calls the API on its own -- `main()` lives in the two callers.

See eval/rerank.py's module docstring for why a reranker is the right shape
for this problem and what it can and cannot fix. Validated there: TX/FL
recall@10 moved from 0.817/0.895 to 0.957/0.981 against a 0.961/1.000 ceiling
on the full 115-subject held-out run (2026-08-17).
"""

import json
from pathlib import Path

MODEL = "claude-opus-5"
RERANK_DEPTH = 50  # candidates per node the reranker sees; caps its upside at recall@50

SYSTEM = """\
You rank state mathematics standards by how well they correspond to a lesson \
ladder node in a K-5 curriculum.

You are given the node's text, the CCSS standards its authors tagged to it, \
and a candidate list of state standards retrieved by an embedding model. The \
retrieval is known to be good and the ORDERING is known to be poor: the \
correct answers are usually somewhere in the list but rarely at the top.

Your job is to reorder the candidates, best first, by whether a curriculum \
author would tag that state standard to this node.

Rules:
- Rank ONLY codes from the candidate list. Never invent a code.
- Include every candidate exactly once.
- Judge the standard's TEXT against the node's content, not code similarity. \
Codes that look alike often mean different things across states.
- Grade proximity is evidence, not a filter. A correct correspondence may sit \
one or two grades from the node.
- A state standard can be a partial match — covering some of the node's \
content — and still belong high in the list.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "description": "Every candidate code, best correspondence first.",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "relevance": {
                        "type": "string",
                        "enum": ["aligned", "partial", "unrelated"],
                    },
                },
                "required": ["code", "relevance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranking"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ prompts

def node_context(cur, node_id: str) -> str:
    """The node as the author wrote it — §4.2's text, which nothing else uses."""
    row = cur.execute(
        "SELECT node_text, concept_skill, goal FROM nodes WHERE node_id = ?",
        (node_id,)).fetchone()
    if not row:
        return ""
    text, concept_skill, goal = row
    parts = [f"Concept/Skill: {concept_skill}", f"Node: {text}"]
    if goal:
        parts.append(f"Goal: {goal}")
    for field in ("specifications", "required_skills_cases"):
        values = [v for (v,) in cur.execute(
            "SELECT value FROM node_fields WHERE node_id = ? AND field = ?"
            " ORDER BY ordinal", (node_id, field))]
        if values:
            label = field.replace("_", " ").title()
            parts.append(f"{label}:\n" + "\n".join(f"  - {v}" for v in values))
    return "\n".join(parts)


def standard_text(cur, code: str) -> str:
    row = cur.execute(
        "SELECT text FROM standards WHERE standard_id = ?", (code,)).fetchone()
    return (row[0] or "").strip() if row else ""


def build_prompt(cur, node_id: str, anchors: tuple, state: str,
                  candidates: list[str]) -> str:
    anchor_lines = []
    for anchor in anchors:
        text = standard_text(cur, anchor)
        anchor_lines.append(f"  {anchor}: {text}" if text else f"  {anchor}")

    listing = []
    for code in candidates:
        text = standard_text(cur, code) or "(no text on file)"
        listing.append(f"  {code}: {text}")

    return (
        f"# Node\n{node_context(cur, node_id)}\n\n"
        f"# CCSS standards the authors tagged to this node\n"
        + "\n".join(anchor_lines)
        + f"\n\n# Candidate {state} standards ({len(candidates)}), "
        "in the embedding model's order\n"
        + "\n".join(listing)
        + f"\n\nReorder all {len(candidates)} candidates, best first."
    )


# -------------------------------------------------------------------- model

def call_model(client, prompt: str) -> list[dict]:
    """One subject. Structured output so there is nothing to parse defensively."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={"effort": "high",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["ranking"]


def clean(ranking: list[dict], candidates: list[str]) -> tuple[list[str], dict]:
    """
    Keep the model honest: drop invented codes, dedupe, append anything it
    dropped in its original order.

    Appending rather than discarding matters — a candidate the model silently
    omitted would otherwise be counted as if the retrieval never produced it,
    which would flatter the reranker by shrinking its own list.
    """
    allowed, seen, out = set(candidates), set(), []
    stats = {"invented": 0, "duplicate": 0, "dropped": 0}
    for item in ranking:
        code = item.get("code")
        if code not in allowed:
            stats["invented"] += 1
        elif code in seen:
            stats["duplicate"] += 1
        else:
            seen.add(code)
            out.append(code)
    missing = [c for c in candidates if c not in seen]
    stats["dropped"] = len(missing)
    return out + missing, stats


# -------------------------------------------------------------------- cache

def load_cache(path: Path) -> dict:
    """key ('node_id|state') -> raw ranking, as the model returned it."""
    cache = {}
    if path.exists():
        for line in path.open():
            entry = json.loads(line)
            cache[entry["key"]] = entry["ranking"]
    return cache


def append_cache(path: Path, key: str, ranking: list[dict]) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps({"key": key, "ranking": ranking}) + "\n")
