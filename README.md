# prompt-log

Claude Code와 Codex의 세션 기록을 사람이 읽는 날짜별 리포트로 내보내는 범용 플러그인입니다.

로그를 새로 수집하지 않습니다. 각 에이전트가 이미 남긴 JSONL을 읽어 요약 `.md`와
중립 이벤트 `.jsonl`을 만들며, 원본 로그와 기존 `~/claude-prompt-logs`는 건드리지 않습니다.

## 지원 형식

| 에이전트 | 원본 | 처리 방식 |
|---|---|---|
| Claude Code | `~/.claude/projects/<프로젝트>/<세션ID>.jsonl` | compact/resume 정렬 및 조상 세션 중복 제거 |
| Codex | `~/.codex/sessions/**/*.jsonl` | native `item_completed`와 가져온 legacy 세션 지원 |

Codex transcript는 안정된 공개 포맷이 아니므로 알려진 이벤트는 정규화하고, 새 종류의
completed item도 버리지 않고 일반 도구 이벤트로 보존합니다.

## 출력 구조

```text
~/agent-prompt-logs/
└── <agent>/                         # claude 또는 codex
    └── <project>/
        └── <YYYY-MM-DD>/
            └── <HH-MM>_<session-hash8>/
                ├── <project>-<date>-<session-hash8>.md
                └── <project>-<date>-<session-hash8>.jsonl
```

`session-hash8`은 전체 세션 ID의 SHA-256 앞 8자입니다. 시간 접두사가 같은 Codex 세션도
충돌하지 않습니다. 여러 날 이어진 세션은 프롬프트 경계 기준으로 날짜별 분리되며 한 턴은
쪼개지지 않습니다.

### Markdown 리포트

에이전트·세션·클라이언트, 기간, 모델과 토큰, 도구 사용, 수정 파일, 프롬프트·응답·생각·
도구 호출 타임라인을 담습니다.

### JSONL 리포트

Claude와 Codex에 공통인 다음 이벤트 형태로 내보냅니다.

```json
{"seq": 12, "agent": "codex", "session_id": "...", "kind": "tool", "name": "Bash", "input": {}, "output": "...", "status": "success", "ok": true}
```

도구 상태는 `success`, `failed`, `in_progress`, `unknown`으로 구분합니다.
호환 필드 `ok`는 성공이면 `true`, 실패면 `false`, 미확정이면 `null`입니다.
수정된 파일 집계에는 성공이 확인된 호출만 포함됩니다.

도구 입력의 각 문자열·도구 출력·생각은 UTF-8 기준 4,096바이트를 넘으면 앞 3,072바이트와
뒤 1,024바이트 이내의 완전한 문자를 남깁니다. 생략 안내문 길이는 이 한도에 포함하지
않습니다. 프롬프트·답변은 자르지 않으며, 어댑터와 공통 집계 단계에서는 절단하지 않습니다.

## 설치

### Claude Code

```bash
claude plugin marketplace add <이 저장소 경로 또는 URL>
claude plugin install prompt-log@prompt-log
```

### Codex

이 저장소는 `.codex-plugin/plugin.json`, `hooks/hooks.json`, `skills/prompt-log/SKILL.md`를
포함합니다. Codex 플러그인 마켓플레이스에 이 저장소를 등록한 뒤 `prompt-log`를 설치합니다.

두 환경 모두 `Stop` 훅이 응답 완료 시 현재 transcript를 다시 읽어 리포트를 갱신합니다.
훅은 Codex의 `PLUGIN_ROOT`와 Claude Code의 `CLAUDE_PLUGIN_ROOT`를 자동 선택합니다.

## 수동 실행

```bash
# Claude + Codex 전체 백필
python scripts/export.py --all

# 한 에이전트만 백필
python scripts/export.py --all --agent codex
python scripts/export.py --all --agent claude

# 특정 세션(내용으로 형식 자동 판별)
python scripts/export.py <session.jsonl>

# 다른 출력 루트에 시험 리포트 생성 (실제로 파일을 씁니다)
python scripts/export.py <session.jsonl> --output <temporary-directory>

# 내장 회귀검사
python scripts/export.py --selftest
```

Windows에서는 `python`, macOS/Linux에서는 환경에 따라 `python3`를 사용하면 됩니다.

## 설계상 중요한 점

- Claude JSONL은 compact/resume 때문에 시간순이 아닐 수 있어 정렬 후 처리합니다.
- Claude 도구 호출과 결과는 순서가 아닌 `tool_use_id`로 연결합니다.
- 첨부가 있는 Claude 프롬프트와 도구 거부에 포함된 추가 사용자 지시도 복원합니다.
- Codex 네이티브 로그는 중복된 `response_item`보다 완결된 `item_completed`를 우선합니다.
- Codex legacy 로그의 `external_agent_tool_call/result` 마커도 도구 이벤트로 복원합니다.
- 파일 경로에 포함된 UUID는 재현에 필요하므로 보존하고, 원본의 구조적 호출 UUID는 제거합니다.
- 결과 생성은 멱등입니다. 같은 세션을 다시 내보내면 같은 경로의 리포트를 갱신합니다.
- Codex 누적 토큰은 이전 합계와의 차이만 더합니다. 누적값이 없는 기록은 개별 사용량으로
  처리하므로, 값이 같다는 이유만으로 정상적인 별도 사용량을 제거하지 않습니다.
- 수동 실행·백필·자기검사 실패는 비정상 종료 코드로 알립니다. `Stop` 훅에서 발생한
  리포트 오류는 stderr에 남기고 세션을 차단하지 않습니다.

Python 3.11 이상이 필요합니다. 리포트 내보내기 자체는 표준 라이브러리만으로 동작하며
별도 실행 의존성이 없습니다. 검색·질의 기능은 외부 라이브러리를 사용합니다.

## 코드 구조와 공부 순서

`scripts/export.py`는 CLI·훅 진입점이고, 구현은 `scripts/promptlog/`에 있습니다.

1. `models.py`: 어댑터가 반환하는 `SessionLog`, `Session`, `Event` 계약
2. `adapters/claude.py`, `adapters/codex.py`: 원본 JSONL → 공통 이벤트 변환
3. `assemble.py`: 두 소스가 공유하는 날짜·대화 묶음·통계 집계
4. `render.py`, `text.py`: 출력 직전 절단과 Markdown·JSONL 생성
5. `service.py`, `storage.py`, `cli.py`: 전체 연결·파일 저장·실행 모드

리팩터링 이유, 동작 차이, 검증 결과와 후속 작업은 [작업 문서](docs/refactoring.md)에
기록했습니다. 전체 회귀검사는 `python -B scripts/export.py --selftest` 또는
`python -B -m unittest discover -s tests -v`로 실행합니다.
