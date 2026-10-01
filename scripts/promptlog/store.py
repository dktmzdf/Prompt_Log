"""SQLite 정본 저장소. 원본 줄은 여기에만 무절단으로 남고 리포트는 관여하지 않는다."""

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000
STORE_NAME = "promptlog.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
  session_key    TEXT PRIMARY KEY,
  agent          TEXT NOT NULL,
  source_path    TEXT NOT NULL,
  cwd            TEXT,
  branch         TEXT,
  client         TEXT,
  version        TEXT,
  history_mode   TEXT,
  ts_first       TEXT,
  ts_last        TEXT,
  ingested_lines INTEGER NOT NULL DEFAULT 0,
  ingested_at    TEXT
);

CREATE TABLE IF NOT EXISTS raw_line (
  session_key TEXT NOT NULL REFERENCES session,
  line_no     INTEGER NOT NULL,
  body        TEXT NOT NULL,
  PRIMARY KEY (session_key, line_no)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS event (
  source_id TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,
  ts        TEXT,
  text      TEXT,
  tool_name TEXT,
  status    TEXT,
  payload   TEXT
);

CREATE TABLE IF NOT EXISTS session_event (
  session_key TEXT NOT NULL REFERENCES session,
  source_id   TEXT NOT NULL REFERENCES event,
  seq         INTEGER NOT NULL,
  turn_no     INTEGER,
  PRIMARY KEY (session_key, source_id)
);

CREATE INDEX IF NOT EXISTS ix_se_order ON session_event(session_key, seq);
CREATE INDEX IF NOT EXISTS ix_se_event ON session_event(source_id);

CREATE TABLE IF NOT EXISTS turn (
  session_key TEXT NOT NULL REFERENCES session,
  turn_no     INTEGER NOT NULL,
  ts_start    TEXT,
  ts_end      TEXT,
  prompt_text TEXT,
  content_hash TEXT,
  PRIMARY KEY (session_key, turn_no)
);
"""

TABLES = ("session", "raw_line", "event", "session_event", "turn")

# 버전 N을 N+1로 올리는 문. 새 파일은 SCHEMA가 최신 정의로 바로 만든다.
MIGRATIONS = {
    1: (
        "ALTER TABLE turn ADD COLUMN content_hash TEXT",
        "CREATE INDEX IF NOT EXISTS ix_se_event ON session_event(source_id)",
    )
}


def read_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_version(conn, version):
    # 파라미터 바인딩을 못 쓰는 PRAGMA다. 값은 코드 상수라 외부 입력이 아니다.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def refuse_newer(version):
    if version > SCHEMA_VERSION:
        raise ValueError(f"저장소 스키마 버전 {version}이 이 코드({SCHEMA_VERSION})보다 새롭습니다")


def upgrade(conn):
    """버전별 문을 쓰기 트랜잭션 하나로 실행한다. 실패하면 이전 버전 그대로 남는다.

    Stop 훅이 병렬로 돌면 다른 연결이 먼저 올렸을 수 있다. 그래서 쓰기 잠금을 잡은 뒤
    버전을 다시 읽는다. 다시 읽지 않으면 두 번째 `ALTER`가 컬럼 중복으로 실패한다.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = read_version(conn)
        refuse_newer(current)
        for version in range(current, SCHEMA_VERSION):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
        if current != SCHEMA_VERSION:
            set_version(conn, SCHEMA_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_schema(conn):
    """스키마를 최신 버전으로 맞춘다. 이미 최신인 DB에 다시 불러도 안전하다."""
    version = read_version(conn)
    refuse_newer(version)
    if version == 0:
        conn.executescript(SCHEMA)
        set_version(conn, SCHEMA_VERSION)
        conn.commit()
    elif version < SCHEMA_VERSION:
        upgrade(conn)
    return conn


def connect(path):
    """저장소를 열고 스키마를 보장한다.

    Stop 훅은 병렬로 실행되고 여러 세션이 동시에 진행될 수 있다. WAL과 busy_timeout이
    없으면 그 경합이 `database is locked`로 드러난다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    if created:
        os.chmod(path, 0o600)  # 개인 대화 로그가 들어간다
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        return ensure_schema(conn)
    except Exception:
        conn.close()
        raise


def store_path(report_root):
    """리포트 루트 아래에 둔다. `--output`을 바꾸면 저장소도 함께 옮겨 간다."""
    return Path(report_root) / STORE_NAME


def session_key_of(session, source_path):
    return str(session.session_id or Path(source_path).stem)


def upsert_session(conn, key, session, source_path, ts_first="", ts_last=""):
    conn.execute(
        "INSERT INTO session (session_key, agent, source_path, cwd, branch, client,"
        " version, history_mode, ts_first, ts_last, ingested_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(session_key) DO UPDATE SET"
        " agent = excluded.agent, source_path = excluded.source_path,"
        " cwd = excluded.cwd, branch = excluded.branch, client = excluded.client,"
        " version = excluded.version, history_mode = excluded.history_mode,"
        " ts_first = excluded.ts_first, ts_last = excluded.ts_last,"
        " ingested_at = excluded.ingested_at",
        (
            key,
            session.agent,
            str(source_path),
            session.cwd,
            session.branch,
            session.client,
            session.version,
            session.history_mode,
            ts_first,
            ts_last,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def split_lines(data):
    """바이트 단위로 자른다. str.splitlines는 유니코드 구분자까지 잘라 원본을 깨뜨린다."""
    return data.splitlines(keepends=True)


def ingest_lines(conn, key, source_path):
    """새 줄만 적재하고 적재한 줄 수를 돌려준다.

    종결자 없는 마지막 줄은 아직 쓰이는 중일 수 있다. 저장은 하되 완료로 세지 않아서
    다음 적재가 같은 `line_no`를 덮어쓰도록 둔다.
    """
    lines = split_lines(Path(source_path).read_bytes())
    row = conn.execute(
        "SELECT ingested_lines FROM session WHERE session_key = ?", (key,)
    ).fetchone()
    start = row[0] if row else 0
    pending = [(key, n, lines[n].decode("utf-8")) for n in range(start, len(lines))]
    conn.executemany(
        "INSERT OR REPLACE INTO raw_line (session_key, line_no, body) VALUES (?, ?, ?)",
        pending,
    )
    complete = len(lines)
    if lines and not lines[-1].endswith((b"\n", b"\r")):
        complete -= 1
    conn.execute(
        "UPDATE session SET ingested_lines = ? WHERE session_key = ?", (complete, key)
    )
    return len(pending)


def ingest(conn, source_path, session, ts_first="", ts_last=""):
    """세션 메타와 원본 줄을 한 트랜잭션으로 적재한다. (세션 키, 적재한 줄 수)."""
    key = session_key_of(session, source_path)
    with conn:
        upsert_session(conn, key, session, source_path, ts_first, ts_last)
        added = ingest_lines(conn, key, source_path)
    return key, added


def raw_text(conn, key):
    """적재된 원본을 순서대로 이어붙인다. 원본 파일과 바이트 단위로 같아야 한다."""
    rows = conn.execute(
        "SELECT body FROM raw_line WHERE session_key = ? ORDER BY line_no", (key,)
    )
    return "".join(row[0] for row in rows)


def raw_lines(conn, key):
    """적재된 원본 줄을 순서대로 낸다. 첫 줄 BOM은 `read_jsonl`(utf-8-sig)처럼 벗긴다."""
    rows = conn.execute(
        "SELECT line_no, body FROM raw_line WHERE session_key = ? ORDER BY line_no", (key,)
    )
    for line_no, body in rows:
        yield body.removeprefix("﻿") if line_no == 0 else body


def stored_sessions(conn):
    """재색인 대상. 원본 파일 경로는 파일명(stem)만 쓰고 파일은 열지 않는다."""
    return conn.execute(
        "SELECT session_key, agent, source_path FROM session ORDER BY session_key"
    ).fetchall()


def clear_index(conn):
    """색인 테이블만 비운다. `session`·`raw_line`은 정본이라 건드리지 않는다.

    커밋하지 않는다. 호출자가 재색인 전체와 한 트랜잭션으로 묶는다.
    """
    for table in ("turn", "session_event", "event"):
        conn.execute(f"DELETE FROM {table}")  # 코드 상수 테이블명


def event_row(event):
    """필터·정렬에 쓰는 값은 컬럼으로, 나머지는 `payload` JSON으로 나눈다."""
    is_tool = event.kind == "tool"
    rest = asdict(event)
    for name in ("source_id", "kind", "ts", "status", "output" if is_tool else "text"):
        rest.pop(name)
    if is_tool:
        rest.pop("name")
    return (
        event.source_id,
        event.kind,
        event.ts,
        event.output if is_tool else event.text,
        event.name if is_tool else None,
        event.status if is_tool else None,
        json.dumps(rest, ensure_ascii=False, default=str),
    )


def assign_turns(events):
    """첫 프롬프트 이전은 턴 0, 프롬프트마다 새 턴.

    `assemble()`의 날짜 버킷을 펼친 것과 같은 규칙이다 — 비프롬프트 이벤트는 가장 최근
    프롬프트에 귀속된다. 두 구현이 어긋나지 않는지는 회귀 테스트가 지킨다.
    """
    turns, current = [], 0
    for event in events:
        if event.kind == "prompt":
            current += 1
        turns.append(current)
    return turns


def fingerprint(rows):
    """턴의 색인 행을 순서대로 직렬화한 해시. `seq`는 넣지 않는다 — 앞쪽에 이벤트가 끼어들면
    뒤의 값이 전부 밀려 모든 턴의 지문이 바뀐다. ASCII 이스케이프라 깨진 문자도 안전하다."""
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()


def turn_rows(key, events, turns, rows):
    """턴별 구간·첫 프롬프트·지문. `rows`는 `event` 표에 쓰는 `event_row()` 값 그대로다."""
    spans, grouped = {}, {}
    for event, turn, row in zip(events, turns, rows):
        start, end, prompt = spans.get(turn, (None, None, None))
        if event.kind == "prompt" and prompt is None:
            prompt = event.text
        if event.ts:
            start = min(start, event.ts) if start else event.ts
            end = max(end, event.ts) if end else event.ts
        spans[turn] = (start, end, prompt)
        grouped.setdefault(turn, []).append(row)
    return [
        (key, turn, *span, fingerprint(grouped[turn]))
        for turn, span in sorted(spans.items())
    ]


def index_log(conn, key, log):
    """파싱된 이벤트를 색인하고 커밋한다. `session` 행이 먼저 있어야 한다(외래키).

    이벤트는 `source_id`로 합쳐지고, 세션 귀속과 턴은 세션 단위로 지웠다가 다시 쓴다.
    그래서 재개 세션의 공유 이벤트는 한 행이 되고, 재적재해도 결과가 같다.
    """
    with conn:
        return write_index(conn, key, log)


def upsert_events(conn, rows):
    # REPLACE는 지웠다 다시 넣어 행마다 session_event 외래키 검사를 부른다. 매 턴
    # 전체를 다시 색인하므로 O(n^2)이 된다. 제자리 갱신은 삭제가 없다.
    conn.executemany(
        "INSERT INTO event (source_id, kind, ts, text, tool_name, status, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(source_id) DO UPDATE SET"
        " kind = excluded.kind, ts = excluded.ts, text = excluded.text,"
        " tool_name = excluded.tool_name, status = excluded.status,"
        " payload = excluded.payload",
        rows,
    )


def shared_turns(conn, key):
    """이 세션과 이벤트를 공유하는 다른 세션의 (세션, 턴) 쌍. 재개 세션 쌍이 여기 걸린다."""
    return conn.execute(
        "SELECT DISTINCT o.session_key, o.turn_no FROM session_event me"
        " JOIN session_event o ON o.source_id = me.source_id"
        " AND o.session_key != me.session_key WHERE me.session_key = ?",
        (key,),
    ).fetchall()


def turn_event_rows(conn, key, turn_no):
    """`event` 표에 저장된 값으로 그 턴을 구성하는 행. `event_row()`와 같은 모양이다."""
    return conn.execute(
        "SELECT e.source_id, e.kind, e.ts, e.text, e.tool_name, e.status, e.payload"
        " FROM session_event se JOIN event e ON e.source_id = se.source_id"
        " WHERE se.session_key = ? AND se.turn_no = ? ORDER BY se.seq",
        (key, turn_no),
    ).fetchall()


def refresh_shared_turns(conn, key):
    """공유 이벤트 행은 마지막 적재가 이긴다. 그 행을 쓰는 다른 세션의 턴 지문도 저장된
    내용 기준으로 다시 맞춘다. 지문은 언제나 `event` 표가 말하는 턴 내용의 해시여야 한다."""
    for other, turn_no in shared_turns(conn, key):
        digest = fingerprint(turn_event_rows(conn, other, turn_no))
        conn.execute(
            "UPDATE turn SET content_hash = ? WHERE session_key = ? AND turn_no = ?"
            " AND content_hash IS NOT ?",
            (digest, other, turn_no, digest),
        )


def write_index(conn, key, log):
    """`index_log`의 본체. 커밋하지 않으므로 여러 세션을 한 트랜잭션에 묶을 수 있다."""
    events, turns = log.events, assign_turns(log.events)
    rows = [event_row(event) for event in events]
    links = [
        (key, event.source_id, seq, turn)
        for seq, (event, turn) in enumerate(zip(events, turns))
    ]
    upsert_events(conn, rows)
    conn.execute("DELETE FROM session_event WHERE session_key = ?", (key,))
    conn.execute("DELETE FROM turn WHERE session_key = ?", (key,))
    conn.executemany(
        "INSERT INTO session_event (session_key, source_id, seq, turn_no)"
        " VALUES (?, ?, ?, ?)",
        links,
    )
    conn.executemany(
        "INSERT INTO turn (session_key, turn_no, ts_start, ts_end, prompt_text,"
        " content_hash) VALUES (?, ?, ?, ?, ?, ?)",
        turn_rows(key, events, turns, rows),
    )
    refresh_shared_turns(conn, key)
    return len(links)
