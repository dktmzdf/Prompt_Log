"""Application pipeline and compatibility entry points."""

import sys
from contextlib import closing
from pathlib import Path

from . import store
from .adapters import claude, codex
from .assemble import assemble
from .readers import detect_agent, read_jsonl
from .render import present
from .storage import REPORT_ROOT, write_reports

ADAPTERS = {"claude": claude, "codex": codex}


def load(path):
    return claude.ordered(read_jsonl(path), Path(path).stem)


def load_codex(path):
    return codex.ordered(read_jsonl(path))


def build(rows):
    """Compatibility wrapper; new callers should use parse -> assemble -> present."""
    return present(assemble(claude.parse(rows)))


def build_codex(rows):
    meta = next(
        (r.get("payload") or {} for r in rows if r.get("type") == "session_meta"), {}
    )
    return present(assemble(codex.parse(rows))), meta


def archive(path, log, report_root):
    """원본과 색인을 저장소에 넣는다. 실패해도 리포트는 계속 만들어야 하므로 삼킨다."""
    try:
        times = [event.ts for event in log.events if event.ts]
        with closing(store.connect(store.store_path(report_root))) as conn:
            key, _ = store.ingest(
                conn, path, log.session, min(times, default=""), max(times, default="")
            )
            store.index_log(conn, key, log)
    except Exception as exc:
        print(f"prompt-log store: {exc}", file=sys.stderr)


def export(path, agent=None, report_root=REPORT_ROOT):
    path = Path(path)
    rows = read_jsonl(path)
    selected = agent or detect_agent(path, rows)
    if selected not in ADAPTERS:
        raise ValueError(f"지원하지 않는 에이전트: {selected}")
    log = ADAPTERS[selected].parse(rows, path.stem)
    archive(path, log, report_root)
    return write_reports(present(assemble(log)), log.session, path, report_root)
