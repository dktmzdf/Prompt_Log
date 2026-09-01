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
{"seq": 12, "agent": "codex", "session_id": "...", "kind": "tool", "name": "Bash", "input": {}, "output": "...", "ok": true}
```

도구 결과는 재현성을 위해 요약하지 않고 4KB를 넘을 때 가운데만 절단합니다.

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

# 다른 출력 루트에서 드라이런
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

표준 라이브러리만 사용하며 별도 Python 패키지가 필요하지 않습니다.
