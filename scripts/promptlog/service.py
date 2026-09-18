"""Application pipeline and compatibility entry points."""

from pathlib import Path

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


def export(path, agent=None, report_root=REPORT_ROOT):
    path = Path(path)
    rows = read_jsonl(path)
    selected = agent or detect_agent(path, rows)
    if selected not in ADAPTERS:
        raise ValueError(f"지원하지 않는 에이전트: {selected}")
    log = ADAPTERS[selected].parse(rows, path.stem)
    return write_reports(present(assemble(log)), log.session, path, report_root)
