#!/usr/bin/env python3
"""Claude Code 세션 JSONL을 사람이 읽는 리포트로 내보낸다.

로그를 새로 쌓지 않는다. Claude Code가 이미 ~/.claude/projects/ 아래 남기는
JSONL을 읽어 요약 .md 와 상세 .jsonl 두 개로 뽑는다.

  훅(stdin)   : {"transcript_path": ..., "session_id": ...}
  파일 지정   : export.py <session.jsonl>
  전체 백필   : export.py --all
  자기검사    : export.py --selftest
"""
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

OUT_ROOT = Path.home() / "claude-prompt-logs"
PROJECTS = Path.home() / ".claude" / "projects"

# 도구 결과 절단 — 실측상 92%가 4KB 이하라 대부분 손실이 0이다.
# 에러 메시지는 출력의 앞이나 끝에 몰려 있어 가운데를 잘라도 증거가 남는다.
LIMIT, HEAD, TAIL = 4096, 3072, 1024

# 사람이 친 프롬프트가 아닌, 시스템이 주입한 문자열들
INJECTED = ("<command-", "<local-command", "Caveat:", "This session is being continued")

# 도구 거부에 딸려 온 사용자 지시는 이 문구 뒤에 붙는다
DENIAL_PREFIX = "The user doesn't want to proceed with this tool use."
DENIAL_MARKER = "To tell you how to proceed, the user said:"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# 이 간격 이상 비어 있으면 사람이 자리를 비운 것으로 보고 활동 시간에서 뺀다
IDLE_GAP = 30 * 60

# 첫 프롬프트 이전에 온 답변·도구를 담는 자리표. 실측상 묶음 108개 중 7개(6%)에서
# 생긴다(주로 compact/resume 직후의 이어지는 답변). 사람이 친 프롬프트가 아니므로
# 집계에서는 뺀다.
PRE_PROMPT = "프롬프트 이전"

# 파일 경로를 담는 인자 키. NotebookEdit만 notebook_path를 쓴다.
PATH_KEYS = ("file_path", "notebook_path", "filePath", "path")

# 파일을 고쳐 쓰는 도구 — "수정된 파일" 집계 대상
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


# ---------------------------------------------------------------- 시각

