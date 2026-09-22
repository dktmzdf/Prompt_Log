"""저장소 스키마·연결 설정·재사용·무손실 적재 회귀."""

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from scripts.promptlog import store
from scripts.promptlog.models import Session


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


def a_session(session_id="s1", agent="claude"):
    return Session(agent, session_id, cwd="/work/proj", branch="main")


def write_bytes(path, payload):
    path.write_bytes(payload)
    return payload


class IngestTests(unittest.TestCase):
    def assert_roundtrip(self, payload, name="source.jsonl"):
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = write_bytes(db.parent / name, payload)
            key, added = store.ingest(conn, db.parent / name, a_session())
            self.assertEqual(store.raw_text(conn, key).encode("utf-8"), source)
            return added

    def test_lines_roundtrip_byte_for_byte(self):
        rows = [{"type": "user", "uuid": f"u{n}", "n": n} for n in range(5)]
        payload = ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")
        self.assertEqual(self.assert_roundtrip(payload), 5)

    def test_missing_trailing_newline_roundtrips(self):
        payload = b'{"a": 1}\n{"b": 2}'
        self.assert_roundtrip(payload)

    def test_crlf_endings_roundtrip(self):
        payload = b'{"a": 1}\r\n{"b": 2}\r\n'
        self.assert_roundtrip(payload)

    def test_byte_order_mark_is_preserved(self):
        payload = b'\xef\xbb\xbf{"a": 1}\n'
        self.assert_roundtrip(payload)

    def test_non_ascii_content_roundtrips(self):
        payload = '{"text": "한국어와 이모지 🙂"}\n'.encode("utf-8")
        self.assert_roundtrip(payload)

    def test_unparseable_line_is_preserved(self):
        payload = b'{"a": 1}\n}\n{"b": 2}\n'
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = db.parent / "broken.jsonl"
            write_bytes(source, payload)
            key, _ = store.ingest(conn, source, a_session())
            bodies = conn.execute(
                "SELECT body FROM raw_line WHERE session_key = ? ORDER BY line_no",
                (key,),
            ).fetchall()
            self.assertEqual(bodies[1][0], "}\n")
            self.assertEqual(store.raw_text(conn, key).encode("utf-8"), payload)

    def test_content_over_four_kilobytes_is_not_truncated(self):
        big = "x" * 20_000
        payload = (json.dumps({"type": "user", "output": big}) + "\n").encode("utf-8")
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = db.parent / "big.jsonl"
            write_bytes(source, payload)
            key, _ = store.ingest(conn, source, a_session())
            stored = json.loads(store.raw_text(conn, key))
            self.assertEqual(len(stored["output"]), 20_000)
            self.assertEqual(store.raw_text(conn, key).encode("utf-8"), payload)

    def test_session_metadata_is_recorded(self):
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = db.parent / "meta.jsonl"
            write_bytes(source, b'{"a": 1}\n')
            key, _ = store.ingest(conn, source, a_session("abc", "codex"), "t0", "t9")
            row = conn.execute(
                "SELECT agent, cwd, branch, ts_first, ts_last, ingested_lines"
                " FROM session WHERE session_key = ?",
                (key,),
            ).fetchone()
            self.assertEqual(key, "abc")
            self.assertEqual(row[:5], ("codex", "/work/proj", "main", "t0", "t9"))
            self.assertEqual(row[5], 1)


class IncrementalTests(unittest.TestCase):
    def test_only_appended_lines_are_written(self):
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = db.parent / "grow.jsonl"
            write_bytes(source, b'{"a": 1}\n{"b": 2}\n')
            key, first = store.ingest(conn, source, a_session())
            self.assertEqual(first, 2)

            with open(source, "ab") as stream:
                stream.write(b'{"c": 3}\n{"d": 4}\n')
            _, second = store.ingest(conn, source, a_session())

            self.assertEqual(second, 2)
            total = conn.execute(
                "SELECT COUNT(*) FROM raw_line WHERE session_key = ?", (key,)
            ).fetchone()[0]
            self.assertEqual(total, 4)
            self.assertEqual(
                store.raw_text(conn, key).encode("utf-8"), source.read_bytes()
            )

    def test_reingesting_unchanged_file_writes_nothing(self):
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = db.parent / "same.jsonl"
            write_bytes(source, b'{"a": 1}\n{"b": 2}\n')
            key, _ = store.ingest(conn, source, a_session())
            before = store.raw_text(conn, key)

            _, again = store.ingest(conn, source, a_session())

            self.assertEqual(again, 0)
            total = conn.execute(
                "SELECT COUNT(*) FROM raw_line WHERE session_key = ?", (key,)
            ).fetchone()[0]
            self.assertEqual(total, 2)
            self.assertEqual(store.raw_text(conn, key), before)

    def test_partial_final_line_is_completed_on_next_ingest(self):
        with store_dir() as db, closing(store.connect(db)) as conn:
            source = db.parent / "partial.jsonl"
            write_bytes(source, b'{"a": 1}\n{"b": ')
            key, _ = store.ingest(conn, source, a_session())
            self.assertEqual(
                conn.execute(
                    "SELECT ingested_lines FROM session WHERE session_key = ?", (key,)
                ).fetchone()[0],
                1,
            )

            with open(source, "ab") as stream:
                stream.write(b"2}\n")
            store.ingest(conn, source, a_session())

            total = conn.execute(
                "SELECT COUNT(*) FROM raw_line WHERE session_key = ?", (key,)
            ).fetchone()[0]
            self.assertEqual(total, 2)
            self.assertEqual(
                store.raw_text(conn, key).encode("utf-8"), source.read_bytes()
            )


if __name__ == "__main__":
    unittest.main()
