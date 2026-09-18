"""Codex source interpretation only; no date buckets or presentation limits."""

import re

from ..models import Event, Session, SessionLog
from ..text import json_text
from .codex_content import codex_text, normalized_type


def ordered(rows):
    return sorted(rows, key=lambda r: (r.get("ordinal", 10**18), r.get("_line_no", 0)))


def tool_status(item):
    status = normalized_type(item.get("status"))
    if (
        status in ("failed", "declined", "cancelled", "canceled", "error")
        or item.get("error") is not None
        or item.get("success") is False
    ):
        return "failed"
    if item.get("exit_code") is not None and item["exit_code"] != 0:
        return "failed"
    if status in ("inprogress", "running", "pending", "started"):
        return "in_progress"
    if (
        status in ("completed", "complete", "success", "succeeded")
        or item.get("success") is True
        or item.get("exit_code") == 0
    ):
        return "success"
    return "unknown"


def legacy_input(body):
    """Retain the raw body: unescaped legacy key/value text is ambiguous."""
    fields, key = {}, None
    for line in body.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*):[ \t]*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2)
        elif key is not None:
            fields[key] += "\n" + line
    return {**fields, "_raw": body}


def native_event(item, ts):
    kind = normalized_type(item.get("type"))
    if kind in ("usermessage", "agentmessage", "reasoning"):
        value = item.get("content")
        if kind == "reasoning":
            value = item.get("summary_text") or item.get("summary") or value
        text = codex_text(value)
        if not text or (
            kind == "usermessage" and text.lstrip().startswith("<environment_context>")
        ):
            return None
        return Event(
            {
                "usermessage": "prompt",
                "agentmessage": "answer",
                "reasoning": "thinking",
            }[kind],
            ts,
            text=text,
            phase=item.get("phase") if kind == "agentmessage" else None,
        )
    name, inp, output, summary, paths = (
        item.get("type") or "UnknownItem",
        {},
        "",
        None,
        (),
    )
    if kind == "commandexecution":
        name = "Bash"
        command = item.get("command") or ""
        inp = {"command": command, "cwd": item.get("cwd") or ""}
        display = (
            " ".join(map(str, command)) if isinstance(command, list) else str(command)
        )
        summary = " ".join(display.split())[:60]
        output = item.get("aggregated_output")
        if output is None:
            output = "\n".join(
                str(x) for x in (item.get("stdout"), item.get("stderr")) if x
            )
    elif kind == "filechange":
        name = "apply_patch"
        changes = item.get("changes") or []
        paths = tuple(
            str(c["path"]) for c in changes if isinstance(c, dict) and c.get("path")
        )
        inp, output, summary = (
            {"changes": changes},
            item.get("output") or "",
            ", ".join(paths[:3]),
        )
    elif kind == "mcptoolcall":
        server, tool = item.get("server") or "", item.get("tool") or "tool"
        name = f"mcp__{server}__{tool}" if server else str(tool)
        inp = item.get("arguments") or {}
        output = item.get("result") if item.get("error") is None else item["error"]
    elif kind == "dynamictoolcall":
        namespace, tool = item.get("namespace") or "", item.get("tool") or "tool"
        name = f"{namespace}.{tool}" if namespace else str(tool)
        inp, output = item.get("arguments") or {}, item.get("contentItems") or item.get(
            "content_items"
        )
    elif kind == "collabagenttoolcall":
        name = item.get("tool") or "Agent"
        inp = {
            k: item[k]
            for k in ("prompt", "model", "reasoningEffort", "receiverThreadIds")
            if item.get(k) is not None
        }
        output = item.get("agentsStates") or item.get("receiverThreadIds")
    elif kind in ("websearch", "extension"):
        name = str(item.get("kind") or "WebSearch")
        inp = {k: item[k] for k in ("query", "action") if item.get(k) is not None}
        output = item.get("results")
    else:
        inp = {
            k: v
            for k, v in item.items()
            if k not in ("id", "raw_content", "encrypted_content", "content")
        }
        output = item.get("content") or ""
    if not isinstance(inp, dict):
        inp = {"value": inp}
    return Event(
        "tool",
        ts,
        name=name,
        input=inp,
        output=json_text(output),
        status=tool_status(item),
        summary=summary,
        files=paths,
    )


def parse(rows, session_id=""):
    rows = ordered(rows)
    meta = next(
        (r.get("payload") or {} for r in rows if r.get("type") == "session_meta"), {}
    )
    log = SessionLog(
        Session(
            "codex",
            str(meta.get("session_id") or meta.get("id") or session_id),
            cwd=meta.get("cwd") or "",
            client=meta.get("originator") or meta.get("source") or "",
            version=meta.get("cli_version") or "",
            history_mode=meta.get("history_mode") or "",
        )
    )
    native = any(
        r.get("type") == "event_msg"
        and (r.get("payload") or {}).get("type") == "item_completed"
        for r in rows
    )
    pending = []
    mapping = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cached_input_tokens": "cache_read_input_tokens",
        "cache_write_input_tokens": "cache_creation_input_tokens",
        "reasoning_output_tokens": "reasoning_output_tokens",
    }
    for rec in rows:
        rtype, payload, ts = (
            rec.get("type"),
            rec.get("payload") or {},
            rec.get("timestamp", ""),
        )
        if rtype == "turn_context":
            if payload.get("model"):
                log.events.append(Event("model", ts, model=payload["model"]))
            continue
        if rtype != "event_msg":
            continue
        ptype = normalized_type(payload.get("type"))
        if ptype == "tokencount":
            info = payload.get("info") or {}
            total = info.get("total_token_usage")
            cumulative = isinstance(total, dict) and bool(total)
            usage = total if cumulative else info.get("last_token_usage") or {}
            tokens = {
                target: usage[source]
                for source, target in mapping.items()
                if isinstance(usage.get(source), int)
                and not isinstance(usage[source], bool)
            }
            log.events.append(
                Event(
                    "usage",
                    ts,
                    tokens=tokens,
                    usage_mode="cumulative" if cumulative else "incremental",
                )
            )
            continue
        if native:
            if ptype != "itemcompleted":
                continue
            event = native_event(payload.get("item") or {}, ts)
            if event:
                log.events.append(event)
        else:
            text = codex_text(payload.get("message") or payload.get("content"))
            if (
                ptype == "usermessage"
                and text
                and not text.lstrip().startswith("<environment_context>")
            ):
                log.events.append(Event("prompt", ts, text=text))
            elif ptype == "agentmessage":
                call = re.fullmatch(
                    r"\s*\[external_agent_tool_call:\s*([^\]]+)\]\s*(.*?)\s*\[/external_agent_tool_call\]\s*",
                    text,
                    re.S,
                )
                result = re.fullmatch(
                    r"\s*\[external_agent_tool_result\]\s*(.*?)\s*\[/external_agent_tool_result\]\s*",
                    text,
                    re.S,
                )
                if call:
                    event = Event(
                        "tool",
                        ts,
                        name=call.group(1).strip(),
                        input=legacy_input(call.group(2).strip()),
                    )
                    log.events.append(event)
                    pending.append(event)
                elif result and pending:
                    # These markers have neither correlation IDs nor a success flag.
                    pending.pop(0).output = result.group(1).strip()
                elif text:
                    log.events.append(Event("answer", ts, text=text))
        if ts:
            log.events.append(Event("activity", ts))
    return log