def local_dt(iso):
    """기록은 UTC다. 그대로 찍으면 09:42가 00:42로 보인다. 로컬로 옮긴다."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def hhmmss(iso):
    dt = local_dt(iso)
    return dt.strftime("%H:%M:%S") if dt else (iso[11:19] if iso else "")


# ---------------------------------------------------------------- 읽기/판별

def load(path):
    """JSONL을 읽어 timestamp로 정렬해 돌려준다. 남의 세션 레코드는 뺀다.

    compact/resume 때문에 파일은 시간순이 아니다(실측: 09:32 → 03:46 → 03:41).
    정렬하지 않으면 리포트 순서가 뒤죽박죽 된다.

    resume하면 조상 세션 전체가 새 파일로 복사된다 — 실측상 61개 중 7개가 그렇고,
    ff351233.jsonl은 3,414개 중 자기 레코드가 4개뿐이다. 그대로 두면 같은 대화가
    세션 수만큼 중복 리포트로 나온다(실측 116묶음 중 26개가 중복 사본).
    복사본만 있고 원본 파일이 없는 세션은 0개라, 걸러도 잃는 대화가 없다.
    sessionId가 아예 없는 레코드(file-history-snapshot 등)는 그대로 둔다.
    """
    sid = Path(path).stem
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # 쓰는 중이라 잘린 마지막 줄일 수 있다
            if rec.get("sessionId", sid) == sid:
                recs.append(rec)
    recs.sort(key=lambda r: r.get("timestamp") or "")
    return recs


def is_human_prompt(rec):
    """사람이 직접 친 프롬프트인가.

    첨부(이미지·파일)가 붙은 프롬프트는 content가 배열이다. "문자열이면 사람"으로
    거르면 통째로 누락된다 — 실측상 한 세션에서 12개, 어떤 세션은 유일한 프롬프트를
    놓쳐 0개가 됐다. tool_result 배열과는 블록 타입으로 구분한다.
    """
    if rec.get("type") != "user" or rec.get("isMeta"):
        return False
    content = rec.get("message", {}).get("content")
    if isinstance(content, str):
        return bool(content.strip()) and not content.startswith(INJECTED)
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def slash_command(rec):
    """사용자가 실행한 슬래시 커맨드 이름.

    그냥 버리면 안 된다 — 실측상 57건이고, 그 **직후 도구 호출 41건이 직전 프롬프트에
    잘못 귀속**된다. 타임라인에 한 줄 남겨 귀속 경계를 만든다. 부속 태그
    (`<command-message>`, `<command-args>`)는 노이즈라 이름만 뽑는다.
    """
    if rec.get("type") != "user" or rec.get("isMeta"):
        return None
    content = rec.get("message", {}).get("content")
    if not isinstance(content, str) or not content.startswith("<command-name>"):
        return None
    name = content[len("<command-name>"):].split("</command-name>", 1)[0].strip()
    return name or None


def denial_feedback(rec):
    """도구를 거부하며 남긴 말을 꺼낸다.

    사용자가 도구 호출을 거부하면서 지시를 덧붙이면 그 말은 별도 프롬프트 레코드가 아니라
    tool_result 안에 묻힌다. 그냥 두면 타임라인에서 통째로 사라진다 —
    실측상 전 프로젝트에 62건, 어떤 세션은 지시 4개 중 3개가 이 경로로 들어왔다.
    """
    # 마커 문자열만 보면 안 된다 — 그 문자열이 우연히 담긴 도구 출력(예: 로그를 grep한
    # 결과)까지 사용자 지시로 오인한다. 실제로 오탐이 났다. 구조적 신호인
    # toolDenialKind와 정해진 시작 문구를 함께 요구하면 걸러진다.
    if rec.get("type") != "user" or not rec.get("toolDenialKind"):
        return None
    content = rec.get("message", {}).get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        text = result_text(block.get("content"))
        if text.startswith(DENIAL_PREFIX) and DENIAL_MARKER in text:
            said = text.split(DENIAL_MARKER, 1)[1].strip()
            if said:
                return said
    return None


def prompt_text(content):
    """프롬프트 본문을 뽑는다. 이미지는 축약 표기로 바꾼다 (경로가 로그에 없다)."""
    if isinstance(content, str):
        return content.strip()
    blocks = [b for b in content if isinstance(b, dict)]
    total = sum(1 for b in blocks if b.get("type") == "image")
    parts, seen = [], 0
    for block in blocks:
        if block.get("type") == "text":
            parts.append(block.get("text", "").strip())
        elif block.get("type") == "image":
            # 위치로 센다 — .index()는 값 비교라 같은 이미지 2장이면 둘 다 1번이 된다
            seen += 1
            src = block.get("source", {})
            kb = len(src.get("data", "")) * 3 // 4 // 1024
            parts.append(f"[이미지 {seen}/{total}: {src.get('media_type','?')}, {kb}KB]")
    return "\n".join(p for p in parts if p)


def result_text(content):
    """tool_result 본문을 문자열로. 이미지는 축약."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False) if content else ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
        elif block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif block.get("type") == "image":
            src = block.get("source", {})
            kb = len(src.get("data", "")) * 3 // 4 // 1024
            parts.append(f"[이미지: {src.get('media_type','?')}, {kb}KB]")
    return "\n".join(parts)


def truncate(text):
    """요약하지 않고 절단한다. 요약하면 오류 원인 추적이라는 목적이 사라진다."""
    if len(text) <= LIMIT:
        return text, 0
    cut = len(text) - HEAD - TAIL
    return f"{text[:HEAD]}\n…({cut:,}바이트 생략)…\n{text[-TAIL:]}", cut


def truncate_input(inp):
    """도구 인자의 긴 문자열도 자른다.

    출력만 자르면 부족하다 — Write의 content가 실측 16.8KB였다. 큰 파일을 쓰면
    통째로 들어간다. 어느 키를 얼마나 잘랐는지 함께 돌려준다.
    """
    if not isinstance(inp, dict):
        return inp, {}
    out, cuts = {}, {}
    for key, value in inp.items():
        if isinstance(value, str):
            out[key], cut = truncate(value)
            if cut:
                cuts[key] = cut
        else:
            out[key] = value
    return out, cuts


# ---------------------------------------------------------------- 도구 요약

