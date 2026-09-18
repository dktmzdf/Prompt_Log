"""Read JSONL once; source-specific ordering belongs to adapters."""

from pathlib import Path

import json

AGENTS = ("claude", "codex")
SOURCE_ROOTS = {
    "claude": Path.home() / ".claude" / "projects",
    "codex": Path.home() / ".codex" / "sessions",
}


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream):
            try:
                value = json.loads(line)
            except ValueError:
                continue  # A live writer can leave a partial final line.
            if isinstance(value, dict):
                value["_line_no"] = line_no
                rows.append(value)
    return rows


def detect_agent(path, recs=None):
    rows = read_jsonl(path) if recs is None else recs
    for row in rows[:50]:
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            return "codex"
        if row.get("type") in ("user", "assistant") and isinstance(
            row.get("message"), dict
        ):
            return "claude"
    parts = str(path).replace("\\", "/").lower()
    for agent in AGENTS:
        if f"/.{agent}/" in parts:
            return agent
    raise ValueError("Claude/Codex 세션 형식을 판별할 수 없습니다")


def discover(agent=None):
    for selected in (AGENTS if agent is None else (agent,)):
        root = SOURCE_ROOTS[selected]
        paths = (
            root.glob("*/*.jsonl") if selected == "claude" else root.rglob("*.jsonl")
        )
        yield from ((path, selected) for path in sorted(paths))
