#!/usr/bin/env python3
"""Stable CLI/hook entry point. Implementation lives in promptlog/.

Run: python scripts/export.py <session.jsonl> | --all | --selftest
With no paths, read a Stop-hook payload from stdin.
"""

if __package__:
    from .promptlog.cli import main, selftest
    from .promptlog.service import load, load_codex, build, build_codex, export
    from .promptlog.readers import detect_agent
    from .promptlog.render import render_md, render_jsonl
    from .promptlog.storage import session_hash
    from .promptlog.text import summarize_tool, HEAD, TAIL, PRE_PROMPT
    from .promptlog.adapters.claude_content import DENIAL_MARKER, DENIAL_PREFIX
    from .promptlog.timing import hhmmss
else:
    from promptlog.cli import main, selftest
    from promptlog.service import load, load_codex, build, build_codex, export
    from promptlog.readers import detect_agent
    from promptlog.render import render_md, render_jsonl
    from promptlog.storage import session_hash
    from promptlog.text import summarize_tool, HEAD, TAIL, PRE_PROMPT
    from promptlog.adapters.claude_content import DENIAL_MARKER, DENIAL_PREFIX
    from promptlog.timing import hhmmss


if __name__ == "__main__":
    raise SystemExit(main())
