"""Original regression cases, retained across the architecture change."""

import json
import unittest
from pathlib import Path
from collections import Counter
from datetime import datetime
from scripts.export import (
    load,
    build,
    build_codex,
    load_codex,
    detect_agent,
    export,
    render_md,
    render_jsonl,
    summarize_tool,
    hhmmss,
    session_hash,
    DENIAL_MARKER,
    DENIAL_PREFIX,
    HEAD,
    TAIL,
    PRE_PROMPT,
)

PATH_UUID = "4e602da4-9af7-4d14-9232-a2fc5b0ea432"


def selftest():
    import tempfile

    def rec(**kw):
        kw.setdefault("sessionId", "sess")  # 파일명과 같아야 자기 세션으로 인정된다
        return json.dumps(kw, ensure_ascii=False)

    img = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "A" * 4096},
    }
    lines = [
        # 일부러 시간순을 뒤섞고, 결과도 호출 순서와 어긋나게 배치한다
        rec(
            type="user",
            timestamp="2026-01-01T00:00:30Z",
            cwd="/tmp/proj",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t2",
                        "content": "두번째결과",
                    }
                ]
            },
        ),
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:20Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Read",
                        "input": {"file_path": "/tmp/a.txt"},
                    }
                ],
            },
        ),
        rec(
            type="user",
            timestamp="2026-01-01T00:00:00Z",
            cwd="/tmp/proj",
            message={"content": "첫 프롬프트"},
        ),
        # 생각 → 답변 → 도구 → 답변 순. .md가 이 순서를 그대로 살려야 한다.
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:05Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "## 먼저 목록부터",
                        "signature": "s",
                    },
                    {"type": "text", "text": "## 확인하겠습니다\n```sh\nls\n```"},
                ],
            },
        ),
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:10Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "ls -la", "description": "목록 확인"},
                    }
                ],
            },
        ),
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:12Z",
            message={
                "model": "m",
                "content": [{"type": "text", "text": "##### 이미 깊은 제목은 그대로"}],
            },
        ),
        rec(
            type="user",
            timestamp="2026-01-01T00:00:15Z",
            cwd="/tmp/proj",
            message={
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "X" * 9000}
                ]
            },
        ),
        # resume가 복사해 온 조상 세션 레코드 — 원본 파일에서 뽑히므로 여기선 빠져야 한다
        rec(
            type="user",
            timestamp="2026-01-01T00:00:35Z",
            sessionId="ancestor",
            cwd="/tmp/proj",
            message={"content": "남의 세션 프롬프트"},
        ),
        # 슬래시 커맨드 — 버리면 뒤따르는 도구가 직전 프롬프트에 잘못 붙는다
        rec(
            type="user",
            timestamp="2026-01-01T00:00:40Z",
            message={
                "content": "<command-name>/compact</command-name>\n"
                "<command-message>compacting</command-message>"
            },
        ),
        rec(
            type="user",
            timestamp="2026-01-01T00:00:41Z",
            isMeta=True,
            message={"content": "메타"},
        ),
        # NotebookEdit는 notebook_path를 쓴다 — 수정 파일 집계에 잡혀야 한다
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:45Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t8",
                        "name": "NotebookEdit",
                        "input": {
                            "notebook_path": "/tmp/nb.ipynb",
                            "new_source": "code",
                        },
                    }
                ],
            },
        ),
        # A modified-file assertion now requires an explicit successful result.
        rec(
            type="user",
            timestamp="2026-01-01T00:00:46Z",
            message={
                "content": [
                    {"type": "tool_result", "tool_use_id": "t8", "content": "saved"}
                ]
            },
        ),
        # 첨부 붙은 프롬프트 — content가 배열이지만 사람이 친 것.
        # cwd가 여기서 바뀐다: 슬러그는 첫 cwd(proj)로 고정돼야 한다.
        rec(
            type="user",
            timestamp="2026-01-01T00:00:50Z",
            cwd="/tmp/other",
            message={
                "content": [img, img, {"type": "text", "text": "이미지 붙인 프롬프트"}]
            },
        ),
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:55Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t3",
                        "name": "낯선도구",
                        "input": {"aaa": "짧음", "bbb": "이것은 훨씬 더 긴 문자열이다"},
                    }
                ],
            },
        ),
        # 내용에 박힌 UUID(경로의 일부) — 지우면 경로가 깨지므로 보존돼야 한다.
        # Bash는 요약이 description이라 .md엔 안 나가고 .jsonl의 input에만 남는다.
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:56Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t4",
                        "name": "Bash",
                        "input": {
                            "command": f"ls /tmp/{PATH_UUID}/x",
                            "description": "스크래치패드 확인",
                        },
                    }
                ],
            },
        ),
        # Read는 요약이 file_path라 .md에도 경로가 그대로 나가야 한다
        rec(
            type="assistant",
            timestamp="2026-01-01T00:00:57Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t5",
                        "name": "Read",
                        "input": {"file_path": f"/tmp/{PATH_UUID}/scratch.py"},
                    }
                ],
            },
        ),
        # 도구 거부에 딸려 온 지시 — tool_result에 묻혀 있지만 프롬프트로 살려야 한다
        rec(
            type="user",
            timestamp="2026-01-01T00:01:00Z",
            cwd="/tmp/proj",
            toolDenialKind="permission-rule",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t5",
                        "is_error": True,
                        "content": f"{DENIAL_PREFIX} "
                        f"{DENIAL_MARKER}\n그거 말고 이걸 해줘",
                    }
                ]
            },
        ),
        # 마커가 담긴 평범한 도구 출력 — 지시로 오인하면 안 된다 (실제로 오탐이 났던 케이스)
        rec(
            type="assistant",
            timestamp="2026-01-01T00:01:06Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t7",
                        "name": "Bash",
                        "input": {
                            "command": "grep denial log",
                            "description": "로그 조사",
                        },
                    }
                ],
            },
        ),
        rec(
            type="user",
            timestamp="2026-01-01T00:01:07Z",
            cwd="/tmp/proj",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t7",
                        "content": f"{DENIAL_PREFIX} {DENIAL_MARKER}\n남의 말",
                    }
                ]
            },
        ),
        rec(
            type="assistant",
            timestamp="2026-01-01T00:01:05Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t6",
                        "name": "Bash",
                        "input": {"command": "echo hi", "description": "다시 시도"},
                    }
                ],
            },
        ),
        # 큰 파일 쓰기 — input도 잘려야 한다 (실측 Write content 16.8KB)
        rec(
            type="assistant",
            timestamp="2026-01-01T00:01:10Z",
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t9",
                        "name": "Write",
                        "input": {"file_path": "/tmp/big.txt", "content": "Y" * 9000},
                    }
                ],
            },
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sess.jsonl"  # stem이 곧 세션ID — 남의 레코드 판별 기준
        src.write_text("\n".join(lines), encoding="utf-8")
        recs = load(src)
        buckets = build(recs)
        assert len(buckets) == 1, list(buckets)  # 합성 세션은 하루짜리다
        one = next(iter(buckets.values()))
        events, prompts, stats = one["events"], one["prompts"], one["stats"]

        # (a) 시간순 복원 (파일 순서가 아니라 timestamp 순서로)
        assert prompts[0]["text"] == "첫 프롬프트", prompts[0]
        assert prompts[2]["text"].endswith("이미지 붙인 프롬프트"), prompts[2]
        # (c) 메타는 걸러지고 (d) 첨부 프롬프트와 (j) 거부 지시는 살아남음
        # (k) 슬래시 커맨드도 남는다
        assert len(prompts) == 4, [p["text"] for p in prompts]
        assert prompts[1]["text"] == "/compact", prompts[1]
        assert prompts[1]["via"] == "슬래시 커맨드"
        assert "command-message" not in prompts[1]["text"]  # 부속 태그는 안 섞인다
        assert prompts[3]["text"] == "그거 말고 이걸 해줘", prompts[3]
        assert prompts[3]["via"] == "도구 거부와 함께"
        assert DENIAL_MARKER not in prompts[3]["text"]  # 안내 문구는 안 섞인다
        # 마커가 담긴 평범한 도구 출력은 지시로 오인하지 않는다
        assert "남의 말" not in [p["text"] for p in prompts]
        # (y) resume가 복사해 온 조상 세션 레코드는 빠진다 — 안 그러면 같은 대화가
        #     세션 수만큼 중복 리포트로 나온다 (실측 116묶음 중 26개가 중복 사본)
        assert "남의 세션 프롬프트" not in [p["text"] for p in prompts], prompts
        # (b) 도구가 올바른 프롬프트에 귀속.
        #     슬래시 커맨드 뒤의 NotebookEdit이 첫 프롬프트로 새지 않는 게 핵심이다.
        tools_of = lambda p: [(a, b) for k, a, b in p["items"] if k == "tool"]
        assert [n for n, _ in tools_of(prompts[0])] == ["Bash", "Read"], prompts[0][
            "items"
        ]
        assert [n for n, _ in tools_of(prompts[1])] == ["NotebookEdit"], prompts[1][
            "items"
        ]
        assert [n for n, _ in tools_of(prompts[2])] == [
            "낯선도구",
            "Bash",
            "Read",
        ], tools_of(prompts[2])
        assert [n for n, _ in tools_of(prompts[3])] == [
            "Bash",
            "Bash",
            "Write",
        ], tools_of(prompts[3])
        # (p) 답변·생각·도구가 발생 순서 그대로 섞여 들어간다
        assert [k for k, _, _ in prompts[0]["items"]] == [
            "thinking",
            "answer",
            "tool",
            "answer",
            "tool",
        ], prompts[0]["items"]
        # (e) 이미지 축약 — 같은 이미지 2장도 번호가 구분돼야 한다
        assert "[이미지 1/2: image/png, 3KB]" in prompts[2]["text"], prompts[2]["text"]
        assert "[이미지 2/2: image/png, 3KB]" in prompts[2]["text"], prompts[2]["text"]
        assert "AAAA" not in prompts[2]["text"]
        # (l) 슬러그는 첫 cwd로 고정 — 중간에 cwd가 바뀌어도 폴더가 안 옮겨간다
        assert stats["project"] == "proj", stats["project"]
        # (m) NotebookEdit의 notebook_path가 수정 파일 목록에 잡힌다
        assert "/tmp/nb.ipynb" in stats["files"], dict(stats["files"])
        assert (
            summarize_tool(
                "NotebookEdit", {"notebook_path": "/a.ipynb", "new_source": "code"}
            )
            == "/a.ipynb"
        )
        # (f) 뒤섞여 도착한 결과가 id로 올바른 호출에 붙음
        tools = [e for e in events if e["kind"] == "tool"]
        assert tools[1]["name"] == "Read" and tools[1]["output"] == "두번째결과", tools[
            1
        ]
        # (h) 절단 + 생략 바이트 기록
        assert tools[0]["truncated"] == 9000 - HEAD - TAIL, tools[0].get("truncated")
        assert len(tools[0]["output"]) < 9000
        # (n) input의 긴 문자열도 잘리고 어느 키를 잘랐는지 남는다
        write = next(t for t in tools if t["name"] == "Write")
        assert write["truncated_input"] == {"content": 9000 - HEAD - TAIL}, write.get(
            "truncated_input"
        )
        assert len(write["input"]["content"]) < 9000
        assert write["input"]["file_path"] == "/tmp/big.txt"  # 짧은 값은 그대로
        # (i) 요약 한 줄: description 있는 Bash / file_path / 미지 도구(짧은 문자열)
        assert tools_of(prompts[0])[0][1] == "목록 확인"
        assert tools_of(prompts[0])[1][1] == "/tmp/a.txt"
        assert tools_of(prompts[2])[0][1] == "짧음"
        # description 없는 Bash는 command로 대체
        assert summarize_tool("Bash", {"command": "git  status"}) == "git status"
        # (g) 구조적 UUID 필드는 하나도 안 나간다.
        #     단 내용에 박힌 UUID(파일 경로 등)는 지우면 재현이 깨지므로 보존한다.
        md = render_md(prompts, stats, "abcd1234-1111-2222-3333-444455556666")
        body = render_jsonl(events)
        for key in (
            "uuid",
            "parentUuid",
            "requestId",
            "promptId",
            "tool_use_id",
            "leafUuid",
            "sessionId",
        ):
            assert f'"{key}"' not in body, key
        assert "`abcd1234-1111-2222-3333-444455556666`" in md
        # 경로 속 UUID는 양쪽 다 보존 — .jsonl은 명령 원문, .md는 file_path 요약으로
        assert body.count(PATH_UUID) >= 2, body.count(PATH_UUID)
        assert f"/tmp/{PATH_UUID}/scratch.py" in md
        # (o) 기록은 UTC지만 리포트는 로컬 시각으로 찍힌다 (타임존 무관하게 검사)
        from datetime import timezone

        want = (
            datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone().strftime("%H:%M:%S")
        )
        assert hhmmss("2026-01-01T00:00:00Z") == want, hhmmss("2026-01-01T00:00:00Z")
        assert f"### 1. {want}" in md, md[:400]
        # (q) 답변 속 제목은 두 단계 강등 — 아니면 ### N. 섹션 구조가 깨진다
        assert "#### 확인하겠습니다" in md, md[:900]
        assert "## 확인하겠습니다\n" not in md.replace("#### 확인하겠습니다", "")
        assert "##### 이미 깊은 제목은 그대로" in md  # #####는 손대지 않는다
        # (r) 코드블록은 원문 그대로 살아남는다 (인용부호로 감쌌다면 깨졌을 것)
        assert "```sh\nls\n```" in md, md[:900]
        # (s) 생각은 접힌 details로, 앞뒤 빈 줄과 함께
        assert (
            "<details><summary>생각</summary>\n\n#### 먼저 목록부터\n\n</details>" in md
        ), md[:900]
        # .md의 ### 로 시작하는 줄은 프롬프트 섹션뿐이어야 한다
        assert len([l for l in md.splitlines() if l.startswith("### ")]) == len(prompts)
    selftest_days()
    selftest_codex()
    print(
        "자기검사 통과 — 정렬·귀속·필터·첨부·페어링·절단·요약·UUID·거부지시·"
        "슬래시커맨드·슬러그·노트북·input절단·타임존·대화인터리브·제목강등·"
        "코드블록·생각접기·날짜분할·턴보존·경로구조·조상세션제외·"
        "Codex네이티브·legacy·중립출력 26항목"
    )


