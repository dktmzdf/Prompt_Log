"""Source-neutral report text utilities."""

import json

PATH_KEYS = ("file_path", "notebook_path", "filePath", "path")
LIMIT, HEAD, TAIL = 4096, 3072, 1024
PRE_PROMPT = "프롬프트 이전"


def truncate(text):
    """요약하지 않고 절단한다. 요약하면 오류 원인 추적이라는 목적이 사라진다."""
    encoded = text.encode("utf-8")
    if len(encoded) <= LIMIT:
        return text, 0
    head = encoded[:HEAD].decode("utf-8", errors="ignore")
    tail = encoded[-TAIL:].decode("utf-8", errors="ignore")
    cut = len(encoded) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n…({cut:,}바이트 생략)…\n{tail}", cut


def truncate_input(inp):
    """도구 인자의 긴 문자열도 자른다.

    출력만 자르면 부족하다 — Write의 content가 실측 16.8KB였다. 큰 파일을 쓰면
    통째로 들어간다. 어느 키를 얼마나 잘랐는지 함께 돌려준다.
    """
    cuts = {}

    def visit(value, path):
        if isinstance(value, str):
            shown, cut = truncate(value)
            if cut:
                cuts[path or "$value"] = cut
            return shown
        if isinstance(value, dict):
            return {
                k: visit(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()
            }
        if isinstance(value, list):
            return [visit(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    return visit(inp, ""), cuts


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


def json_text(value):
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
