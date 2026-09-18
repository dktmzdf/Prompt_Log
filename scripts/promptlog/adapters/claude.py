"""Claude message blocks, ancestor filtering and ID-based tool pairing."""

from ..models import Session, SessionLog, Event
from ..text import PATH_KEYS
from .claude_content import (
    is_human_prompt,
    prompt_text,
    denial_feedback,
    slash_command,
    result_text,
)

EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


def ordered(rows, session_id=""):
    return sorted(
        (
            r
            for r in rows
            if not session_id or r.get("sessionId", session_id) == session_id
        ),
        key=lambda r: r.get("timestamp") or "",
    )


def parse(rows, session_id=""):
    rows = ordered(rows, session_id)
    session = Session("claude", session_id)
    results = {}
    for row in rows:
        if not session.cwd and row.get("cwd"):
            session.cwd = row["cwd"]
        session.branch = row.get("gitBranch") or session.branch
        if row.get("type") == "user":
            for block in row.get("message", {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block
    log = SessionLog(session)
    for row in rows:
        ts = row.get("timestamp") or ""
        said, via = None, None
        if is_human_prompt(row):
            said = prompt_text(row.get("message", {}).get("content"))
        elif row.get("type") == "user":
            said = denial_feedback(row)
            via = "도구 거부와 함께"
            if not said:
                said, via = slash_command(row), "슬래시 커맨드"
        if said:
            log.events.append(Event("prompt", ts, text=said, via=via))
        if row.get("toolDenialKind"):
            log.events.append(Event("denial", ts, text=row["toolDenialKind"]))
        if row.get("type") == "assistant":
            msg = row.get("message") or {}
            if msg.get("model"):
                log.events.append(Event("model", ts, model=msg["model"]))
            usage = msg.get("usage") or {}
            for part in usage.get("iterations") or ([usage] if usage else []):
                tokens = {
                    k: part.get(k) or 0
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                }
                log.events.append(Event("usage", ts, tokens=tokens))
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind in ("text", "thinking") and block.get(kind, "").strip():
                    log.events.append(
                        Event(
                            "answer" if kind == "text" else "thinking",
                            ts,
                            text=block[kind].strip(),
                        )
                    )
                elif kind == "tool_use":
                    name, inp = block.get("name", "?"), block.get("input") or {}
                    result = results.get(block.get("id"))
                    status = (
                        "unknown"
                        if result is None
                        else "failed" if result.get("is_error") else "success"
                    )
                    files = ()
                    if name in EDIT_TOOLS and isinstance(inp, dict):
                        path = next((inp[k] for k in PATH_KEYS if inp.get(k)), None)
                        if path:
                            files = (path,)
                    log.events.append(
                        Event(
                            "tool",
                            ts,
                            name=name,
                            input=inp,
                            output=result_text((result or {}).get("content")),
                            status=status,
                            files=files,
                        )
                    )
        # Keep source activity timestamps without rendering internal records.
        if ts:
            log.events.append(Event("activity", ts))
    return log
