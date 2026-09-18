"""The only implementation of turn grouping, day boundaries and statistics."""

from collections import Counter, OrderedDict, defaultdict
from pathlib import PurePosixPath

from .models import SessionLog
from .timing import local_dt
from .text import PRE_PROMPT, summarize_tool


def project_name(cwd):
    return PurePosixPath(cwd.replace("\\", "/").rstrip("/")).name if cwd else ""


def assemble(log: SessionLog):
    session = log.session
    buckets = OrderedDict()
    current = None
    pending = []
    cumulative = {}

    def new_bucket():
        return {
            "events": [],
            "prompts": [],
            "pre": [],
            "stats": {
                "tools": Counter(),
                "models": Counter(),
                "files": defaultdict(Counter),
                "denials": Counter(),
                "tokens": Counter(),
                "times": [],
                "project": project_name(session.cwd),
                "branch": session.branch,
                "agent": session.agent,
                "client": session.client,
                "version": session.version,
                "history_mode": session.history_mode,
            },
        }

    def apply(event):
        stats = current["stats"]
        # Metadata does not extend activity duration on its own. Adapters emit
        # activity explicitly for source records that represent session activity.
        if event.ts and event.kind in (
            "activity",
            "prompt",
            "answer",
            "thinking",
            "tool",
        ):
            stats["times"].append(event.ts)
        if event.kind == "activity":
            return
        if event.kind == "model":
            if event.model:
                stats["models"][event.model] += 1
            return
        if event.kind == "denial":
            stats["denials"][event.text] += 1
            return
        if event.kind == "usage":
            previous = cumulative.get(event.usage_stream, {})
            # A decreasing total denotes a reset/new accounting epoch.
            reset = any(
                value < previous.get(key, 0) for key, value in event.tokens.items()
            )
            for key, value in event.tokens.items():
                delta = value
                if event.usage_mode == "cumulative" and not reset:
                    delta -= previous.get(key, 0)
                stats["tokens"][key] += max(0, delta)
            if event.usage_mode == "cumulative":
                cumulative[event.usage_stream] = {
                    **({} if reset else previous),
                    **event.tokens,
                }
            else:
                # If totals appear after last-only samples, those samples have
                # already been charged. Keep the accounting baseline aligned.
                cumulative[event.usage_stream] = {
                    **previous,
                    **{
                        key: previous.get(key, 0) + max(0, value)
                        for key, value in event.tokens.items()
                    },
                }
            return
        if event.kind == "prompt":
            prompt = {"ts": event.ts, "text": event.text, "via": event.via, "items": []}
            current["prompts"].append(prompt)
            rendered = {"kind": "prompt", "ts": event.ts, "text": event.text}
            if event.via:
                rendered["via"] = event.via
            current["events"].append(rendered)
            return
        items = (
            current["prompts"][-1]["items"] if current["prompts"] else current["pre"]
        )
        if event.kind == "tool":
            stats["tools"][event.name] += 1
            if event.status == "success":
                for path in event.files:
                    stats["files"][path][event.name] += 1
            summary = (
                event.summary
                if event.summary is not None
                else summarize_tool(event.name, event.input)
            )
            items.append(("tool", event.name, summary))
            current["events"].append(
                {
                    "kind": "tool",
                    "ts": event.ts,
                    "name": event.name,
                    "summary": summary,
                    "input": event.input,
                    "output": event.output,
                    "status": event.status,
                    "ok": (
                        True
                        if event.status == "success"
                        else False if event.status == "failed" else None
                    ),
                }
            )
        else:
            rendered = {"kind": event.kind, "ts": event.ts, "text": event.text}
            if event.phase:
                rendered["phase"] = event.phase
            current["events"].append(rendered)
            items.append((event.kind, event.text, None))

    for event in log.events:
        when = local_dt(event.ts)
        content = event.kind in ("prompt", "answer", "thinking", "tool")
        if event.kind == "prompt" and when:
            current = buckets.setdefault(when.strftime("%Y-%m-%d"), new_bucket())
        elif current is None and content and when:
            current = buckets.setdefault(when.strftime("%Y-%m-%d"), new_bucket())
        if current is None:
            pending.append(event)
            continue
        for before in pending:
            apply(before)
        pending.clear()
        apply(event)

    for bucket in buckets.values():
        if bucket["pre"]:
            times = bucket["stats"]["times"]
            bucket["prompts"].insert(
                0,
                {
                    "ts": times[0] if times else "",
                    "text": "",
                    "via": PRE_PROMPT,
                    "items": bucket["pre"],
                },
            )
    return buckets
