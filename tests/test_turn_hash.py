"""턴 지문 회귀. 내용이 바뀐 턴만 지문이 바뀌고, 같은 입력은 언제나 같은 지문을 낸다."""

import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.promptlog import store
from tests.test_schema_upgrade import make_v1_store
from tests.test_store import assistant, index_rows, mixed_rows, tool_result, user

HEX64 = re.compile(r"[0-9a-f]{64}")


def hashes(conn, key="s1"):
    rows = conn.execute(
        "SELECT turn_no, content_hash FROM turn WHERE session_key = ? ORDER BY turn_no",
        (key,),
    )
    return dict(rows.fetchall())


def seqs(conn, key="s1"):
    rows = conn.execute(
        "SELECT source_id, seq FROM session_event WHERE session_key = ?", (key,)
    )
    return dict(rows.fetchall())


# mixed_rows의 프롬프트는 넷이다: 일반 질문, 슬래시 커맨드, 도구 거부 응답, 일반 질문.
# 그래서 턴은 첫 프롬프트 이전(0)과 1~4다.
MIXED_TURNS = [0, 1, 2, 3, 4]


class Indexed:
    """같은 파일에 행을 늘려 가며 적재한다. 임시 디렉터리와 연결을 함께 정리한다."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.conn = store.connect(self.folder / "store.db")

    def index(self, rows, key="s1"):
        index_rows(self.conn, self.folder, key, rows)
        return hashes(self.conn, key)

    def close(self):
        self.conn.close()
        self.tmp.cleanup()


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.db = Indexed()
        self.addCleanup(self.db.close)

    def test_same_session_indexed_twice_gives_identical_hex_hashes(self):
        first = self.db.index(mixed_rows())
        second = self.db.index(mixed_rows())
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), MIXED_TURNS)
        self.assertTrue(all(HEX64.fullmatch(value) for value in first.values()))

    def test_turns_have_distinct_hashes(self):
        got = self.db.index(mixed_rows())
        self.assertEqual(len(set(got.values())), len(got))

    def test_appended_turn_keeps_old_hashes_and_adds_a_new_one(self):
        before = self.db.index(mixed_rows())
        more = [user("u4", "네 번째", 30), assistant("a7", {"type": "text", "text": "답 4"}, 31)]
        after = self.db.index(mixed_rows() + more)
        self.assertEqual({n: after[n] for n in before}, before)
        self.assertEqual(sorted(after), MIXED_TURNS + [5])
        self.assertRegex(after[5], HEX64)

    def test_tool_result_changes_only_its_own_turn(self):
        tool = {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "ls"}}
        rows = [
            user("u1", "첫 질문", 1),
            assistant("a1", {"type": "text", "text": "답 1"}, 2),
            user("u2", "둘째 질문", 3),
            assistant("a2", tool, 4),
        ]
        before = self.db.index(rows)
        after = self.db.index(rows + [tool_result("r9", "t9", "file.txt", 5)])
        self.assertEqual(sorted(before), [1, 2])
        self.assertEqual(after[1], before[1])
        self.assertNotEqual(after[2], before[2])

    def test_event_inserted_before_first_prompt_only_changes_turn_zero(self):
        before = self.db.index(mixed_rows())
        before_seq = seqs(self.db.conn)
        early = assistant("a-early", {"type": "thinking", "thinking": "먼저"}, -5)
        after = self.db.index(mixed_rows() + [early])
        after_seq = seqs(self.db.conn)
        shifted = [sid for sid, seq in before_seq.items() if after_seq[sid] != seq]
        self.assertGreater(len(shifted), 0)  # 앞에 끼어들어 뒤의 순번이 실제로 밀렸다
        self.assertNotEqual(after[0], before[0])
        later = MIXED_TURNS[1:]
        self.assertEqual({n: after[n] for n in later}, {n: before[n] for n in later})

    def test_turn_numbers_do_not_shift_when_early_event_is_inserted(self):
        self.db.index(mixed_rows())
        self.db.index(mixed_rows() + [assistant("a-early", {"type": "text", "text": "x"}, -5)])
        prompts = self.db.conn.execute(
            "SELECT turn_no, prompt_text FROM turn WHERE prompt_text IS NOT NULL ORDER BY 1"
        ).fetchall()
        self.assertEqual([n for n, _ in prompts], MIXED_TURNS[1:])


TOOL = {"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "ls"}}


def original_rows():
    """세션 A: 첫 턴은 답변으로 끝나고, 둘째 턴은 도구 호출만 있고 결과가 없다."""
    return [
        user("u1", "첫 질문", 1, "A"),
        assistant("a1", {"type": "text", "text": "답 1"}, 2, "A"),
        user("u2", "둘째 질문", 3, "A"),
        assistant("a2", TOOL, 4, "A"),
    ]


def resumed_rows(extra=()):
    """세션 B: A의 행을 그대로 복사하고(uuid 보존, sessionId만 다름) 이어서 진행한다."""
    return [{**row, "sessionId": "B"} for row in original_rows()] + list(extra)


def recomputed(conn):
    """모든 턴의 지문을 `event` 표에서 다시 계산한다. 저장된 값과 같아야 한다."""
    turns = conn.execute("SELECT session_key, turn_no FROM turn").fetchall()
    return {
        (key, n): store.fingerprint(store.turn_event_rows(conn, key, n)) for key, n in turns
    }


def stored(conn):
    rows = conn.execute("SELECT session_key, turn_no, content_hash FROM turn").fetchall()
    return {(key, n): value for key, n, value in rows}


class SharedEventTests(unittest.TestCase):
    def setUp(self):
        self.db = Indexed()
        self.addCleanup(self.db.close)

    def test_result_added_in_resumed_session_updates_original_turn(self):
        before = self.db.index(original_rows(), "A")
        self.db.index(resumed_rows([tool_result("r9", "t9", "file.txt", 5, "B")]), "B")
        after = hashes(self.db.conn, "A")
        self.assertEqual(after[1], before[1])  # 공유 이벤트가 바뀌지 않은 턴
        self.assertNotEqual(after[2], before[2])  # 공유 도구 이벤트에 결과가 붙은 턴
        self.assertEqual(stored(self.db.conn), recomputed(self.db.conn))

    def test_fingerprint_matches_event_table_after_any_indexing_order(self):
        self.db.index(original_rows(), "A")
        self.db.index(resumed_rows([tool_result("r9", "t9", "file.txt", 5, "B")]), "B")
        self.assertEqual(stored(self.db.conn), recomputed(self.db.conn))
        self.db.index(original_rows(), "A")  # A를 다시 적재하면 공유 행이 A 내용으로 돌아간다
        self.assertEqual(stored(self.db.conn), recomputed(self.db.conn))

    def test_unrelated_session_is_untouched(self):
        other = [user("c1", "관계없는 질문", 1, "C"), assistant("c2", TOOL, 2, "C")]
        other = [{**row, "uuid": "c-" + row["uuid"]} for row in other]
        untouched = self.db.index(other, "C")
        self.db.index(original_rows(), "A")
        self.db.index(resumed_rows([tool_result("r9", "t9", "file.txt", 5, "B")]), "B")
        self.assertEqual(hashes(self.db.conn, "C"), untouched)

    def test_session_without_shared_events_does_not_rewrite_other_hashes(self):
        self.db.index(original_rows(), "A")
        changes = self.db.conn.total_changes
        self.assertEqual(store.shared_turns(self.db.conn, "A"), [])
        self.assertEqual(self.db.conn.total_changes, changes)

    def test_shared_lookup_uses_the_source_id_index(self):
        self.db.index(original_rows(), "A")
        plan = self.db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT DISTINCT o.session_key, o.turn_no FROM session_event me"
            " JOIN session_event o ON o.source_id = me.source_id"
            " AND o.session_key != me.session_key WHERE me.session_key = 'A'"
        ).fetchall()
        self.assertIn("ix_se_event", " ".join(str(row) for row in plan))


class BackfillTests(unittest.TestCase):
    def test_reindexing_an_upgraded_session_fills_null_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            make_v1_store(path)
            with closing(store.connect(path)) as conn:
                self.assertEqual(hashes(conn), {1: None})
                index_rows(conn, Path(tmp), "s1", mixed_rows())
                got = hashes(conn)
            self.assertEqual(sorted(got), MIXED_TURNS)
            self.assertTrue(all(HEX64.fullmatch(value) for value in got.values()))


if __name__ == "__main__":
    unittest.main()
