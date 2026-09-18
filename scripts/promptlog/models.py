"""Unabridged adapter output; presentation never mutates these records."""

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


@dataclass
class SessionLog:
    session: Session
    events: list[Event] = field(default_factory=list)
