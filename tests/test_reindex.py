"""원본 줄만으로 색인을 재생성하는 경로의 회귀. 원본 파일 없이 복원되고 반복해도 같아야 한다."""

import io
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.promptlog import cli, store
from scripts.promptlog.service import export, reindex
from tests.test_store import mixed_rows

INDEX_TABLES = ("event", "session_event", "turn")
HEX64 = re.compile(r"[0-9a-f]{64}")


def codex_native():
    item = {"id": "msg_a", "type": "UserMessage", "content": "안녕"}
    tool = {"id": "exec_b", "type": "CommandExecution", "command": "ls", "exit_code": 0}
    return [
        {"type": "session_meta", "payload": {"id": "cx-native", "cwd": "/w"}},
        *(
            {
                "type": "event_msg",
                "timestamp": f"2026-01-01T00:00:0{n}Z",
                "payload": {"type": "item_completed", "item": body},
            }
            for n, body in enumerate((item, tool))
        ),
    ]


def codex_legacy():
    return [
        {"type": "session_meta", "payload": {"id": "cx-legacy"}},
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"type": "user_message", "message": "안녕"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:01Z",
            "payload": {"type": "agent_message", "message": "반가워"},
        },
    ]


def write_source(path, rows, bom=False, broken=False):
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if broken:
        lines.insert(2, '{"type": "user", 깨진 줄')
    body = "\n".join(lines) + "\n"
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8"))
    return path


def export_all(folder, root):
    """재개 세션 쌍(s1·s2는 uuid를 공유)과 Codex native·legacy를 실제 경로로 적재한다."""
    sources = [
        write_source(folder / "s1.jsonl", mixed_rows("s1"), bom=True, broken=True),
        write_source(folder / "s2.jsonl", mixed_rows("s2")),
        write_source(folder / "native.jsonl", codex_native()),
        write_source(folder / "legacy.jsonl", codex_legacy()),
    ]
    for source in sources:
        export(source, report_root=root)
    return sources


def snapshot(root, tables):
    with closing(store.connect(store.store_path(root))) as conn:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in tables}


class ReindexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.root = folder / "reports"
        self.sources = export_all(folder, self.root)
        self.index = snapshot(self.root, INDEX_TABLES)

    def remove_sources(self):
        for source in self.sources:
            source.unlink()

    def test_index_is_restored_from_raw_lines_alone(self):
        self.remove_sources()
        with closing(store.connect(store.store_path(self.root))) as conn, conn:
            store.clear_index(conn)
        self.assertFalse(any(snapshot(self.root, INDEX_TABLES).values()))
        self.assertEqual(reindex(self.root)[0], 4)
        self.assertEqual(snapshot(self.root, INDEX_TABLES), self.index)

    def test_every_agent_format_contributes_events(self):
        linked = {row[0] for row in self.index["session_event"]}
        self.assertEqual(linked, {"s1", "s2", "cx-native", "cx-legacy"})
        ids = {row[0] for row in self.index["event"]}
        self.assertIn("codex:item:msg_a#prompt", ids)
        self.assertIn("codex:line:cx-legacy:1#prompt", ids)

    def test_resumed_pair_stays_merged(self):
        events = len(self.index["event"])
        links = len(self.index["session_event"])
        self.assertLess(events, links)
        reindex(self.root)
        self.assertEqual(len(snapshot(self.root, ("event",))["event"]), events)

    def test_repeated_reindex_is_identical(self):
        self.remove_sources()
        first = (reindex(self.root), snapshot(self.root, INDEX_TABLES))
        second = (reindex(self.root), snapshot(self.root, INDEX_TABLES))
        self.assertEqual(first, second)

    def content_hashes(self):
        with closing(store.connect(store.store_path(self.root))) as conn:
            rows = conn.execute("SELECT session_key, turn_no, content_hash FROM turn")
            return {(key, n): value for key, n, value in rows}

    def test_reindex_keeps_every_content_hash(self):
        before = self.content_hashes()
        self.assertTrue(before)
        self.assertTrue(all(HEX64.fullmatch(value or "") for value in before.values()))
        self.remove_sources()
        reindex(self.root)
        self.assertEqual(self.content_hashes(), before)
        with closing(store.connect(store.store_path(self.root))) as conn:
            for (key, n), value in before.items():
                self.assertEqual(store.fingerprint(store.turn_event_rows(conn, key, n)), value)

    def test_reindex_fills_hashes_left_null_by_upgrade(self):
        before = self.content_hashes()
        self.set_session("UPDATE turn SET content_hash = NULL")
        self.assertEqual(set(self.content_hashes().values()), {None})
        reindex(self.root)
        self.assertEqual(self.content_hashes(), before)

    def test_raw_lines_are_untouched(self):
        before = snapshot(self.root, ("session", "raw_line"))
        reindex(self.root)
        reindex(self.root)
        self.assertEqual(snapshot(self.root, ("session", "raw_line")), before)

    def test_orphan_events_are_dropped(self):
        with closing(store.connect(store.store_path(self.root))) as conn, conn:
            conn.execute("INSERT INTO event (source_id, kind) VALUES ('stale#x', 'prompt')")
        reindex(self.root)
        self.assertEqual(snapshot(self.root, INDEX_TABLES), self.index)

    def set_session(self, sql, *args):
        with closing(store.connect(store.store_path(self.root))) as conn, conn:
            conn.execute(sql, args)

    def test_failure_rolls_back_to_previous_index(self):
        self.set_session(
            "INSERT INTO session (session_key, agent, source_path) VALUES (?, ?, ?)",
            "aa-unknown", "gemini", "aa-unknown.jsonl",
        )
        with self.assertRaises(ValueError):
            reindex(self.root)
        self.assertEqual(snapshot(self.root, INDEX_TABLES), self.index)

    def test_source_path_separator_does_not_depend_on_host_os(self):
        update = "UPDATE session SET source_path = ? WHERE session_key = ?"
        self.set_session(update, r"C:\Users\u\.claude\projects\p\s1.jsonl", "s1")
        self.set_session(update, "/home/u/.claude/projects/p/s2.jsonl", "s2")
        reindex(self.root)
        self.assertEqual(snapshot(self.root, INDEX_TABLES), self.index)


class ReindexCliTests(unittest.TestCase):
    def test_cli_reindex_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reports"
            export_all(Path(tmp), root)
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["--reindex", "--output", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("재색인 세션 4", out.getvalue())

    def test_cli_reindex_failure_is_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with closing(sqlite3.connect(str(store.store_path(root)))) as raw:
                raw.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
            err = io.StringIO()
            with redirect_stderr(err):
                code = cli.main(["--reindex", "--output", str(root)])
            self.assertEqual(code, 1)
            self.assertIn("prompt-log reindex:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
