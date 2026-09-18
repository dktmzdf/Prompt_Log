"""Behavioral contracts for both adapters and the shared event pipeline."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scripts.promptlog.adapters import claude, codex
from scripts.promptlog.assemble import assemble, project_name
from scripts.promptlog.models import Event, Session, SessionLog
from scripts.promptlog.readers import detect_agent, read_jsonl
from scripts.promptlog.render import present
from scripts.promptlog.text import HEAD, TAIL, truncate, truncate_input


def at(minutes=0):
    return (
        datetime(2026, 1, 1, 23, 50).astimezone() + timedelta(minutes=minutes)
    ).isoformat()


def native(item, ts=None):
    return {
        "type": "event_msg",
        "timestamp": ts or at(),
        "payload": {"type": "item_completed", "item": item},
    }


def usage(total=None, last=None, ts=None):
    info = {}
    if total is not None:
        info["total_token_usage"] = total
    if last is not None:
        info["last_token_usage"] = last
    return {
        "type": "event_msg",
        "timestamp": ts or at(1),
        "payload": {"type": "token_count", "info": info},
    }


class CommonCoreTests(unittest.TestCase):
    def test_same_turns_from_both_adapters(self):
        a = claude.parse(
            [
                {
                    "type": "user",
                    "timestamp": at(),
                    "cwd": "/work/project",
                    "message": {"content": "start"},
                },
                {
                    "type": "assistant",
                    "timestamp": at(20),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Bash",
                                "input": {"command": "pwd", "cwd": ""},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "timestamp": at(21),
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": "/work/project",
                            }
                        ]
                    },
                },
                {"type": "user", "timestamp": at(30), "message": {"content": "next"}},
                {
                    "type": "assistant",
                    "timestamp": at(31),
                    "message": {"content": [{"type": "text", "text": "done"}]},
                },
            ]
        )
        b = codex.parse(
            [
                {"type": "session_meta", "payload": {"cwd": "/work/project"}},
                native({"type": "UserMessage", "content": "start"}),
                native(
                    {
                        "type": "CommandExecution",
                        "command": "pwd",
                        "status": "completed",
                        "aggregated_output": "/work/project",
                    },
                    at(20),
                ),
                native({"type": "UserMessage", "content": "next"}, at(30)),
                native({"type": "AgentMessage", "content": "done"}, at(31)),
            ]
        )
        first, second = assemble(a), assemble(b)
        self.assertEqual(list(first), ["2026-01-01", "2026-01-02"])
        self.assertEqual(list(first), list(second))
        for day in first:
            self.assertEqual(first[day]["events"], second[day]["events"])
            self.assertEqual(first[day]["prompts"], second[day]["prompts"])
            for field in ("tools", "files", "tokens", "project"):
                self.assertEqual(
                    first[day]["stats"][field], second[day]["stats"][field]
                )
        self.assertEqual(len(first["2026-01-01"]["events"]), 2)

    def test_presentation_does_not_truncate_adapter_or_core(self):
        text = "한글🙂" * 3000
        event = Event(
            "tool",
            at(),
            name="Write",
            input={"changes": [{"content": text}]},
            output=text,
        )
        log = SessionLog(
            Session("claude", "test"), [event, Event("thinking", at(1), text=text)]
        )
        raw = assemble(log)
        shown = present(raw)
        day = next(iter(raw))
        self.assertEqual(raw[day]["events"][0]["output"], text)
        self.assertEqual(event.input["changes"][0]["content"], text)
        self.assertNotEqual(shown[day]["events"][0]["output"], text)
        self.assertIn("changes[0].content", shown[day]["events"][0]["truncated_input"])
        self.assertNotEqual(shown[day]["prompts"][0]["items"][1][1], text)

    def test_unconfirmed_changes_not_counted(self):
        for status in ("unknown", "in_progress", "failed", "success"):
            with self.subTest(status=status):
                bucket = next(
                    iter(
                        assemble(
                            SessionLog(
                                Session("codex", "test"),
                                [
                                    Event(
                                        "tool",
                                        at(),
                                        name="apply_patch",
                                        files=("app.py",),
                                        status=status,
                                    )
                                ],
                            )
                        ).values()
                    )
                )
                self.assertEqual(bool(bucket["stats"]["files"]), status == "success")
                self.assertEqual(
                    bucket["events"][0]["ok"],
                    {"success": True, "failed": False}.get(status),
                )
                self.assertEqual(bucket["stats"]["tools"]["apply_patch"], 1)

    def test_path_name_is_host_independent(self):
        self.assertEqual(
            project_name(r"C:\Work\whisper.cpp-master"), "whisper.cpp-master"
        )
        self.assertEqual(
            project_name("/work/whisper.cpp-master/"), "whisper.cpp-master"
        )

    def test_metadata_without_timestamp_does_not_create_fake_day(self):
        log = SessionLog(
            Session("claude", "test"),
            [
                Event("activity"),
                Event("model", model="m"),
                Event("prompt", at(), text="hello"),
            ],
        )
        buckets = assemble(log)
        self.assertEqual(list(buckets), ["2026-01-01"])
        self.assertEqual(buckets["2026-01-01"]["stats"]["models"]["m"], 1)


class TokenTests(unittest.TestCase):
    def test_total_after_last_only_does_not_charge_it_twice(self):
        tokens = self.tokens(
            [usage(last={"input_tokens": 100}), usage({"input_tokens": 150})]
        )
        self.assertEqual(tokens["input_tokens"], 150)

    def test_missing_counter_in_partial_total_preserves_baseline(self):
        tokens = self.tokens(
            [
                usage({"input_tokens": 100, "output_tokens": 10}),
                usage({"input_tokens": 120}),
                usage({"input_tokens": 130, "output_tokens": 20}),
            ]
        )
        self.assertEqual(tokens["output_tokens"], 20)

    def tokens(self, rows):
        buckets = assemble(
            codex.parse([native({"type": "UserMessage", "content": "hello"}), *rows])
        )
        return next(iter(buckets.values()))["stats"]["tokens"]

    def test_repeated_totals_are_not_repeated_charges(self):
        count = usage(
            {"input_tokens": 201, "output_tokens": 10, "cached_input_tokens": 40},
            {"input_tokens": 201},
        )
        tokens = self.tokens([count, count, count])
        self.assertEqual(tokens["input_tokens"], 201)
        self.assertEqual(tokens["output_tokens"], 10)
        self.assertEqual(tokens["cache_read_input_tokens"], 40)

    def test_total_delta_not_last_sample_is_authoritative(self):
        tokens = self.tokens(
            [
                usage({"input_tokens": 100}, {"input_tokens": 100}),
                usage({"input_tokens": 150}, {"input_tokens": 999}),
            ]
        )
        self.assertEqual(tokens["input_tokens"], 150)

    def test_last_only_preserves_equal_legitimate_usage(self):
        self.assertEqual(
            self.tokens(
                [usage(last={"input_tokens": 100}), usage(last={"input_tokens": 100})]
            )["input_tokens"],
            200,
        )

    def test_cumulative_reset_starts_new_epoch(self):
        self.assertEqual(
            self.tokens(
                [
                    usage({"input_tokens": 100}),
                    usage({"input_tokens": 20}),
                    usage({"input_tokens": 30}),
                ]
            )["input_tokens"],
            130,
        )

    def test_cumulative_state_crosses_day_boundary(self):
        log = codex.parse(
            [
                native({"type": "UserMessage", "content": "first"}),
                usage({"input_tokens": 100}),
                native({"type": "UserMessage", "content": "next"}, at(30)),
                usage({"input_tokens": 130}, ts=at(31)),
            ]
        )
        buckets = assemble(log)
        self.assertEqual(
            [b["stats"]["tokens"]["input_tokens"] for b in buckets.values()], [100, 30]
        )

    def test_claude_iterations_are_incremental(self):
        log = claude.parse(
            [
                {
                    "type": "assistant",
                    "timestamp": at(),
                    "message": {
                        "content": [{"type": "text", "text": "done"}],
                        "usage": {
                            "input_tokens": 0,
                            "iterations": [{"input_tokens": 4}, {"input_tokens": 7}],
                        },
                    },
                }
            ]
        )
        self.assertEqual(
            next(iter(assemble(log).values()))["stats"]["tokens"]["input_tokens"], 11
        )


class AdapterTests(unittest.TestCase):
    def test_missing_claude_result_is_unknown(self):
        log = claude.parse(
            [
                {
                    "type": "assistant",
                    "timestamp": at(),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "absent",
                                "name": "Write",
                                "input": {"file_path": "app.py"},
                            }
                        ]
                    },
                }
            ]
        )
        event = next(e for e in log.events if e.kind == "tool")
        self.assertEqual(event.status, "unknown")
        self.assertEqual(event.output, "")

    def test_native_status_precedence(self):
        cases = [
            ({}, "unknown"),
            ({"status": "in_progress"}, "in_progress"),
            ({"status": "completed"}, "success"),
            ({"status": "completed", "exit_code": 1}, "failed"),
            ({"status": "completed", "error": "bad"}, "failed"),
            ({"status": "completed", "success": False}, "failed"),
            ({"exit_code": 0}, "success"),
        ]
        for data, status in cases:
            with self.subTest(data=data):
                self.assertEqual(
                    codex.native_event(
                        {"type": "CommandExecution", **data}, at()
                    ).status,
                    status,
                )

    def test_all_native_tool_mappings(self):
        cases = [
            (
                {"type": "McpToolCall", "server": "s", "tool": "t", "result": {"x": 1}},
                "mcp__s__t",
            ),
            (
                {
                    "type": "DynamicToolCall",
                    "namespace": "n",
                    "tool": "t",
                    "arguments": "text",
                },
                "n.t",
            ),
            (
                {
                    "type": "CollabAgentToolCall",
                    "tool": "spawn_agent",
                    "prompt": "work",
                },
                "spawn_agent",
            ),
            ({"type": "WebSearch", "query": "docs"}, "WebSearch"),
            ({"type": "FileChange", "changes": [{"path": "a.py"}]}, "apply_patch"),
        ]
        for item, name in cases:
            with self.subTest(item=item):
                event = codex.native_event(item, at())
                self.assertEqual(event.name, name)
                self.assertIsInstance(event.input, dict)

    def test_unknown_native_item_keeps_safe_fields(self):
        event = codex.native_event(
            {
                "type": "NewItem",
                "id": "private-id",
                "raw_content": "raw",
                "encrypted_content": "secret",
                "custom": {"path": "keep"},
                "content": "visible",
            },
            at(),
        )
        self.assertEqual(event.name, "NewItem")
        self.assertEqual(event.input, {"type": "NewItem", "custom": {"path": "keep"}})
        self.assertEqual(event.output, "visible")

    def test_legacy_multiline_and_fifo(self):
        body = "command: echo first\n  echo second\ndescription: multiline"

        def row(message):
            return {
                "type": "event_msg",
                "timestamp": at(),
                "payload": {"type": "agent_message", "message": message},
            }

        log = codex.parse(
            [
                row(
                    f"[external_agent_tool_call: Bash]\n{body}\n[/external_agent_tool_call]"
                ),
                row(
                    "[external_agent_tool_call: Read]\npath: a.py\n[/external_agent_tool_call]"
                ),
                row(
                    "[external_agent_tool_result]\nfirst result\n[/external_agent_tool_result]"
                ),
                row(
                    "[external_agent_tool_result]\nsecond result\n[/external_agent_tool_result]"
                ),
            ]
        )
        events = [e for e in log.events if e.kind == "tool"]
        self.assertEqual(events[0].input["command"], "echo first\n  echo second")
        self.assertEqual(events[0].input["_raw"], body)
        self.assertEqual([e.output for e in events], ["first result", "second result"])
        self.assertTrue(all(e.status == "unknown" for e in events))

    def test_native_ignores_mirrors_and_environment(self):
        log = codex.parse(
            [
                native({"type": "UserMessage", "content": "hello"}),
                native(
                    {
                        "type": "UserMessage",
                        "content": "<environment_context>internal</environment_context>",
                    }
                ),
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "duplicate"},
                },
                {
                    "type": "response_item",
                    "payload": {"role": "user", "content": "duplicate"},
                },
            ]
        )
        self.assertEqual([e.text for e in log.events if e.kind == "prompt"], ["hello"])


class TextAndReaderTests(unittest.TestCase):
    def test_utf8_limits_count_bytes_without_broken_characters(self):
        for text in ("A" * 9000, "한글" * 3000, "🙂" * 2500):
            with self.subTest(text=text[:4]):
                shown, cut = truncate(text)
                head, marker, tail = shown.split("\n")
                self.assertLessEqual(len(head.encode("utf-8")), HEAD)
                self.assertLessEqual(len(tail.encode("utf-8")), TAIL)
                self.assertEqual(
                    cut,
                    len(text.encode("utf-8"))
                    - len(head.encode("utf-8"))
                    - len(tail.encode("utf-8")),
                )
                self.assertNotIn("\ufffd", shown)
                self.assertIn(f"{cut:,}바이트", marker)
        self.assertEqual(truncate("🙂" * 1024), ("🙂" * 1024, 0))

    def test_nested_arrays_are_truncated_without_mutation(self):
        inp = {
            "command": ["powershell", "한" * 3000],
            "changes": [{"diff": "x" * 5000}],
        }
        out, cuts = truncate_input(inp)
        self.assertEqual(set(cuts), {"command[1]", "changes[0].diff"})
        self.assertEqual(inp["changes"][0]["diff"], "x" * 5000)
        self.assertNotEqual(inp, out)

    def test_bom_malformed_and_nonobject_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.jsonl"
            path.write_text(
                "\ufeff"
                + json.dumps({"type": "session_meta", "payload": {"id": "test"}})
                + "\n[]\nnull\n{partial",
                encoding="utf-8",
            )
            rows = read_jsonl(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(detect_agent(path, rows), "codex")

    def test_unknown_source_is_explicit_error(self):
        with self.assertRaises(ValueError):
            detect_agent("unknown.jsonl", [])
