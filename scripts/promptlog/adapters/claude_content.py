"""Claude-only user-message filters and content block decoding."""

import json

INJECTED = ("<command-", "<local-command", "Caveat:", "This session is being continued")
DENIAL_PREFIX = "The user doesn't want to proceed with this tool use."
DENIAL_MARKER = "To tell you how to proceed, the user said:"


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
    name = content[len("<command-name>") :].split("</command-name>", 1)[0].strip()
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
    blocks = [b for b in (content or []) if isinstance(b, dict)]
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
            parts.append(
                f"[이미지 {seen}/{total}: {src.get('media_type','?')}, {kb}KB]"
            )
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