def selftest_days():
    """날짜 분할 — 자정을 넘겨도 턴이 쪼개지지 않아야 한다 (실데이터 7건 케이스)."""
    import tempfile
    from datetime import timedelta, timezone

    # 로컬 자정을 기준점으로 잡는다. 어느 타임존에서 돌려도 결과가 같다.
    midnight = datetime(2026, 3, 2, 0, 0).astimezone()

    def at(minutes):
        """로컬 자정 기준 분 단위 오프셋을 UTC 기록 형식으로."""
        return (
            (midnight + timedelta(minutes=minutes))
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    def rec(**kw):
        return json.dumps(kw, ensure_ascii=False)

    lines = [
        # 1일차 23:50 프롬프트 → 도구가 자정을 넘겨 2일차 00:10에 실행
        rec(
            type="user",
            timestamp=at(-10),
            cwd="/tmp/proj",
            message={"content": "자정 직전 프롬프트"},
        ),
        rec(
            type="assistant",
            timestamp=at(10),
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "x1",
                        "name": "Bash",
                        "input": {"command": "sleep", "description": "자정 넘긴 도구"},
                    }
                ],
            },
        ),
        rec(
            type="user",
            timestamp=at(12),
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "x1",
                        "content": "넘어간 결과",
                    }
                ]
            },
        ),
        # 2일차 09:00 새 프롬프트 → 여기서 날짜가 바뀐다
        rec(
            type="user",
            timestamp=at(540),
            cwd="/tmp/proj",
            message={"content": "다음날 프롬프트"},
        ),
        rec(
            type="assistant",
            timestamp=at(545),
            message={
                "model": "m",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "x2",
                        "name": "Read",
                        "input": {"file_path": "/tmp/b.txt"},
                    }
                ],
            },
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sess.jsonl"
        src.write_text("\n".join(lines), encoding="utf-8")
        buckets = build(load(src))
        days = sorted(buckets)
        # (t) 자정을 넘긴 세션이 두 묶음으로 갈린다
        assert len(days) == 2, days
        d1, d2 = days
        # (u) 턴 보존 — 23:50 프롬프트의 00:10 도구가 앞날에 남는다
        p1 = buckets[d1]["prompts"]
        assert len(p1) == 1 and p1[0]["text"] == "자정 직전 프롬프트", p1
        assert [k for k, _, _ in p1[0]["items"]] == ["tool"], p1[0]["items"]
        # (v) 자정 넘어 도착한 결과도 올바른 호출에 붙는다
        t1 = [e for e in buckets[d1]["events"] if e["kind"] == "tool"]
        assert t1[0]["output"] == "넘어간 결과", t1[0]
        # (w) 집계가 그날 것만 — 도구가 1개씩 갈린다
        assert buckets[d1]["stats"]["tools"] == Counter({"Bash": 1}), buckets[d1][
            "stats"
        ]["tools"]
        assert buckets[d2]["stats"]["tools"] == Counter({"Read": 1}), buckets[d2][
            "stats"
        ]["tools"]
        # (x) 여러 날 세션에만 "이어짐" 줄이 붙는다
        md1 = render_md(p1, buckets[d1]["stats"], "abcd1234", days, d1)
        assert "**이어짐**" in md1 and "여기는 1일차" in md1, md1[:400]
        one_day = render_md(p1, buckets[d1]["stats"], "abcd1234", [d1], d1)
        assert "**이어짐**" not in one_day
    print("  날짜 분할 검사 통과 — 자정 넘김 2묶음, 턴 보존, 페어링 유지, 날짜별 집계")


