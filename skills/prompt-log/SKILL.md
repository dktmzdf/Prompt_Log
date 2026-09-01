---
name: prompt-log
description: Claude Code 또는 Codex 세션 기록을 사람이 읽는 범용 리포트로 내보낸다. 사용자가 "프롬프트 로그", "세션 리포트", "이번 세션 뭐 했는지 정리", "작업 기록 뽑아줘", "prompt log", "세션 로그 백필"이라고 하거나 과거 에이전트 세션에서 무엇을 했는지 되짚어야 할 때 사용한다.
---

# prompt-log

Claude Code와 Codex가 이미 기록한 세션 JSONL을 읽어 날짜별 리포트로 변환한다.
새 로그를 만들거나 원본을 수정하지 않는다.

원본 위치:

- Claude Code: `~/.claude/projects/<프로젝트>/<세션ID>.jsonl`
- Codex: `~/.codex/sessions/**/*.jsonl`

출력 위치:

`~/agent-prompt-logs/<에이전트>/<프로젝트>/<날짜>/<HH-MM>_<세션해시8자>/<프로젝트>-<날짜>-<세션해시8자>.{md,jsonl}`

- `.md`: 세션 메타데이터, 기간·모델·토큰 집계, 수정 파일, 프롬프트와 응답 타임라인
- `.jsonl`: `agent`와 `session_id`가 포함된 중립 이벤트 스트림. 도구 결과는 요약하지
  않고 4KB에서 가운데만 절단한다.

`Stop` 훅은 응답이 끝날 때 현재 세션 리포트를 갱신한다. 훅 명령은 Codex의
`PLUGIN_ROOT`와 Claude Code의 `CLAUDE_PLUGIN_ROOT`를 모두 지원한다.

## 수동 실행

```bash
# Claude + Codex 전체 백필
python3 scripts/export.py --all

# 한 에이전트만 백필
python3 scripts/export.py --all --agent codex
python3 scripts/export.py --all --agent claude

# 형식을 자동 판별해 특정 파일 내보내기
python3 scripts/export.py <세션.jsonl>

# 임시 출력 경로로 확인
python3 scripts/export.py <세션.jsonl> --output <디렉터리>

# 내장 회귀검사
python3 scripts/export.py --selftest
```

## 구현 제약

- Claude의 compact/resume 파일은 시간순이 아닐 수 있으므로 `timestamp`로 정렬한다.
- Claude의 도구 호출과 결과는 순서가 아니라 `tool_use_id`로 짝짓는다.
- Claude resume가 복사한 조상 세션 레코드는 `sessionId`로 제외한다.
- Codex 네이티브 로그는 중복 `response_item` 대신 `event_msg/item_completed`를 우선한다.
- Codex legacy 로그의 `external_agent_tool_call/result` 마커는 발생 순서로 짝짓는다.
- Codex transcript 형식은 안정 API가 아니므로 미지의 completed item도 중립 도구 이벤트로
  보존한다.
- 기록 시각은 로컬 타임존으로 표시하고, 날짜는 프롬프트 경계에서만 바꿔 한 턴을 쪼개지
  않는다.
- 세션 디렉터리는 ID 앞 8자가 아니라 전체 ID의 SHA-256 앞 8자를 쓴다.
- 기존 `~/claude-prompt-logs`는 이동하거나 지우지 않는다.
