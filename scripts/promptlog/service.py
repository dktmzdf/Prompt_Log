"""Application pipeline and compatibility entry points."""

import sys
from contextlib import closing
from pathlib import Path, PureWindowsPath

from . import store
from .adapters import claude, codex
from .assemble import assemble
from .readers import detect_agent, parse_lines, read_jsonl
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


def stored_stem(source_path):
    """적재한 OS와 무관하게 파일명을 얻는다. `PureWindowsPath`는 역슬래시와 `/`를 모두 가른다."""
    return PureWindowsPath(source_path).stem


def reindex(report_root=REPORT_ROOT):
    """저장된 원본 줄만으로 색인 전체를 다시 만든다. (세션 수, 이벤트 연결 수).

    파싱 입력은 export와 같아야 `source_id`가 일치한다. 그래서 같은 줄 해석
    (`parse_lines`)과 같은 세션 인자(원본 파일명 stem)를 쓴다. 비우기와 재생성을 한
    트랜잭션으로 묶어, 중간에 실패하면 기존 색인으로 되돌아간다.
    """
    with closing(store.connect(store.store_path(report_root))) as conn, conn:
        store.clear_index(conn)
        sessions = store.stored_sessions(conn)
        linked = 0
        for key, agent, source_path in sessions:
            if agent not in ADAPTERS:
                raise ValueError(f"{key}: 지원하지 않는 에이전트 {agent}")
            rows = parse_lines(store.raw_lines(conn, key))
            log = ADAPTERS[agent].parse(rows, stored_stem(source_path))
            linked += store.write_index(conn, key, log)
    return len(sessions), linked


def export(path, agent=None, report_root=REPORT_ROOT):
    path = Path(path)
    rows = read_jsonl(path)
    selected = agent or detect_agent(path, rows)
    if selected not in ADAPTERS:
        raise ValueError(f"지원하지 않는 에이전트: {selected}")
    log = ADAPTERS[selected].parse(rows, path.stem)
    archive(path, log, report_root)
    return write_reports(present(assemble(log)), log.session, path, report_root)
