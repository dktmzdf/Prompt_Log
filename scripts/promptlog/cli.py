"""CLI errors fail visibly; Stop hooks report errors without blocking the agent."""

import argparse
import json
import sys
import unittest
from pathlib import Path

from .readers import AGENTS, discover
from .service import export
from .storage import REPORT_ROOT


def selftest():
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests"), top_level_dir=str(root)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun else 1


def run_hook(args):
    try:
        payload = json.loads((sys.stdin.read() or "{}").lstrip("\ufeff"))
        if not isinstance(payload, dict):
            raise ValueError("훅 입력은 JSON 객체여야 합니다")
        path = payload.get("transcript_path")
        if path:
            export(Path(path), args.agent, args.output)
    except Exception as exc:
        print(f"prompt-log hook: {exc}", file=sys.stderr)
    # Both hook consumers accept continue; never return a blocking decision.
    print(json.dumps({"continue": True}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Claude/Codex 세션 로그를 중립 리포트로 내보냅니다"
    )
    parser.add_argument("paths", nargs="*", type=Path, help="내보낼 세션 JSONL")
    parser.add_argument("--all", action="store_true", help="발견된 세션을 모두 백필")
    parser.add_argument("--agent", choices=AGENTS)
    parser.add_argument("--selftest", action="store_true", help="회귀검사 실행")
    parser.add_argument("--output", type=Path, default=REPORT_ROOT, help="출력 루트")
    args = parser.parse_args(argv)
    if args.selftest:
        try:
            return selftest()
        except Exception as exc:
            print(f"prompt-log selftest: {exc}", file=sys.stderr)
            return 1
    if not args.all and not args.paths:
        return run_hook(args)
    targets = (
        discover(args.agent) if args.all else ((p, args.agent) for p in args.paths)
    )
    failed = False
    for target, agent in targets:
        try:
            done = export(target, agent, args.output)
        except Exception as exc:
            print(f"건너뜀 {target.name}: {exc}", file=sys.stderr)
            failed = True
            continue
        for base, prompts, tools in done:
            print(f"{base}.{{md,jsonl}}  프롬프트 {prompts} · 도구 {tools}")
    return int(failed)
