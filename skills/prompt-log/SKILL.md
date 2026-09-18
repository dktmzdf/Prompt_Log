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
- `.jsonl`: `agent`와 `session_id`가 포함된 중립 이벤트 스트림. 도구 입력 문자열·출력·
  생각은 UTF-8 기준 4,096바이트를 넘으면 가운데를 절단한다(생략 안내문은 한도 외).
  프롬프트·답변은 자르지 않는다.
- 도구 `status`는 `success`/`failed`/`in_progress`/`unknown`, 호환 필드 `ok`는
  `true`/`false`/`null`이다. 결과가 없거나 구형 마커만 있어 성공 여부를 알 수 없으면
  성공으로 단정하지 않는다. 수정 파일 집계도 확인된 성공 호출만 센다.

`Stop` 훅은 응답이 끝날 때 현재 세션 리포트를 갱신한다. 훅 명령은 Codex의
`PLUGIN_ROOT`와 Claude Code의 `CLAUDE_PLUGIN_ROOT`를 모두 지원한다.

## 수동 실행

Python 3.10 이상을 사용한다. 아래 상대 경로는 플러그인 루트 기준이며, 다른 작업
폴더에서는 이 스킬 위치에서 두 단계 위의 `scripts/export.py` 절대 경로를 사용한다.
`scripts/export.py`만 복사하지 말고 `scripts/promptlog/` 패키지도 함께 유지한다.

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
- Codex 누적 토큰은 차이만 합산한다. 누적값 없이 개별 사용량만 있는 경우의 중복은
  확정할 수 없다. legacy 인자의 `_raw`는 모호한 여러 줄 입력을 보존한 본문이다.
- Codex transcript 형식은 안정 API가 아니므로 미지의 completed item도 중립 도구 이벤트로
  보존한다.
- 기록 시각은 로컬 타임존으로 표시하고, 날짜는 프롬프트 경계에서만 바꿔 한 턴을 쪼개지
  않는다.
- 세션 디렉터리는 ID 앞 8자가 아니라 전체 ID의 SHA-256 앞 8자를 쓴다.
- 기존 `~/claude-prompt-logs`는 이동하거나 지우지 않는다.
- `--output`은 지정한 위치에 실제 리포트를 쓴다. 원본 로그는 수정하지 않는다.
- 수동 실행·백필·`--selftest` 실패는 0이 아닌 종료 코드다. 훅 모드만 오류를 stderr로
  알리고 `{"continue": true}`와 종료 코드 0으로 끝낸다.
