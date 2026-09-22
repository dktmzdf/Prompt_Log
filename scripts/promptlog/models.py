"""Unabridged adapter output; presentation never mutates these records."""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["success", "failed", "in_progress", "unknown"]
Kind = Literal[
    "prompt", "answer", "thinking", "tool", "usage", "model", "denial", "activity"
]


@dataclass
class Session:
    agent: str
    session_id: str
    cwd: str = ""
    branch: str = ""
    client: str = ""
    version: str = ""
    history_mode: str = ""


@dataclass
class Event:
    kind: Kind
    ts: str = ""
    text: str = ""
    via: str | None = None
    phase: str | None = None
    name: str = ""
    input: Any = field(default_factory=dict)
    output: str = ""
    status: Status = "unknown"
    files: tuple[str, ...] = ()
    summary: str | None = None
    model: str = ""
    tokens: dict[str, int] = field(default_factory=dict)
    usage_mode: Literal["incremental", "cumulative"] = "incremental"
    usage_stream: str = "default"
    source_id: str = ""


@dataclass
class SessionLog:
    session: Session
    events: list[Event] = field(default_factory=list)


def source_ids(prefix):
    """한 원본 레코드가 내는 이벤트마다 충돌하지 않는 `source_id`를 발급한다.

    같은 kind가 두 번 이상 나오는 경우(`usage` iterations, 블록이 여럿인 메시지)에만
    순번이 붙는다. 첫 번째는 순번 없이 나가므로 흔한 경우의 id가 안정적이다.
    """
    seen = Counter()

    def issue(kind):
        nth = seen[kind]
        seen[kind] += 1
        return f"{prefix}#{kind}" if not nth else f"{prefix}#{kind}.{nth}"

    return issue
