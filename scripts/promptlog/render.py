"""Presentation functions shared by both source adapters."""

import json
import re
from copy import deepcopy
from .text import PRE_PROMPT, truncate, truncate_input
from .timing import hhmmss, fmt_span


def present(buckets):
    """Apply report-only limits without mutating the assembled source data."""
    shown = deepcopy(buckets)
    for bucket in shown.values():
        for event in bucket["events"]:
            if event["kind"] == "tool":
                event["input"], cuts = truncate_input(event["input"])
                event["output"], cut = truncate(event["output"])
                if cuts:
                    event["truncated_input"] = cuts
                if cut:
                    event["truncated"] = cut
            elif event["kind"] == "thinking":
                event["text"] = truncate(event["text"])[0]
        for prompt in bucket["prompts"]:
            prompt["items"] = [
                (kind, truncate(a)[0] if kind == "thinking" else a, b)
                for kind, a, b in prompt["items"]
            ]
    return shown


def real_prompts(prompts):
    """사람이 친 것만 센다. PRE_PROMPT 자리표는 프롬프트가 아니다."""
    return sum(1 for p in prompts if p.get("via") != PRE_PROMPT)


def demote(text):
    """답변 속 마크다운 제목을 두 단계 낮춘다.

    답변 2,645건 중 436건에 `#` 제목이 있어서, 그대로 넣으면 리포트의 `### N.` 프롬프트
    섹션 구조가 깨진다. 인용부호로 감싸는 방법은 못 쓴다 — 코드블록 300건과 표 264건이
    깨진다. `#####` 이상은 이미 최하단이라 건드리지 않는다.
    """
    return re.sub(r"^(#{1,4})(?= )", r"##\1", text, flags=re.M)


def render_md(prompts, stats, sid, days=(), day=None):
    start, end, span, active = fmt_span(stats["times"])
    tok = stats["tokens"]
    out = [f"# 세션 리포트 — {stats['project'] or '?'}", ""]
    out.append(f"- **에이전트** `{stats.get('agent', '?')}`")
    out.append(f"- **세션** `{sid}` · {start} → {end}")
    client = stats.get("client")
    version = stats.get("version")
    if client or version:
        out.append(
            f"- **클라이언트** {client or '?'}" + (f" · {version}" if version else "")
        )
    if stats.get("history_mode"):
        out.append(f"- **기록 모드** {stats['history_mode']}")
    if span:
        out.append(f"- **기간** 활동 {active} (달력 간격 {span})")
    if len(days) > 1 and day in days:
        # 이 줄이 없으면 대화 중간부터 시작하는 리포트가 잘린 것처럼 보인다
        out.append(
            f"- **이어짐** 이 세션은 {days[0]} ~ {days[-1]} "
            f"{len(days)}일에 걸쳐 있다 (여기는 {days.index(day) + 1}일차)"
        )
    if stats["branch"]:
        out.append(f"- **브랜치** {stats['branch']}")
    if stats["models"]:
        out.append(
            "- **모델** "
            + " · ".join(f"{m} {c}회" for m, c in stats["models"].most_common())
        )
    out.append(
        f"- **토큰** 입력 {tok['input_tokens']:,} · 출력 {tok['output_tokens']:,}"
        f" · 캐시읽기 {tok['cache_read_input_tokens']:,}"
    )
    denials = sum(stats["denials"].values())
    out.append(
        f"- **프롬프트** {real_prompts(prompts)}개 · **도구 호출** "
        f"{sum(stats['tools'].values())}회 · **권한 거부** {denials}회"
    )

    if stats["tools"]:
        out += ["", "## 도구 사용", "", "| 도구 | 횟수 |", "|---|---|"]
        out += [f"| `{n}` | {c} |" for n, c in stats["tools"].most_common()]

    if stats["files"]:
        out += ["", "## 수정된 파일", ""]
        for path, kinds in sorted(stats["files"].items()):
            detail = ", ".join(f"{k}×{v}" for k, v in kinds.most_common())
            out.append(f"- `{path}` — {detail}")

    out += ["", "## 프롬프트 타임라인", ""]
    for i, prompt in enumerate(prompts, 1):
        mark = f" _({prompt['via']})_" if prompt.get("via") else ""
        out.append(f"### {i}. {hhmmss(prompt['ts'])}{mark}")
        out.append("")
        out += [f"> {line}" for line in prompt["text"].splitlines()]
        out.append("")
        prev = None
        for kind, a, b in prompt["items"]:
            if kind == "tool":
                # 연속된 도구는 한 덩어리로 붙여 목록처럼 보이게 한다
                if prev not in (None, "tool"):
                    out.append("")
                out.append(f"- `{a}`" + (f" — {b}" if b else ""))
            elif kind == "answer":
                out += ["", demote(a), ""]
            else:  # thinking — 과정이라 접어둔다
                out += [
                    "",
                    "<details><summary>생각</summary>",
                    "",
                    demote(a),
                    "",
                    "</details>",
                    "",
                ]
            prev = kind
        if not prompt["items"]:
            out.append("_(도구 호출 없음)_")
        out.append("")
    return "\n".join(out) + "\n"


def render_jsonl(events, agent="", session_id=""):
    """중립 이벤트 JSONL. 원본 도구 UUID는 버리고 세션 식별 정보만 붙인다."""
    lines = []
    for seq, event in enumerate(events, 1):
        lines.append(
            json.dumps(
                {"seq": seq, "agent": agent, "session_id": session_id, **event},
                ensure_ascii=False,
            )
        )
    return "\n".join(lines) + "\n"