def summarize_tool(name, inp):
    """도구가 무엇을 했는지 한 줄. 생성하지 않고 로그에 있는 필드에서 꺼낸다."""
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        # 실측: 2,102회 중 2,079회(98.9%)에 description이 이미 있다
        if inp.get("description"):
            return inp["description"]
        return " ".join(inp.get("command", "").split())[:60]
    for key in PATH_KEYS:
        if inp.get(key):
            return str(inp[key])
    if inp.get("Title"):
        return str(inp["Title"])
    if inp.get("url"):
        return str(inp["url"])
    for key in ("pattern", "query"):
        if inp.get(key):
            return str(inp[key])[:60]
    if name == "AskUserQuestion":
        questions = inp.get("questions")
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            first = questions[0]
            return first.get("header") or str(first.get("question", ""))[:60]
    if inp.get("taskId"):
        return f"{inp['taskId']} → {inp.get('status', '')}".strip(" →")
    strings = [v for v in inp.values() if isinstance(v, str) and v.strip()]
    return min(strings, key=len)[:60] if strings else ""


# ---------------------------------------------------------------- 조립

def build(recs):
    """정렬된 레코드를 이벤트 스트림과 집계로 바꾼다."""
    # 도구 호출↔결과는 순서로 짝지으면 안 된다 — 전수 검증 결과 순차 페어링은
    # 95.75%만 맞았다(5,221건 중 222건 어긋남). id로 맞추고 id는 출력에서 버린다.
    results = {}
    for rec in recs:
        content = rec.get("message", {}).get("content")
        if rec.get("type") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block

    # 날짜별로 나눠 담는다. 하루치 일지가 되도록 그날 한 작업만 그날 묶음에 들어간다.
    buckets, cur = OrderedDict(), None
    branch, project = "", ""

    def new_bucket():
        # "첫 프롬프트 이전에도 도구가 돈다"(compact 직후)를 받는 자리가 pre다
        return {"events": [], "prompts": [], "pre": [], "stats": {
            "tools": Counter(), "models": Counter(), "files": defaultdict(Counter),
            "denials": Counter(), "tokens": Counter(),
            "branch": "", "project": "", "times": []}}

    def use(day):
        """묶음을 그날로 옮긴다. 프롬프트를 만날 때만 부른다 — 턴이 쪼개지지 않는다."""
        nonlocal cur
        cur = buckets.setdefault(day, new_bucket())
        return cur

    def add(item):
        """답변·생각·도구를 발생 순서 그대로 현재 프롬프트에 붙인다.

        레코드를 timestamp 정렬해 순회하므로 append 순서가 곧 시간순이다.
        """
        (cur["prompts"][-1]["items"] if cur["prompts"] else cur["pre"]).append(item)

    for rec in recs:
        rtype = rec.get("type")
        when = local_dt(rec.get("timestamp"))
        day = when.strftime("%Y-%m-%d") if when else "0000-00-00"

        said = via = None
        if is_human_prompt(rec):
            said = prompt_text(rec.get("message", {}).get("content"))
        elif rtype == "user":
            said = denial_feedback(rec)
            via = "도구 거부와 함께"
            if not said:
                said = slash_command(rec)
                via = "슬래시 커맨드"

        # 프롬프트에서만 날짜를 바꾼다. 23:59 프롬프트의 00:01 도구는 앞날에 남는다
        # (실측 7건). 레코드를 날짜로 자르면 도구 호출과 결과가 갈라져 페어링이 깨진다.
        if said or cur is None:
            if when:
                use(day)
            elif cur is None:
                # 파일 선두에는 timestamp 없는 메타 레코드가 온다(queue-operation 등).
                # 날짜를 정할 수 없으니 건너뛴다 — 안 그러면 0000-00-00 폴더가 생긴다.
                continue
        stats = cur["stats"]
        events, prompts = cur["events"], cur["prompts"]

        if rec.get("timestamp"):
            stats["times"].append(rec["timestamp"])
        branch = rec.get("gitBranch") or branch
        # 첫 cwd로 고정한다. 마지막 cwd로 두면 훅이 매 턴 도는 동안 같은 세션
        # 리포트가 여러 폴더로 흩어진다(52세션 중 10개가 cwd 다중).
        if rec.get("cwd") and not project:
            project = os.path.basename(rec["cwd"])
        if rec.get("toolDenialKind"):
            stats["denials"][rec["toolDenialKind"]] += 1

        if said:
            prompts.append({"ts": rec.get("timestamp", ""), "text": said,
                            "via": via, "items": []})
            event = {"kind": "prompt", "ts": rec.get("timestamp", ""), "text": said}
            if via:
                event["via"] = via
            events.append(event)
            continue

        if rtype != "assistant":
            continue

        msg = rec.get("message", {})
        if msg.get("model"):
            stats["models"][msg["model"]] += 1
        usage = msg.get("usage") or {}
        # 상위 필드가 0이고 iterations에만 실값이 있는 레코드가 있다
        rows = usage.get("iterations") or ([usage] if usage else [])
        for row in rows:
            for key in ("input_tokens", "output_tokens",
                        "cache_read_input_tokens", "cache_creation_input_tokens"):
                stats["tokens"][key] += row.get(key) or 0

        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking" and block.get("thinking", "").strip():
                # 내용 없는 thinking이 17%(3,001건 중 519건)다. 그냥 넣으면
                # 펼칠 게 없는 접기 버튼만 생긴다.
                text = truncate(block["thinking"].strip())[0]
                events.append({"kind": "thinking", "ts": rec.get("timestamp", ""),
                               "text": text})
                add(("thinking", text, None))
            elif btype == "text" and block.get("text", "").strip():
                text = block["text"].strip()
                events.append({"kind": "answer", "ts": rec.get("timestamp", ""),
                               "text": text})
                add(("answer", text, None))
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input") or {}
                stats["tools"][name] += 1
                if name in EDIT_TOOLS:
                    # NotebookEdit는 notebook_path를 쓴다 — file_path만 보면 누락된다
                    path = next((inp[k] for k in PATH_KEYS if inp.get(k)), None)
                    if path:
                        stats["files"][path][name] += 1
                summary = summarize_tool(name, inp)
                add(("tool", name, summary))
                res = results.get(block.get("id")) or {}
                out, cut = truncate(result_text(res.get("content")))
                shown, in_cuts = truncate_input(inp)
                event = {"kind": "tool", "ts": rec.get("timestamp", ""), "name": name,
                         "summary": summary, "input": shown, "output": out,
                         "ok": not res.get("is_error", False)}
                if cut:
                    event["truncated"] = cut
                if in_cuts:
                    event["truncated_input"] = in_cuts
                events.append(event)

    for b in buckets.values():
        st = b["stats"]
        st["branch"], st["project"] = branch, project  # 세션 공통값
        if b["pre"]:
            b["prompts"].insert(0, {"ts": st["times"][0] if st["times"] else "",
                                    "text": "", "via": PRE_PROMPT,
                                    "items": b["pre"]})
    return buckets


