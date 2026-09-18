"""Codex content blocks and event-type spelling normalization."""

import re


def codex_text(value):
    """Codex의 문자열/콘텐츠 블록을 평문으로 바꾼다."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = (
                    block.get("text")
                    or block.get("content")
                    or block.get("summary_text")
                )
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p.strip() for p in parts if p and p.strip())
    return ""


def normalized_type(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
