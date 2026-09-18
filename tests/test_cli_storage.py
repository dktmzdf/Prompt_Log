"""Real subprocess, installation-layout and filesystem regressions."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.promptlog import cli, storage
from scripts.promptlog.service import export

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "export.py"


def source_at(path, cwd="/work/whisper.cpp-master"):
    rows = [
        {"type": "session_meta", "payload": {"id": "test-session", "cwd": cwd}},
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "UserMessage", "content": "hello"},
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def run(*args, cwd=None, input="", entry=ENTRY):
    return subprocess.run(
        [sys.executable, "-B", str(entry), *map(str, args)],
        cwd=cwd,
        input=input,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=30,
    )


class StorageTests(unittest.TestCase):
    def test_dotted_name_and_idempotence_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = source_at(root / "source.jsonl")
            original = source.read_bytes()
            written = export(source, report_root=root / "reports")
            base = written[0][0]
            self.assertIn("whisper.cpp-master-2026-01-01-", base.name)
            files = [Path(str(base) + ext) for ext in (".md", ".jsonl")]
            before = [p.read_bytes() for p in files]
            self.assertEqual(export(source, report_root=root / "reports"), written)
            self.assertEqual([p.read_bytes() for p in files], before)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(len(list((root / "reports").rglob("*.jsonl"))), 1)

    def test_failed_replace_leaves_previous_report_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.md"
            target.write_text("original", encoding="utf-8")
            with patch.object(
                storage.os, "replace", side_effect=OSError("test failure")
            ):
                with self.assertRaises(OSError):
                    storage.atomic_write(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_slug_is_a_portable_segment(self):
        for value in ("..", "CON", "COM1.txt", "a:b/c\\d?", ""):
            with self.subTest(value=value):
                slug = storage.safe_slug(value)
                self.assertNotIn(slug, ("", ".", ".."))
                self.assertFalse(any(c in slug for c in '<>:"/\\|?*'))


class CLITests(unittest.TestCase):
    def test_backfill_continues_but_returns_failure(self):
        with patch.object(
            cli,
            "discover",
            return_value=[(Path("bad.jsonl"), "codex"), (Path("good.jsonl"), "codex")],
        ) as discover:
            with patch.object(
                cli, "export", side_effect=[OSError("bad"), []]
            ) as exporter:
                with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--all", "--agent", "codex"]), 1)
                discover.assert_called_once_with("codex")
                self.assertEqual(exporter.call_count, 2)

    def test_cli_failure_is_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp) / "missing.jsonl")
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_hook_failure_never_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            for payload in (
                "{bad",
                "[]",
                json.dumps({"transcript_path": str(Path(tmp) / "missing.jsonl")}),
            ):
                with self.subTest(payload=payload):
                    result = run(input=payload)
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(json.loads(result.stdout), {"continue": True})
                    self.assertTrue(result.stderr)

    def test_hook_success_with_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = source_at(root / "source.jsonl")
            result = run(
                "--output",
                root / "reports",
                input="\ufeff" + json.dumps({"transcript_path": str(source)}),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"continue": True})
            self.assertEqual(len(list((root / "reports").rglob("*.md"))), 1)

    def test_selftest_exception_is_nonzero(self):
        with patch.object(
            cli, "selftest", side_effect=AssertionError("expected test failure")
        ), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--selftest"]), 1)

    def test_copied_package_runs_from_unrelated_directory_and_selftest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "plugin with spaces"
            shutil.copytree(
                ROOT / "scripts",
                package / "scripts",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            entry = package / "scripts" / "export.py"
            source = source_at(root / "source.jsonl")
            result = run(source, "--output", root / "reports", cwd=root, entry=entry)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list((root / "reports").rglob("*.md"))), 1)
            tests = package / "tests"
            tests.mkdir()
            (tests / "__init__.py").write_text("", encoding="utf-8")
            test = tests / "test_failure.py"
            test.write_text(
                "import unittest\nclass Failure(unittest.TestCase):\n def test_failure(self):\n  self.fail('intentional')\n",
                encoding="utf-8",
            )
            failed = run("--selftest", cwd=root, entry=entry)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertIn("intentional", failed.stderr)