# ---------------------------------------------------------------- 출력

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


def fmt_dur(seconds):
    mins = int(seconds // 60)
    return f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"


def fmt_span(times):
    """시작·끝(로컬 시각)과 달력 간격, 그리고 실제 활동 시간.

    resume한 세션은 달력 간격이 "173시간 44분"으로 나온다. 그건 작업 시간이 아니라
    첫 기록과 마지막 기록 사이일 뿐이다. 30분 이상 빈 구간을 뺀 값을 함께 낸다.
    """
    stamps = sorted(d for d in (local_dt(t) for t in times) if d)
    if not stamps:
        return "", "", "", ""
    first, last = stamps[0], stamps[-1]
    active = 0.0
    for prev, cur in zip(stamps, stamps[1:]):
        gap = (cur - prev).total_seconds()
        if gap < IDLE_GAP:
            active += gap
    fmt = "%Y-%m-%d %H:%M:%S"
    return (first.strftime(fmt), last.strftime(f"{fmt} %Z"),
            fmt_dur((last - first).total_seconds()), fmt_dur(active))


def render_md(prompts, stats, sid, days=(), day=None):
    start, end, span, active = fmt_span(stats["times"])
    tok = stats["tokens"]
    out = [f"# 세션 리포트 — {stats['project'] or '?'}", ""]
    out.append(f"- **세션** `{sid[:8]}` · {start} → {end}")
    if span:
        out.append(f"- **기간** 활동 {active} (달력 간격 {span})")
    if len(days) > 1 and day in days:
        # 이 줄이 없으면 대화 중간부터 시작하는 리포트가 잘린 것처럼 보인다
        out.append(f"- **이어짐** 이 세션은 {days[0]} ~ {days[-1]} "
                   f"{len(days)}일에 걸쳐 있다 (여기는 {days.index(day) + 1}일차)")
    if stats["branch"]:
        out.append(f"- **브랜치** {stats['branch']}")
    if stats["models"]:
        out.append("- **모델** " + " · ".join(
            f"{m} {c}회" for m, c in stats["models"].most_common()))
    out.append(f"- **토큰** 입력 {tok['input_tokens']:,} · 출력 {tok['output_tokens']:,}"
               f" · 캐시읽기 {tok['cache_read_input_tokens']:,}")
    denials = sum(stats["denials"].values())
    out.append(f"- **프롬프트** {real_prompts(prompts)}개 · **도구 호출** "
               f"{sum(stats['tools'].values())}회 · **권한 거부** {denials}회")

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
                out += ["", "<details><summary>생각</summary>", "",
                        demote(a), "", "</details>", ""]
            prev = kind
        if not prompt["items"]:
            out.append("_(도구 호출 없음)_")
        out.append("")
    return "\n".join(out) + "\n"


def render_jsonl(events):
    """UUID는 넣지 않는다. 순서는 seq가, 짝짓기는 이미 끝났다."""
    lines = []
    for seq, event in enumerate(events, 1):
        lines.append(json.dumps({"seq": seq, **event}, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def export(path):
    """날짜 묶음마다 파일 한 쌍씩 쓴다. 하루가 지난 폴더는 다시 바뀌지 않는다."""
    recs = load(path)
    if not recs:
        return []
    buckets = build(recs)
    sid8 = Path(path).stem[:8]
    days = sorted(buckets)
    written = []
    for day in days:
        b = buckets[day]
        stats, times = b["stats"], b["stats"]["times"]
        slug = stats["project"] or Path(path).parent.name.lstrip("-")
        # 그날의 첫 활동 시각. 세션 시작 시각을 쓰면 여러 날 폴더가 전부 같은 이름이 된다.
        first = local_dt(min(times)) if times else None
        hhmm = first.strftime("%H-%M") if first else "00-00"  # ':'는 Finder에서 깨진다
        out_dir = OUT_ROOT / slug / day / f"{hhmm}_{sid8}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for d in (OUT_ROOT, OUT_ROOT / slug, OUT_ROOT / slug / day, out_dir):
            os.chmod(d, 0o700)  # 원본 JSONL이 600이므로 맞춘다
        base = out_dir / f"{slug}-{day}-{sid8}"
        for suffix, body in (
                (".md", render_md(b["prompts"], stats, sid8, days, day)),
                (".jsonl", render_jsonl(b["events"]))):
            target = base.with_suffix(suffix)
            target.write_text(body, encoding="utf-8")
            os.chmod(target, 0o600)  # 디렉터리만 700이면 반쪽이다
        written.append((base, real_prompts(b["prompts"]),
                        sum(stats["tools"].values())))
    return written


# ---------------------------------------------------------------- 자기검사

PATH_UUID = "4e602da4-9af7-4d14-9232-a2fc5b0ea432"


def selftest():
    import tempfile

    def rec(**kw):
        kw.setdefault("sessionId", "sess")  # 파일명과 같아야 자기 세션으로 인정된다
        return json.dumps(kw, ensure_ascii=False)

    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                       "data": "A" * 4096}}
    lines = [
        # 일부러 시간순을 뒤섞고, 결과도 호출 순서와 어긋나게 배치한다
        rec(type="user", timestamp="2026-01-01T00:00:30Z", cwd="/tmp/proj",
            message={"content": [{"type": "tool_result", "tool_use_id": "t2",
                                  "content": "두번째결과"}]}),
        rec(type="assistant", timestamp="2026-01-01T00:00:20Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t2", "name": "Read",
                 "input": {"file_path": "/tmp/a.txt"}}]}),
        rec(type="user", timestamp="2026-01-01T00:00:00Z", cwd="/tmp/proj",
            message={"content": "첫 프롬프트"}),
        # 생각 → 답변 → 도구 → 답변 순. .md가 이 순서를 그대로 살려야 한다.
        rec(type="assistant", timestamp="2026-01-01T00:00:05Z",
            message={"model": "m", "content": [
                {"type": "thinking", "thinking": "## 먼저 목록부터", "signature": "s"},
                {"type": "text", "text": "## 확인하겠습니다\n```sh\nls\n```"}]}),
        rec(type="assistant", timestamp="2026-01-01T00:00:10Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "ls -la", "description": "목록 확인"}}]}),
        rec(type="assistant", timestamp="2026-01-01T00:00:12Z",
            message={"model": "m", "content": [
                {"type": "text", "text": "##### 이미 깊은 제목은 그대로"}]}),
        rec(type="user", timestamp="2026-01-01T00:00:15Z", cwd="/tmp/proj",
            message={"content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": "X" * 9000}]}),
        # resume가 복사해 온 조상 세션 레코드 — 원본 파일에서 뽑히므로 여기선 빠져야 한다
        rec(type="user", timestamp="2026-01-01T00:00:35Z", sessionId="ancestor",
            cwd="/tmp/proj", message={"content": "남의 세션 프롬프트"}),
        # 슬래시 커맨드 — 버리면 뒤따르는 도구가 직전 프롬프트에 잘못 붙는다
        rec(type="user", timestamp="2026-01-01T00:00:40Z",
            message={"content": "<command-name>/compact</command-name>\n"
                                "<command-message>compacting</command-message>"}),
        rec(type="user", timestamp="2026-01-01T00:00:41Z", isMeta=True,
            message={"content": "메타"}),
        # NotebookEdit는 notebook_path를 쓴다 — 수정 파일 집계에 잡혀야 한다
        rec(type="assistant", timestamp="2026-01-01T00:00:45Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t8", "name": "NotebookEdit",
                 "input": {"notebook_path": "/tmp/nb.ipynb", "new_source": "code"}}]}),
        # 첨부 붙은 프롬프트 — content가 배열이지만 사람이 친 것.
        # cwd가 여기서 바뀐다: 슬러그는 첫 cwd(proj)로 고정돼야 한다.
        rec(type="user", timestamp="2026-01-01T00:00:50Z", cwd="/tmp/other",
            message={"content": [img, img,
                                 {"type": "text", "text": "이미지 붙인 프롬프트"}]}),
        rec(type="assistant", timestamp="2026-01-01T00:00:55Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t3", "name": "낯선도구",
                 "input": {"aaa": "짧음", "bbb": "이것은 훨씬 더 긴 문자열이다"}}]}),
        # 내용에 박힌 UUID(경로의 일부) — 지우면 경로가 깨지므로 보존돼야 한다.
        # Bash는 요약이 description이라 .md엔 안 나가고 .jsonl의 input에만 남는다.
        rec(type="assistant", timestamp="2026-01-01T00:00:56Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t4", "name": "Bash",
                 "input": {"command": f"ls /tmp/{PATH_UUID}/x",
                           "description": "스크래치패드 확인"}}]}),
        # Read는 요약이 file_path라 .md에도 경로가 그대로 나가야 한다
        rec(type="assistant", timestamp="2026-01-01T00:00:57Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t5", "name": "Read",
                 "input": {"file_path": f"/tmp/{PATH_UUID}/scratch.py"}}]}),
        # 도구 거부에 딸려 온 지시 — tool_result에 묻혀 있지만 프롬프트로 살려야 한다
        rec(type="user", timestamp="2026-01-01T00:01:00Z", cwd="/tmp/proj",
            toolDenialKind="permission-rule",
            message={"content": [{"type": "tool_result", "tool_use_id": "t5",
                                  "is_error": True,
                                  "content": f"{DENIAL_PREFIX} "
                                             f"{DENIAL_MARKER}\n그거 말고 이걸 해줘"}]}),
        # 마커가 담긴 평범한 도구 출력 — 지시로 오인하면 안 된다 (실제로 오탐이 났던 케이스)
        rec(type="assistant", timestamp="2026-01-01T00:01:06Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t7", "name": "Bash",
                 "input": {"command": "grep denial log", "description": "로그 조사"}}]}),
        rec(type="user", timestamp="2026-01-01T00:01:07Z", cwd="/tmp/proj",
            message={"content": [{"type": "tool_result", "tool_use_id": "t7",
                                  "content": f"{DENIAL_PREFIX} {DENIAL_MARKER}\n남의 말"}]}),
        rec(type="assistant", timestamp="2026-01-01T00:01:05Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t6", "name": "Bash",
                 "input": {"command": "echo hi", "description": "다시 시도"}}]}),
        # 큰 파일 쓰기 — input도 잘려야 한다 (실측 Write content 16.8KB)
        rec(type="assistant", timestamp="2026-01-01T00:01:10Z",
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "t9", "name": "Write",
                 "input": {"file_path": "/tmp/big.txt", "content": "Y" * 9000}}]}),
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
        assert [n for n, _ in tools_of(prompts[0])] == ["Bash", "Read"], prompts[0]["items"]
        assert [n for n, _ in tools_of(prompts[1])] == ["NotebookEdit"], prompts[1]["items"]
        assert [n for n, _ in tools_of(prompts[2])] == ["낯선도구", "Bash", "Read"], \
            tools_of(prompts[2])
        assert [n for n, _ in tools_of(prompts[3])] == ["Bash", "Bash", "Write"], \
            tools_of(prompts[3])
        # (p) 답변·생각·도구가 발생 순서 그대로 섞여 들어간다
        assert [k for k, _, _ in prompts[0]["items"]] == \
            ["thinking", "answer", "tool", "answer", "tool"], prompts[0]["items"]
        # (e) 이미지 축약 — 같은 이미지 2장도 번호가 구분돼야 한다
        assert "[이미지 1/2: image/png, 3KB]" in prompts[2]["text"], prompts[2]["text"]
        assert "[이미지 2/2: image/png, 3KB]" in prompts[2]["text"], prompts[2]["text"]
        assert "AAAA" not in prompts[2]["text"]
        # (l) 슬러그는 첫 cwd로 고정 — 중간에 cwd가 바뀌어도 폴더가 안 옮겨간다
        assert stats["project"] == "proj", stats["project"]
        # (m) NotebookEdit의 notebook_path가 수정 파일 목록에 잡힌다
        assert "/tmp/nb.ipynb" in stats["files"], dict(stats["files"])
        assert summarize_tool("NotebookEdit", {"notebook_path": "/a.ipynb",
                                               "new_source": "code"}) == "/a.ipynb"
        # (f) 뒤섞여 도착한 결과가 id로 올바른 호출에 붙음
        tools = [e for e in events if e["kind"] == "tool"]
        assert tools[1]["name"] == "Read" and tools[1]["output"] == "두번째결과", tools[1]
        # (h) 절단 + 생략 바이트 기록
        assert tools[0]["truncated"] == 9000 - HEAD - TAIL, tools[0].get("truncated")
        assert len(tools[0]["output"]) < 9000
        # (n) input의 긴 문자열도 잘리고 어느 키를 잘랐는지 남는다
        write = next(t for t in tools if t["name"] == "Write")
        assert write["truncated_input"] == {"content": 9000 - HEAD - TAIL}, write.get(
            "truncated_input")
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
        for key in ("uuid", "parentUuid", "requestId", "promptId",
                    "tool_use_id", "leafUuid", "sessionId"):
            assert f'"{key}"' not in body, key
        assert "abcd1234-1111-2222-3333-444455556666" not in md  # 세션 ID 전체는 미출력
        assert "`abcd1234`" in md  # 앞 8자만 추적용으로 남긴다
        # 경로 속 UUID는 양쪽 다 보존 — .jsonl은 명령 원문, .md는 file_path 요약으로
        assert body.count(PATH_UUID) >= 2, body.count(PATH_UUID)
        assert f"/tmp/{PATH_UUID}/scratch.py" in md
        # (o) 기록은 UTC지만 리포트는 로컬 시각으로 찍힌다 (타임존 무관하게 검사)
        from datetime import timezone
        want = datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone().strftime("%H:%M:%S")
        assert hhmmss("2026-01-01T00:00:00Z") == want, hhmmss("2026-01-01T00:00:00Z")
        assert f"### 1. {want}" in md, md[:400]
        # (q) 답변 속 제목은 두 단계 강등 — 아니면 ### N. 섹션 구조가 깨진다
        assert "#### 확인하겠습니다" in md, md[:900]
        assert "## 확인하겠습니다\n" not in md.replace("#### 확인하겠습니다", "")
        assert "##### 이미 깊은 제목은 그대로" in md  # #####는 손대지 않는다
        # (r) 코드블록은 원문 그대로 살아남는다 (인용부호로 감쌌다면 깨졌을 것)
        assert "```sh\nls\n```" in md, md[:900]
        # (s) 생각은 접힌 details로, 앞뒤 빈 줄과 함께
        assert "<details><summary>생각</summary>\n\n#### 먼저 목록부터\n\n</details>" in md, \
            md[:900]
        # .md의 ### 로 시작하는 줄은 프롬프트 섹션뿐이어야 한다
        assert len([l for l in md.splitlines() if l.startswith("### ")]) == len(prompts)
    selftest_days()
    print("자기검사 통과 — 정렬·귀속·필터·첨부·페어링·절단·요약·UUID·거부지시·"
          "슬래시커맨드·슬러그·노트북·input절단·타임존·대화인터리브·제목강등·"
          "코드블록·생각접기·날짜분할·턴보존·경로구조·조상세션제외 23항목")


