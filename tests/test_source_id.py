"""이벤트 식별자 발급 회귀. 한 원본 레코드가 여러 이벤트를 내도 충돌하지 않아야 한다."""

import unittest
from collections import Counter

from scripts.promptlog.adapters import claude, codex
from scripts.promptlog.models import source_ids


def ids_of(log):
    return [event.source_id for event in log.events]


def duplicates(values):
    return sorted(name for name, count in Counter(values).items() if count > 1)


class IssueTests(unittest.TestCase):
    def test_first_of_a_kind_has_no_ordinal(self):
        issue = source_ids("claude:uuid:abc")
        self.assertEqual(issue("usage"), "claude:uuid:abc#usage")

    def test_repeats_get_ordinals(self):
        issue = source_ids("claude:uuid:abc")
        got = [issue("usage") for _ in range(3)]
        self.assertEqual(
            got,
            [
                "claude:uuid:abc#usage",
                "claude:uuid:abc#usage.1",
                "claude:uuid:abc#usage.2",
            ],
        )

    def test_kinds_do_not_interfere(self):
        issue = source_ids("p")
        self.assertEqual([issue("a"), issue("b"), issue("a")], ["p#a", "p#b", "p#a.1"])


class ClaudeSourceIdTests(unittest.TestCase):
    def test_assistant_row_kinds_are_distinct(self):
        row = {
            "type": "assistant",
            "uuid": "u-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "sessionId": "s1",
            "message": {
                "model": "claude-opus-5",
                "usage": {"input_tokens": 5, "output_tokens": 6},
                "content": [{"type": "text", "text": "안녕"}],
            },
        }
        log = claude.parse([row], "s1")
        got = ids_of(log)
        self.assertEqual(duplicates(got), [])
        self.assertEqual(
            set(got),
            {
                "claude:uuid:u-1#model",
                "claude:uuid:u-1#usage",
                "claude:uuid:u-1#answer",
                "claude:uuid:u-1#activity",
            },
        )

    def test_user_row_prompt_and_denial_are_distinct(self):
        row = {
            "type": "user",
            "uuid": "u-2",
            "timestamp": "2026-01-01T00:00:01Z",
            "sessionId": "s1",
            "toolDenialKind": "permission",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": (
                            "The user doesn't want to proceed with this tool use."
                            " To tell you how to proceed, the user said: 다르게 해"
                        ),
                    }
                ]
            },
        }
        log = claude.parse([row], "s1")
        got = ids_of(log)
        self.assertEqual(duplicates(got), [])
        self.assertIn("claude:uuid:u-2#prompt", got)
        self.assertIn("claude:uuid:u-2#denial", got)

    def test_usage_iterations_get_ordinals(self):
        row = {
            "type": "assistant",
            "uuid": "u-3",
            "timestamp": "2026-01-01T00:00:02Z",
            "sessionId": "s1",
            "message": {
                "usage": {
                    "iterations": [
                        {"input_tokens": 1, "output_tokens": 2},
                        {"input_tokens": 3, "output_tokens": 4},
                        {"input_tokens": 5, "output_tokens": 6},
                    ]
                },
                "content": [],
            },
        }
        log = claude.parse([row], "s1")
        usage = [e.source_id for e in log.events if e.kind == "usage"]
        self.assertEqual(
            usage,
            [
                "claude:uuid:u-3#usage",
                "claude:uuid:u-3#usage.1",
                "claude:uuid:u-3#usage.2",
            ],
        )

    def test_row_without_uuid_falls_back_to_line_number(self):
        row = {
            "type": "mode",
            "timestamp": "2026-01-01T00:00:03Z",
            "sessionId": "s1",
            "_line_no": 7,
        }
        log = claude.parse([row], "s1")
        self.assertEqual(ids_of(log), ["claude:line:s1:7#activity"])

    def test_same_uuid_in_two_sessions_yields_one_identity(self):
        shared = {
            "type": "assistant",
            "uuid": "shared-1",
            "timestamp": "2026-01-01T00:00:04Z",
            "message": {"content": [{"type": "text", "text": "같은 사건"}]},
        }
        first = claude.parse([{**shared, "sessionId": "old"}], "old")
        second = claude.parse([{**shared, "sessionId": "new"}], "new")
        answers = [
            [e.source_id for e in log.events if e.kind == "answer"]
            for log in (first, second)
        ]
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0], ["claude:uuid:shared-1#answer"])


class CodexSourceIdTests(unittest.TestCase):
    def native_rows(self):
        return [
            {"type": "session_meta", "payload": {"id": "cx1", "cwd": "/w"}},
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:00Z",
                "ordinal": 1,
                "payload": {
                    "type": "item_completed",
                    "item": {"id": "msg_a", "type": "UserMessage", "content": "안녕"},
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:01Z",
                "ordinal": 2,
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "id": "exec_b",
                        "type": "CommandExecution",
                        "command": "ls",
                        "exit_code": 0,
                    },
                },
            },
        ]

    def test_native_items_use_item_id(self):
        log = codex.parse(self.native_rows(), "cx1")
        got = ids_of(log)
        self.assertEqual(duplicates(got), [])
        self.assertIn("codex:item:msg_a#prompt", got)
        self.assertIn("codex:item:exec_b#tool", got)
        self.assertIn("codex:item:msg_a#activity", got)

    def test_native_item_without_id_falls_back_to_line_number(self):
        rows = self.native_rows()
        rows[1]["payload"]["item"].pop("id")
        rows[1]["_line_no"] = 4
        log = codex.parse(rows, "cx1")
        got = ids_of(log)
        self.assertEqual(duplicates(got), [])
        self.assertIn("codex:line:cx1:4#prompt", got)

    def test_legacy_rows_use_line_numbers(self):
        rows = [
            {"type": "session_meta", "payload": {"id": "cx2"}},
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:00Z",
                "_line_no": 1,
                "payload": {"type": "user_message", "message": "안녕"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-01-01T00:00:01Z",
                "_line_no": 2,
                "payload": {"type": "agent_message", "message": "반가워"},
            },
        ]
        log = codex.parse(rows, "cx2")
        got = ids_of(log)
        self.assertEqual(duplicates(got), [])
        self.assertIn("codex:line:cx2:1#prompt", got)
        self.assertIn("codex:line:cx2:2#answer", got)

    def test_every_event_carries_an_identifier(self):
        for rows, key in ((self.native_rows(), "cx1"),):
            log = codex.parse(rows, key)
            self.assertTrue(all(e.source_id for e in log.events))


if __name__ == "__main__":
    unittest.main()