def selftest_codex():
    """Codex 네이티브/legacy 형식과 범용 경로·메타데이터 회귀검사."""
    import tempfile

    sid = "019abcde-1111-2222-3333-444455556666"
    ts = "2026-01-02T00:00:00Z"

    def row(rtype, payload, seconds=0, ordinal=None):
        rec = {
            "type": rtype,
            "timestamp": f"2026-01-02T00:00:{seconds:02d}Z",
            "payload": payload,
        }
        if ordinal is not None:
            rec["ordinal"] = ordinal
        return json.dumps(rec, ensure_ascii=False)

    native = [
        row(
            "session_meta",
            {
                "id": sid,
                "session_id": sid,
                "cwd": "/tmp/codex-proj",
                "originator": "codex_cli",
                "cli_version": "1.2.3",
            },
            ordinal=0,
        ),
        row("turn_context", {"model": "gpt-test"}, 1, 1),
        row(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "UserMessage",
                    "id": "u1",
                    "content": "실제 사용자 프롬프트",
                },
            },
            2,
            2,
        ),
        # response_item 복제본은 native 모드에서 무시해야 한다.
        row(
            "response_item",
            {"type": "message", "role": "user", "content": "중복"},
            3,
            3,
        ),
        row(
            "event_msg",
            {
                "type": "item_completed",
                "item": {"type": "Reasoning", "summary_text": "검토 중"},
            },
            4,
            4,
        ),
        row(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "CommandExecution",
                    "command": "python -V",
                    "cwd": "/tmp/codex-proj",
                    "status": "completed",
                    "exit_code": 0,
                    "aggregated_output": "Python 3.x",
                },
            },
            5,
            5,
        ),
        row(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "FileChange",
                    "status": "completed",
                    "changes": [{"path": "app.py", "kind": "update"}],
                },
            },
            6,
            6,
        ),
        row(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "Extension",
                    "kind": "web.search",
                    "query": "docs",
                    "results": [{"title": "문서"}],
                },
            },
            7,
            7,
        ),
        row(
            "event_msg",
            {
                "type": "item_completed",
                "item": {"type": "AgentMessage", "content": "완료", "phase": "final"},
            },
            8,
            8,
        ),
        row(
            "event_msg",
            {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 3,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 2,
                    }
                },
            },
            9,
            9,
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "rollout.jsonl"
        source.write_text("\n".join(native) + "\n{잘린줄", encoding="utf-8")
        assert detect_agent(source) == "codex"
        recs = load_codex(source)
        buckets, meta = build_codex(recs)
        bucket = next(iter(buckets.values()))
        assert [p["text"] for p in bucket["prompts"]] == ["실제 사용자 프롬프트"]
        assert bucket["stats"]["tools"] == Counter(
            {"Bash": 1, "apply_patch": 1, "web.search": 1}
        )
        assert bucket["stats"]["files"]["app.py"] == Counter({"apply_patch": 1})
        assert bucket["stats"]["tokens"]["input_tokens"] == 10
        assert bucket["stats"]["tokens"]["reasoning_output_tokens"] == 2
        assert any(e.get("output") == "Python 3.x" for e in bucket["events"])
        written = export(source, report_root=Path(tmp) / "reports")
        assert written and "codex" in written[0][0].parts
        report = written[0][0].with_suffix(".jsonl").read_text(encoding="utf-8")
        assert f'"agent": "codex"' in report and f'"session_id": "{sid}"' in report
        assert session_hash(sid) in str(written[0][0])
        assert session_hash(sid) != session_hash(sid + "-other")

        legacy_source = Path(tmp) / "legacy.jsonl"
        legacy = [
            row(
                "session_meta",
                {"id": "legacy-id", "cwd": "/tmp/legacy", "history_mode": "legacy"},
                0,
            ),
            row("event_msg", {"type": "user_message", "message": "구형 프롬프트"}, 1),
            row(
                "event_msg",
                {
                    "type": "agent_message",
                    "message": "[external_agent_tool_call: Bash]\ncommand: pwd\n[/external_agent_tool_call]",
                },
                2,
            ),
            row(
                "event_msg",
                {
                    "type": "agent_message",
                    "message": "[external_agent_tool_result]\n/tmp/legacy\n[/external_agent_tool_result]",
                },
                3,
            ),
        ]
        legacy_source.write_text("\n".join(legacy), encoding="utf-8")
        legacy_buckets, _ = build_codex(load_codex(legacy_source))
        legacy_bucket = next(iter(legacy_buckets.values()))
        legacy_tool = next(e for e in legacy_bucket["events"] if e["kind"] == "tool")
        assert legacy_tool["name"] == "Bash" and legacy_tool["output"] == "/tmp/legacy"
    print("  Codex 검사 통과 — native·legacy·도구·토큰·중립 경로·세션 해시")


class LegacyRegression(unittest.TestCase):
    def test_original_suite(self):
        selftest()
