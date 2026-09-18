"""Report paths and atomic per-file writes; source logs are never changed."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from .render import real_prompts, render_jsonl, render_md
from .timing import local_dt

REPORT_ROOT = Path.home() / "agent-prompt-logs"


def session_hash(session_id):
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:8]


def safe_slug(value):
    # A transcript-controlled project name must remain one portable path segment.
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value:
        return "project"
    if value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = "_" + value
    return value


def atomic_write(target, body):
    """Replace one report only after its full UTF-8 content has been written."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=".prompt-log-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(body)
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_reports(buckets, session, source_path, report_root):
    root = Path(report_root)
    sid8, days, written = session_hash(session.session_id), sorted(buckets), []
    for day in days:
        bucket = buckets[day]
        stats = bucket["stats"]
        slug = safe_slug(stats["project"] or Path(source_path).parent.name.lstrip("-"))
        stamps = [dt for ts in stats["times"] if (dt := local_dt(ts))]
        hhmm = min(stamps).strftime("%H-%M") if stamps else "00-00"
        directory = root / session.agent / slug / day / f"{hhmm}_{sid8}"
        directory.mkdir(parents=True, exist_ok=True)
        for part in (
            root,
            root / session.agent,
            root / session.agent / slug,
            directory.parent,
            directory,
        ):
            os.chmod(part, 0o700)
        base = directory / f"{slug}-{day}-{sid8}"
        bodies = (
            (".md", render_md(bucket["prompts"], stats, session.session_id, days, day)),
            (
                ".jsonl",
                render_jsonl(bucket["events"], session.agent, session.session_id),
            ),
        )
        for suffix, body in bodies:
            # with_suffix would remove part of names such as whisper.cpp-master.
            atomic_write(Path(str(base) + suffix), body)
        written.append(
            (base, real_prompts(bucket["prompts"]), sum(stats["tools"].values()))
        )
    return written