def selftest_days():
    """날짜 분할 — 자정을 넘겨도 턴이 쪼개지지 않아야 한다 (실데이터 7건 케이스)."""
    import tempfile
    from datetime import timedelta, timezone

    # 로컬 자정을 기준점으로 잡는다. 어느 타임존에서 돌려도 결과가 같다.
    midnight = datetime(2026, 3, 2, 0, 0).astimezone()

    def at(minutes):
        """로컬 자정 기준 분 단위 오프셋을 UTC 기록 형식으로."""
        return (midnight + timedelta(minutes=minutes)).astimezone(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def rec(**kw):
        return json.dumps(kw, ensure_ascii=False)

    lines = [
        # 1일차 23:50 프롬프트 → 도구가 자정을 넘겨 2일차 00:10에 실행
        rec(type="user", timestamp=at(-10), cwd="/tmp/proj",
            message={"content": "자정 직전 프롬프트"}),
        rec(type="assistant", timestamp=at(10),
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "x1", "name": "Bash",
                 "input": {"command": "sleep", "description": "자정 넘긴 도구"}}]}),
        rec(type="user", timestamp=at(12),
            message={"content": [{"type": "tool_result", "tool_use_id": "x1",
                                  "content": "넘어간 결과"}]}),
        # 2일차 09:00 새 프롬프트 → 여기서 날짜가 바뀐다
        rec(type="user", timestamp=at(540), cwd="/tmp/proj",
            message={"content": "다음날 프롬프트"}),
        rec(type="assistant", timestamp=at(545),
            message={"model": "m", "content": [
                {"type": "tool_use", "id": "x2", "name": "Read",
                 "input": {"file_path": "/tmp/b.txt"}}]}),
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
        assert buckets[d1]["stats"]["tools"] == Counter({"Bash": 1}), \
            buckets[d1]["stats"]["tools"]
        assert buckets[d2]["stats"]["tools"] == Counter({"Read": 1}), \
            buckets[d2]["stats"]["tools"]
        # (x) 여러 날 세션에만 "이어짐" 줄이 붙는다
        md1 = render_md(p1, buckets[d1]["stats"], "abcd1234", days, d1)
        assert "**이어짐**" in md1 and "여기는 1일차" in md1, md1[:400]
        one_day = render_md(p1, buckets[d1]["stats"], "abcd1234", [d1], d1)
        assert "**이어짐**" not in one_day
    print("  날짜 분할 검사 통과 — 자정 넘김 2묶음, 턴 보존, 페어링 유지, 날짜별 집계")


# ---------------------------------------------------------------- 진입점

def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return
    if "--all" in args:
        targets = sorted(PROJECTS.glob("*/*.jsonl"))
    elif args:
        targets = [Path(a) for a in args]
    else:  # 훅 — stdin으로 페이로드가 온다
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return
        path = payload.get("transcript_path")
        if not path or not Path(path).exists():
            return
        targets = [Path(path)]

    for target in targets:
        try:
            done = export(target)
        except Exception as exc:  # 리포트 실패가 세션을 막으면 안 된다
            print(f"건너뜀 {target.name}: {exc}", file=sys.stderr)
            continue
        if args:  # 날짜 묶음마다 한 줄
            for base, n_prompt, n_tool in done:
                print(f"{base}.{{md,jsonl}}  프롬프트 {n_prompt} · 도구 {n_tool}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"prompt-log: {exc}", file=sys.stderr)
    sys.exit(0)  # 훅은 항상 성공으로 끝낸다
