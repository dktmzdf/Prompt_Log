"""SQLite 정본 저장소. 원본 줄은 여기에만 무절단으로 남고 리포트는 관여하지 않는다."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000

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

CREATE TABLE IF NOT EXISTS turn (
  session_key TEXT NOT NULL REFERENCES session,
  turn_no     INTEGER NOT NULL,
  ts_start    TEXT,
  ts_end      TEXT,
  prompt_text TEXT,
  PRIMARY KEY (session_key, turn_no)
);
"""

TABLES = ("session", "raw_line", "event", "session_event", "turn")


def ensure_schema(conn):
    """스키마를 적용하고 버전을 맞춘다. 이미 최신인 DB에 다시 불러도 안전하다."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f"저장소 스키마 버전 {version}이 이 코드({SCHEMA_VERSION})보다 새롭습니다")
    conn.executescript(SCHEMA)
    if version != SCHEMA_VERSION:
        # 파라미터 바인딩을 못 쓰는 PRAGMA다. 값은 코드 상수라 외부 입력이 아니다.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
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
