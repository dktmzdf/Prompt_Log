"""SQLite 정본 저장소. 원본 줄은 여기에만 무절단으로 남고 리포트는 관여하지 않는다."""

import os
import sqlite3
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
