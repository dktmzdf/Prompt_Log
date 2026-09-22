"""저장소 스키마·연결 설정·재사용 회귀."""

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from scripts.promptlog import store


@contextmanager
def store_dir():
    """임시 디렉터리와 그 안의 DB 경로.

    Windows는 열린 파일을 지우지 못한다. 연결을 디렉터리 정리보다 먼저 닫아야 하므로
    `addCleanup`(테스트 종료 후 실행)은 쓸 수 없다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "store.db"


def names_of(conn, kind):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
        (kind,),
    )
    return {row[0] for row in rows}


class SchemaTests(unittest.TestCase):
    def test_schema_creates_every_table_and_index(self):
        with store_dir() as path, closing(store.connect(path)) as conn:
            self.assertEqual(names_of(conn, "table"), set(store.TABLES))
            self.assertIn("ix_se_order", names_of(conn, "index"))

    def test_schema_version_is_recorded(self):
        with store_dir() as path, closing(store.connect(path)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, store.SCHEMA_VERSION)

    def test_parent_directory_is_created(self):
        with store_dir() as path:
            nested = path.parent / "nested" / "deeper" / "store.db"
            with closing(store.connect(nested)):
                self.assertTrue(nested.exists())

    def test_newer_schema_version_is_refused(self):
        with store_dir() as path:
            with closing(store.connect(path)):
                pass
            with closing(sqlite3.connect(str(path))) as raw:
                raw.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
                raw.commit()
            with self.assertRaises(ValueError):
                store.connect(path)


class ConnectionTests(unittest.TestCase):
    def test_wal_and_busy_timeout_are_set(self):
        with store_dir() as path, closing(store.connect(path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            self.assertEqual(timeout, store.BUSY_TIMEOUT_MS)

    def test_foreign_keys_are_enforced(self):
        with store_dir() as path, closing(store.connect(path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO raw_line (session_key, line_no, body) VALUES (?, ?, ?)",
                    ("없는세션", 0, "{}"),
                )

    def test_concurrent_writers_wait_instead_of_failing(self):
        with store_dir() as path:
            with closing(store.connect(path)):
                pass
            errors = []

            def writer(tag):
                try:
                    with closing(store.connect(path)) as conn:
                        for i in range(20):
                            conn.execute(
                                "INSERT INTO session (session_key, agent, source_path)"
                                " VALUES (?, ?, ?)",
                                (f"{tag}-{i}", "claude", "source.jsonl"),
                            )
                            conn.commit()
                except Exception as exc:  # 어떤 실패든 보고해야 한다
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(t,)) for t in "abc"]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual([str(e) for e in errors], [])
            with closing(store.connect(path)) as conn:
                total = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
            self.assertEqual(total, 60)


class ReuseTests(unittest.TestCase):
    def test_reopening_keeps_schema_and_data(self):
        with store_dir() as path:
            with closing(store.connect(path)) as first:
                first.execute(
                    "INSERT INTO session (session_key, agent, source_path)"
                    " VALUES (?, ?, ?)",
                    ("s1", "codex", "source.jsonl"),
                )
                first.commit()
            with closing(store.connect(path)) as second:
                self.assertEqual(names_of(second, "table"), set(store.TABLES))
                kept = second.execute(
                    "SELECT agent FROM session WHERE session_key = 's1'"
                )
                self.assertEqual(kept.fetchone()[0], "codex")

    def test_repeated_connect_does_not_reset_version(self):
        with store_dir() as path:
            for _ in range(3):
                with closing(store.connect(path)) as conn:
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    self.assertEqual(version, store.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
