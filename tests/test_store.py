"""저장소 스키마·연결 설정·재사용·무손실 적재 회귀."""

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from datetime import datetime, timedelta

from scripts.promptlog import store
from scripts.promptlog.adapters import claude
from scripts.promptlog.assemble import assemble
from scripts.promptlog.models import Session
from scripts.promptlog.readers import read_jsonl
from scripts.promptlog.text import PRE_PROMPT


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


def local(minutes):
    """로컬 자정을 넘기는 타임스탬프. 날짜 버킷은 로컬 시각 기준이다."""
    base = datetime(2026, 1, 1, 23, 50).astimezone()
    return (base + timedelta(minutes=minutes)).isoformat()


def user(uuid, text, minutes, sid="s1", **extra):
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": sid,
        "timestamp": local(minutes),
        "message": {"content": text},
        **extra,
    }


def assistant(uuid, block, minutes, sid="s1"):
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": sid,
        "timestamp": local(minutes),
        "message": {"model": "claude-opus-5", "content": [block]},
    }


def tool_result(uuid, tool_id, output, minutes, sid="s1"):
    content = [{"type": "tool_result", "tool_use_id": tool_id, "content": output}]
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": sid,
        "timestamp": local(minutes),
        "message": {"content": content},
    }


def mixed_rows(sid="s1"):
    """첫 프롬프트 전 생각, 자정 넘김, 도구 짝, 슬래시 커맨드, 거부 지시가 섞인 세션."""
    denial = (
        "The user doesn't want to proceed with this tool use."
        " To tell you how to proceed, the user said: 다르게 해"
    )
    return [
        assistant("a0", {"type": "thinking", "thinking": "준비"}, 0, sid),
        user("u1", "첫 질문", 1, sid),
        assistant("a1", {"type": "tool_use", "id": "t1", "name": "Bash",
                         "input": {"command": "ls"}}, 2, sid),
        tool_result("r1", "t1", "file.txt", 3, sid),
        assistant("a2", {"type": "text", "text": "답 1"}, 4, sid),
        user("u2", "<command-name>/review</command-name>", 15, sid),
        assistant("a3", {"type": "text", "text": "답 2"}, 16, sid),
        assistant("a4", {"type": "tool_use", "id": "t2", "name": "Write",
                         "input": {"file_path": "a.py", "content": "x"}}, 17, sid),
        {**tool_result("r2", "t2", denial, 18, sid), "toolDenialKind": "reject"},
        assistant("a5", {"type": "thinking", "thinking": "다시"}, 19, sid),
        user("u3", "세 번째", 20, sid),
        assistant("a6", {"type": "text", "text": "답 3"}, 21, sid),
    ]


def index_rows(conn, folder, key, rows):
    """합성 행을 파일로 쓰고, 실제 경로대로 적재 -> 파싱 -> 색인한다."""
    source = folder / f"{key}.jsonl"
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    source.write_text(body, encoding="utf-8")
    log = claude.parse(read_jsonl(source), key)
    store.ingest(conn, source, log.session)
    store.index_log(conn, key, log)
    return log


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()


class EventIndexTests(unittest.TestCase):
    def test_columns_and_payload_roundtrip(self):
        big = "y" * 20_000
        rows = [
            user("u1", "질문", 1),
            assistant("a1", {"type": "tool_use", "id": "t1", "name": "Bash",
                             "input": {"command": "ls"}}, 2),
            tool_result("r1", "t1", big, 3),
            assistant("a2", {"type": "text", "text": "답"}, 4),
        ]
        with store_dir() as db, closing(store.connect(db)) as conn:
            index_rows(conn, db.parent, "s1", rows)
            tool = one(
                conn,
                "SELECT text, tool_name, status, payload FROM event WHERE source_id = ?",
                "claude:uuid:a1#tool",
            )
            answer = one(
                conn,
                "SELECT text, tool_name, status, payload FROM event WHERE source_id = ?",
                "claude:uuid:a2#answer",
            )
        self.assertEqual(len(tool[0]), 20_000)
        self.assertEqual(tool[1:3], ("Bash", "success"))
        self.assertEqual(json.loads(tool[3])["input"], {"command": "ls"})
        self.assertNotIn("output", json.loads(tool[3]))
        self.assertEqual(answer[:3], ("답", None, None))
        self.assertNotIn("text", json.loads(answer[3]))


