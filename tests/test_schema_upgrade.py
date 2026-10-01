"""저장소 스키마 버전 1 -> 2 제자리 업그레이드 회귀. 원본 줄은 한 바이트도 바뀌면 안 된다."""

import sqlite3
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

from scripts.promptlog import store
from tests.test_store import store_dir

# 버전 1 스키마를 그대로 고정한다. 코드의 SCHEMA가 바뀌어도 업그레이드 입력은 변하면 안 된다.
V1_SCHEMA = """
CREATE TABLE session (
  session_key    TEXT PRIMARY KEY,
  agent          TEXT NOT NULL,
  source_path    TEXT NOT NULL,
  cwd TEXT, branch TEXT, client TEXT, version TEXT, history_mode TEXT,
  ts_first TEXT, ts_last TEXT,
  ingested_lines INTEGER NOT NULL DEFAULT 0,
  ingested_at    TEXT
);
CREATE TABLE raw_line (
  session_key TEXT NOT NULL REFERENCES session,
  line_no     INTEGER NOT NULL,
  body        TEXT NOT NULL,
  PRIMARY KEY (session_key, line_no)
) WITHOUT ROWID;
CREATE TABLE event (
  source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, ts TEXT, text TEXT,
  tool_name TEXT, status TEXT, payload TEXT
);
CREATE TABLE session_event (
  session_key TEXT NOT NULL REFERENCES session,
  source_id   TEXT NOT NULL REFERENCES event,
  seq         INTEGER NOT NULL,
  turn_no     INTEGER,
  PRIMARY KEY (session_key, source_id)
);
CREATE INDEX ix_se_order ON session_event(session_key, seq);
CREATE TABLE turn (
  session_key TEXT NOT NULL REFERENCES session,
  turn_no     INTEGER NOT NULL,
  ts_start TEXT, ts_end TEXT, prompt_text TEXT,
  PRIMARY KEY (session_key, turn_no)
);
PRAGMA user_version = 1;
"""

V1_ROWS = """
INSERT INTO session (session_key, agent, source_path, ingested_lines)
  VALUES ('s1', 'claude', 's1.jsonl', 2);
INSERT INTO raw_line VALUES ('s1', 0, char(65279) || '{"type": "user"}' || char(13, 10));
INSERT INTO raw_line VALUES ('s1', 1, '{"text": "한글 ✓"}' || char(10));
INSERT INTO event VALUES ('claude:uuid:u1#prompt', 'prompt', 't0', '질문', NULL, NULL, '{}');
INSERT INTO session_event VALUES ('s1', 'claude:uuid:u1#prompt', 0, 1);
INSERT INTO turn VALUES ('s1', 1, 't0', 't0', '질문');
"""

TURN_V1_COLUMNS = "session_key, turn_no, ts_start, ts_end, prompt_text"


def make_v1_store(path):
    """버전 1 코드가 만든 저장소처럼 WAL로 둔다. WAL은 파일에 영구 기록되는 설정이다."""
    with closing(sqlite3.connect(str(path))) as raw:
        raw.execute("PRAGMA journal_mode = WAL")
        raw.executescript(V1_SCHEMA + V1_ROWS)


def raw_dump(path):
    """`store.connect`를 거치지 않고 읽는다. 거치면 읽는 순간 업그레이드된다."""
    with closing(sqlite3.connect(str(path))) as raw:
        tables = {
            name: raw.execute(f"SELECT * FROM {name} ORDER BY 1, 2").fetchall()
            for name in ("session", "raw_line", "event", "session_event")
        }
        turns = raw.execute(f"SELECT {TURN_V1_COLUMNS} FROM turn ORDER BY 1, 2")
        tables["turn"] = turns.fetchall()
        tables["version"] = store.read_version(raw)
        return tables


def index_names(path):
    with closing(sqlite3.connect(str(path))) as raw:
        rows = raw.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        return {row[0] for row in rows}


def turn_columns(path):
    with closing(sqlite3.connect(str(path))) as raw:
        return [row[1] for row in raw.execute("PRAGMA table_info(turn)")]


class FreshStoreTests(unittest.TestCase):
    def test_new_store_is_latest_version_with_hash_column(self):
        with store_dir() as path:
            with closing(store.connect(path)):
                pass
            self.assertEqual(raw_dump(path)["version"], 2)
            self.assertIn("content_hash", turn_columns(path))
            self.assertIn("ix_se_event", index_names(path))


class UpgradeTests(unittest.TestCase):
    def test_v1_store_is_upgraded_in_place(self):
        with store_dir() as path:
            make_v1_store(path)
            before = raw_dump(path)
            with closing(store.connect(path)):
                pass
            after = raw_dump(path)
            self.assertEqual(after.pop("version"), store.SCHEMA_VERSION)
            before.pop("version")
            self.assertEqual(after, before)
            self.assertEqual(turn_columns(path).count("content_hash"), 1)
            self.assertIn("ix_se_event", index_names(path))
            with closing(sqlite3.connect(str(path))) as raw:
                hashes = raw.execute("SELECT content_hash FROM turn").fetchall()
            self.assertEqual(hashes, [(None,)])

    def test_reopening_upgraded_store_changes_nothing(self):
        with store_dir() as path:
            make_v1_store(path)
            with closing(store.connect(path)):
                pass
            first = (raw_dump(path), turn_columns(path))
            with closing(store.connect(path)):
                pass
            self.assertEqual((raw_dump(path), turn_columns(path)), first)

    def test_failed_upgrade_leaves_v1_untouched(self):
        broken = (*store.MIGRATIONS[1], "INSERT INTO no_such_table VALUES (1)")
        with store_dir() as path:
            make_v1_store(path)
            before = raw_dump(path)
            with patch.dict(store.MIGRATIONS, {1: broken}):
                with self.assertRaises(sqlite3.OperationalError):
                    store.connect(path)
            self.assertEqual(raw_dump(path), before)
            self.assertEqual(before["version"], 1)
            self.assertNotIn("content_hash", turn_columns(path))
            self.assertNotIn("ix_se_event", index_names(path))

    def test_concurrent_upgrades_both_succeed_once(self):
        with store_dir() as path:
            make_v1_store(path)
            start, errors = threading.Barrier(2), []

            def open_store():
                start.wait()
                try:
                    with closing(store.connect(path)):
                        pass
                except Exception as exc:  # 스레드 안의 예외는 본 스레드로 모은다
                    errors.append(exc)

            workers = [threading.Thread(target=open_store) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(errors, [])
            self.assertEqual(raw_dump(path)["version"], store.SCHEMA_VERSION)
            self.assertEqual(turn_columns(path).count("content_hash"), 1)


if __name__ == "__main__":
    unittest.main()