def assemble_turns(log):
    turns = []
    for bucket in assemble(log).values():
        for prompt in bucket["prompts"]:
            text = None if prompt.get("via") == PRE_PROMPT else prompt["text"]
            turns.append((text, [kind for kind, _, _ in prompt["items"]]))
    return turns


def assigned_turns(log):
    grouped = {}
    for event, turn in zip(log.events, store.assign_turns(log.events)):
        entry = grouped.setdefault(turn, [None, []])
        if event.kind == "prompt":
            entry[0] = event.text
        elif event.kind in ("answer", "thinking", "tool"):
            entry[1].append(event.kind)
    return [(t, k) for n, (t, k) in sorted(grouped.items()) if n or k]


class TurnTests(unittest.TestCase):
    def test_turn_boundaries_match_assemble(self):
        log = claude.parse(mixed_rows(), "s1")
        self.assertGreater(len(assemble(log)), 1, "합성 로그가 자정을 넘겨야 한다")
        self.assertEqual(assigned_turns(log), assemble_turns(log))

    def test_prompts_open_turns_and_earlier_events_go_to_turn_zero(self):
        with store_dir() as db, closing(store.connect(db)) as conn:
            index_rows(conn, db.parent, "s1", mixed_rows())
            pre = one(
                conn,
                "SELECT turn_no FROM session_event WHERE source_id = ?",
                "claude:uuid:a0#thinking",
            )
            turns = conn.execute(
                "SELECT turn_no, prompt_text FROM turn WHERE session_key = 's1'"
                " ORDER BY turn_no"
            ).fetchall()
        self.assertEqual(pre[0], 0)
        self.assertEqual([t for t, _ in turns], [0, 1, 2, 3, 4])
        self.assertIsNone(turns[0][1])
        self.assertEqual(turns[1][1], "첫 질문")

    def test_reindexing_updates_events_in_place(self):
        # 행을 지웠다 다시 넣으면 rowid가 바뀐다. 제자리 갱신이어야 외래키 검사 비용이 없다.
        with store_dir() as db, closing(store.connect(db)) as conn:
            log = index_rows(conn, db.parent, "s1", mixed_rows())
            rowids = lambda: conn.execute(
                "SELECT source_id, rowid FROM event ORDER BY source_id"
            ).fetchall()
            before = rowids()
            store.index_log(conn, "s1", log)
            self.assertEqual(rowids(), before)

    def test_reindexing_is_idempotent(self):
        with store_dir() as db, closing(store.connect(db)) as conn:
            log = index_rows(conn, db.parent, "s1", mixed_rows())
            counts = lambda: [
                one(conn, f"SELECT COUNT(*) FROM {t}")[0]
                for t in ("event", "session_event", "turn")
            ]
            before = counts()
            store.index_log(conn, "s1", log)
            self.assertEqual(counts(), before)


class ResumeMergeTests(unittest.TestCase):
    def test_shared_events_are_stored_once_with_per_session_turns(self):
        # 실제 재개처럼 옛 내용 일부만 새 파일로 복사되고(o1은 빠짐) 새 프롬프트가 뒤에 붙는다.
        shared = [
            user("u9", "공유 질문", 30),
            assistant("a9", {"type": "text", "text": "공유 답"}, 31),
        ]
        old = [user("o1", "옛 질문", 25, "old")] + [{**r, "sessionId": "old"} for r in shared]
        new = [{**r, "sessionId": "new"} for r in shared] + [
            user("n1", "새 질문 1", 40, "new"),
            user("n2", "새 질문 2", 41, "new"),
        ]
        with store_dir() as db, closing(store.connect(db)) as conn:
            old_log = index_rows(conn, db.parent, "old", old)
            new_log = index_rows(conn, db.parent, "new", new)
            events = one(conn, "SELECT COUNT(*) FROM event")[0]
            links = conn.execute(
                "SELECT session_key, turn_no FROM session_event WHERE source_id = ?"
                " ORDER BY session_key",
                ("claude:uuid:a9#answer",),
            ).fetchall()
        union = {e.source_id for e in old_log.events} | {e.source_id for e in new_log.events}
        self.assertEqual(events, len(union))
        self.assertLess(events, len(old_log.events) + len(new_log.events))
        self.assertEqual(links, [("new", 1), ("old", 2)])


if __name__ == "__main__":
    unittest.main()
