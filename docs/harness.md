# Kujin Light Harness

이 프로젝트에는 하네스가 설치돼 있다. 아래 설치 예시와 달리 `verify.sh`는
`TEST_CMD=(python -B scripts/export.py --selftest)`를 사용해 프로젝트와 하네스 테스트를
함께 실행한다. 로그 내보내기용 `hooks/hooks.json`은 별도로 유지한다.
복원 시 `.no-review`를 생성해 LLM 리뷰는 비활성화했다. 테스트 게이트는 계속 실행한다.

Claude Code와 Codex가 **같은 스크립트를 공유하는** 얇은 에이전트 하네스. 하는 일은 셋뿐이다.

1. **테스트 게이트** — 턴이 끝날 때 테스트를 돌리고, 실패하면 2회까지 재작업을 유도한다.
   그 뒤에도 실패하면 사용자에게 보고하고 멈춘다.
2. **LLM 리뷰** — 테스트가 통과하면 독립된 리뷰어(Codex 또는 Claude)가 변경분을 리뷰한다.
   `blocker`/`major` 지적이 있으면 2회까지 재작업을 유도한다. `minor`는 보고만 한다.
   **리뷰어는 읽기 전용이다** — 프롬프트로 부탁하는 게 아니라 `--sandbox read-only`
   (Codex) / `--restricted --tools` (Claude)로 도구 차원에서 막는다. 수정은 Worker만 한다.
3. **커밋 차단** — 에이전트의 `git commit`/`push`를 막는다. 커밋은 사람이 직접 한다.

## 파일

```
.claude/settings.json          Claude Code 배선 (Stop, PreToolUse, permissions.deny)
.codex/hooks.json              Codex 배선 — 같은 스크립트를 가리킨다
.codex/windows.ps1             Windows 진입점 — Git Bash 경로·환경·종료 코드 전달
.claude/hooks/gate.sh          Stop 진입점. 테스트 → 리뷰 순차 실행 + 재귀 방어
.claude/hooks/verify.sh        테스트 게이트
.claude/hooks/review.sh        LLM 리뷰
.claude/hooks/review_runner.py 캐시·재작업·미완료 상태 관리
.claude/hooks/review_context.py diff·규칙 수집, 입력 크기 제한, 자격 증명 경로 제외
.claude/hooks/review_process.py 리뷰어 호출·시간 제한·응답 검증
.claude/hooks/review-prompt.md 리뷰 지시문
.claude/hooks/review-schema.json  리뷰 결과 스키마 (Codex --output-schema)
.claude/hooks/block-git.sh     커밋/푸시 차단 진입점 (PreToolUse)
.claude/hooks/block_git.py     git 명령 판정기 (셸 구문 파서)
.claude/hooks/tests/           회귀 테스트 (셸 3종 + Python 3종)
.githooks/pre-commit           보호 경로·하드코딩 시크릿 커밋 차단
.githooks/commit-msg           제목 72자 상한
```

## 설치

```bash
# 1) 위 파일들을 프로젝트 루트에 복사
# 2) 실행 권한
chmod +x .claude/hooks/*.sh .claude/hooks/tests/*.sh .githooks/*
# 3) git 훅 활성화
git config core.hooksPath .githooks
# 4) 런타임 산출물 무시
printf 'logs/\n.no-review\n' >> .gitignore
```

**필수 수정 한 줄** — `.claude/hooks/verify.sh`의 `TEST_CMD`를 프로젝트 테스트 명령으로 바꾼다.

```bash
TEST_CMD=(python -B -m unittest discover -s .claude/hooks/tests)   # 이 저장소의 테스트. 적용 대상에 맞게 변경.
```

리뷰 지시문(`review-prompt.md`)은 프로젝트 규약을 `AGENTS.md`에서 읽도록 되어 있다.
그 파일에 규약을 써두면 리뷰어가 그 기준으로 판단한다.

## 스위치

| 하고 싶은 것 | 방법 |
|---|---|
| 리뷰 끄기 | `touch .no-review` (즉시 적용) |
| 리뷰 켜기 | `rm .no-review` |
| 리뷰 끄기(스크립트/CI) | `NO_REVIEW=1` |
| 리뷰어 바꾸기 | `HARNESS_REVIEWER=codex` 또는 `HARNESS_REVIEWER=claude` |
| 리뷰 모델 지정 | `HARNESS_REVIEW_MODEL=<모델>` (미지정 시 CLI 기본 모델) |
| 리뷰 추론 수준 | `HARNESS_REVIEW_REASONING_EFFORT=<수준>` (미지정 시 CLI 기본값) |
| 같은 입력 재검토 | `HARNESS_REVIEW_CACHE_EPOCH=<새 값>`으로 캐시 세대 변경 |
| 커밋하기 | 에이전트는 막혀 있다. 사람이 `!git commit ...`로 직접 실행 |
| 재시도 횟수 | 테스트는 `verify.sh`, 리뷰는 `review_runner.py`의 `MAX_RETRY` |

자동 Stop 리뷰의 기본값은 Claude Code 작업에 Codex, Codex 작업에 Claude다.
`HARNESS_REVIEWER`에 유효한 값을 지정하면 운영체제와 호출 앱에 관계없이 그 값을 우선한다.
빈 문자열이나 지원하지 않는 값은 호출 앱의 기본 리뷰어로 돌아간다.
`review.sh`를 직접 실행해 호출 앱의 기본값도 없으면 Codex를 사용한다.
`HARNESS_REVIEW_MODEL`은 작업 에이전트가 아니라 **리뷰어**의 모델을 지정한다.
미지정 시 리뷰어 CLI의 기본 모델을 사용한다.
`HARNESS_REVIEW_REASONING_EFFORT`는 Codex 리뷰어에게 `model_reasoning_effort`,
Claude 리뷰어에게 `--effort`로 전달한다. 지원 수준은 선택한 모델에 따라 다르다.

### 예시

아래 환경변수는 같은 셸에서 새로 시작하는 CLI에 적용된다. 이미 실행 중인 앱에는 전달되지 않는다.

```bash
# Codex 작업의 기본 리뷰어(Claude)를 Codex로 바꾼다.
HARNESS_REVIEWER=codex codex

# Claude Code 작업의 기본 리뷰어(Codex)를 Claude로 바꾼다.
HARNESS_REVIEWER=claude claude

# 이번 Codex 작업의 Claude 리뷰 모델과 추론 수준을 지정한다.
HARNESS_REVIEW_MODEL=claude-sonnet-5 HARNESS_REVIEW_REASONING_EFFORT=medium codex

# 이번 Claude Code 작업의 Codex 리뷰 모델과 추론 수준을 지정한다.
HARNESS_REVIEW_MODEL=gpt-6-luna HARNESS_REVIEW_REASONING_EFFORT=medium claude

# 이번 실행에서 리뷰를 끈다.
NO_REVIEW=1 codex

# 같은 변경을 새 캐시 세대로 다시 검토한다.
HARNESS_REVIEW_CACHE_EPOCH=retry-1 codex
```

```powershell
# 현재 PowerShell에서 이후에 시작하는 CLI의 리뷰어를 Codex로 지정한다.
$env:HARNESS_REVIEWER = 'codex'
codex

# 환경변수를 지워 기본 교차 리뷰로 돌아간다.
Remove-Item Env:HARNESS_REVIEWER

# 현재 PowerShell에서 새로 시작하는 Codex 작업의 Claude 리뷰 모델과 수준을 지정한다.
$env:HARNESS_REVIEW_MODEL = 'claude-sonnet-5'
$env:HARNESS_REVIEW_REASONING_EFFORT = 'medium'
codex
Remove-Item Env:HARNESS_REVIEW_MODEL
Remove-Item Env:HARNESS_REVIEW_REASONING_EFFORT

# Claude Code 작업의 Codex 리뷰 모델과 추론 수준을 지정한다.
$env:HARNESS_REVIEW_MODEL = 'gpt-6-luna'
$env:HARNESS_REVIEW_REASONING_EFFORT = 'medium'
claude
Remove-Item Env:HARNESS_REVIEW_MODEL
Remove-Item Env:HARNESS_REVIEW_REASONING_EFFORT

# 프로젝트 리뷰를 파일 스위치로 끄고 다시 켠다.
New-Item .no-review -ItemType File -Force | Out-Null
Remove-Item .no-review
```

## 요구 사항

`bash`, `git`, `jq`, Python 3.11 이상(표준 라이브러리만 사용).
리뷰를 쓰려면 `codex` CLI(로그인) 또는 `claude` CLI.
없으면 리뷰는 **비차단으로 건너뛴다**(fail-open) — 리뷰어가 죽었다고 세션이 막히지 않는다.
반면 `pre-commit`은 fail-closed다. 안전장치와 품질 레이어는 실패 정책이 달라야 한다.

## 설계 메모

- **Stop 훅은 하나만 등록한다.** Claude Code는 같은 이벤트의 훅을 병렬 실행하므로
  훅을 2개 등록하면 "테스트 먼저"가 깨진다. `gate.sh`가 한 프로세스에서 순차 호출한다.
- **커밋 차단의 주력은 PreToolUse 훅이다.** `permissions.deny`의 Bash 패턴은
  `git -c x=y commit`·`/bin/git commit`·`sh -c '...'`로 우회된다(공식 문서가 명시).
  판정은 `block_git.py`가 한다. 따옴표를 지우고 공백으로 쪼개면 `git -c 'user.name=Jane Doe'
  commit`의 옵션 값 `Doe`를 하위 명령으로 오인해 통과시키므로, `shlex`로 따옴표 경계를
  보존해 파싱한다. 리다이렉션(`git >/dev/null commit`), 치환·eval, 셸 이스케이프
  (`g\it push`), 결합 옵션(`bash -lc 'git push'`)도 같은 파서에서 잡는다.
  `git log --grep=commit` 같은 조회는 통과시킨다. 해석할 수 없는 구문은 차단한다.
- **재귀 방어 2겹.** 리뷰어 자식 세션의 Stop 훅이 다시 `gate.sh`를 타면 무한 재귀가 된다.
  `HARNESS_REVIEW_CHILD=1` 환경변수(1차)와 `logs/.review-running` 락(2차, 20분 만료)으로 막는다.
  락은 **리뷰 단계에만** 걸어서 동시에 열린 다른 세션이 테스트까지 건너뛰지 않게 한다.
- **`verify.sh`의 종료 코드 3**은 "실패했지만 재시도를 소진해 이미 보고함"이다.
  0(통과)과 구분해야 `gate.sh`가 보고 후에 리뷰 루프를 새로 시작하지 않는다.
- **Windows 주의.** 네이티브 jq는 CRLF로 출력해서 `read`의 마지막 변수에 `\r`이 남는다.
  jq 출력을 비교에 쓰는 곳은 전부 `tr -d '\r'`을 거친다.

## 알려진 한계

- **리뷰 비용.** 아래 입력·턴·시간 제한은 총 토큰 수나 구독 사용량의 정확한 상한이 아니다.
  실제 절감률은 `logs/review-usage.jsonl`로 측정한다. 추가 파일 읽기 범위는 프롬프트 지침이다.
- Codex 훅의 `exit 2` 차단과 재작업 시작은 Windows CLI에서 실측했다.
  재시도 소진까지의 전체 반복은 아직 실측하지 않았다.
- 강제 종료(SIGKILL)는 `trap`을 건너뛰므로 `logs/.review-running` 락이 남는다.
  20분 만료로 자동 해소되지만, 그 사이 리뷰는 조용히 생략된다.

## 자동 리뷰 예산 (2026-09-18)

Stop 훅 자동 실행은 유지한다. 테스트 후 Git 변경이 없으면 리뷰를 생략한다.
스테이징 diff와 작업 트리 diff를 따로 수집하고 새 파일은 추가 diff로 제공한다.
삭제·이름 변경도 수집하며 Git 인덱스와 작업 파일은 수정하지 않는다.
Ignore/, logs/, reviews/, MESSAGE, .no-review, __pycache__는 리뷰 대상에서 제외한다.

| 항목 | 기본 동작 |
|---|---|
| 최초 입력 | 규칙·스키마·diff·주변 12줄을 합쳐 UTF-8 10 MiB 이하 |
| 추가 탐색 | 관련 파일 최대 3개, Read 한 번에 200줄, 검색 결과 100줄이라는 지침 |
| Claude 턴 상한 | `--max-turns 8`로 강제. Codex 리뷰에는 이 옵션을 적용하지 않음 |
| 리뷰 시간 | Claude/Codex 모두 120초. 초과 시 자식 프로세스도 종료 |
| 같은 입력 | 내용 해시가 같으면 결과 재사용. 미완료·지적도 그대로 보존 |
| 지적 범위 | 이번 변경이 만들거나 악화시킨 결함. 무관한 기존 규약 위반 제외 |

10 MiB는 최초 사용자 입력의 바이트 수다. CLI 시스템 프롬프트, 도구 정의,
추가 탐색 결과·출력·사고 토큰은 이 한도에 포함되지 않는다.
이 값은 어떤 모델의 컨텍스트보다도 크므로 상한으로서의 보호 기능은 사실상 없다.
한도가 실제로 채워지면 리뷰어 CLI가 API 오류로 실패하고 미완료(비차단)로 떨어진다.

`.env`·`.env.*`·`id_rsa`·`*.pem`·`credentials`처럼 자격 증명으로 보이는 경로는
패킷에 싣지 않는다. `.gitignore`는 `git add -f`를 막지 못하고 pre-commit 검사는
리뷰보다 늦기 때문이다. 변경 사실은 파일명만 리뷰어에게 알린다.
추가 탐색의 파일·줄 수는 강제 카운터가 아니다. 강제 제한은 입력 크기·Claude 턴·시간이다.
Claude 호출은 읽기 도구만 노출하고 MCP·슬래시 명령을 끈다. 프롬프트는 stdin으로 전달한다.

규칙·리뷰 코드·스키마·인덱스·Git 대상 파일 내용·리뷰어 버전·모델 지정·제한값이 바뀌면
캐시를 다시 만든다. 관련 파일의 변경도 놓치지 않도록 Git 대상 파일 전체를 로컬에서 해시한다.
전체 파일을 모델에 전송하는 것은 아니다. 변경 중인 입력의 결과는 통과로 처리하지 않는다.
CLI 기본 모델이나 외부 관리 설정이 별도로 바뀌면 모델을 명시하거나 캐시 세대를 바꾼다.

입력 초과·바이너리·실행 오류·시간/턴 초과는 **검토 미완료**로 보고한다.
일부만 잘라서 통과시키지 않는다. 기존 fail-open 정책대로 종료 코드는 0이지만,
이는 리뷰 통과가 아니라 에이전트 종료 허용이다. 같은 입력을 즉시 유료 재시도하지 않는다.
입력이 크면 작업 변경을 나누거나 사람이 커밋으로 기준점을 정리해야 한다.
실행 장애를 해결한 뒤 같은 내용으로 재검토하려면 캐시 세대를 변경한다.

blocker/major는 캐시 결과도 재작업을 유도한다. 기존 최대 2회 수정과 최종 보고를 유지한다.
소진 후 같은 입력은 미해결 상태를 알리고 종료를 허용하며, 새 입력은 다시 검토할 수 있다.
캐시는 `logs/.harness-review/`, 사용량은 `logs/review-usage.jsonl`에 저장한다.
사용량에는 가능한 경우 입력·출력·캐시 토큰, 턴, 시간, API 비용을 기록하며 원문은 넣지 않는다.
캐시 재사용 행에는 과거 토큰 사용량을 중복 계상하지 않는다. 로그와 캐시는 커밋하지 않는다.

검증: `python -B -m unittest discover -s .claude/hooks/tests -t .claude/hooks/tests`
(유료 호출 없음). 셸 진입점 테스트 `test-gate.sh`, `test-block-git.sh`도 유지한다.
Claude CLI 옵션은 로컬 2.1.280과 [공식 CLI 문서](https://code.claude.com/docs/en/cli-reference)
기준이다. 2026-09-24 Windows 임시 저장소에서 Claude Code → Codex 리뷰의 완료와
Codex 0.156.1 → Claude 리뷰의 `blocker` 차단·재작업 시작을 실측했다.
재시도 소진까지의 실제 앱 동작은 실측하지 않았다.

## Windows에서 훅 실행

Claude Code와 Codex의 Windows용 `commandWindows`는 `.codex/windows.ps1`을 호출한다.
이 진입점은 `%ProgramFiles%/Git/bin/bash.exe`를 직접 선택하며 PATH의 WSL Bash는 쓰지 않는다.
Git을 다른 위치에 설치했다면 `HARNESS_GIT_BASH`에 해당 `bash.exe` 경로를 지정한다.
Git Bash 로그인 환경에서 기존 셸 훅을 실행하므로 Unix 도구의 PATH도 구성된다.
저장소 루트는 Git과 진입점 파일 위치로 정하며, 하위 폴더·공백 경로도 지원한다.
Claude Code는 Codex를, Codex는 Claude를 기본 리뷰어로 전달한다.
`HARNESS_REVIEWER`가 유효하면 이 기본값보다 우선하며, stdin과 종료 코드(특히 차단 코드 2)는 그대로 전달한다.

PowerShell 5.1에서는 native 명령의 중첩 따옴표가 손실될 수 있어 `bash -lc` 명령 문자열 대신
`bash --login <스크립트 경로>`로 호출한다. 회귀 검사는 다음과 같다(유료 호출 없음).

```powershell
python -B .claude/hooks/tests/test_windows_hooks.py -v
```

설정 파일을 수정하면 Codex가 정확한 훅 정의의 신뢰를 다시 요구한다.
CLI의 `/hooks`에서 이 저장소의 PreToolUse와 Stop을 검토하고 신뢰해야 활성화된다.
설정의 신뢰 해시를 직접 고치거나 신뢰 검사를 우회하지 않는다.
[공식 훅 문서](https://learn.chatgpt.com/docs/hooks)의 `commandWindows`와 신뢰 절차를 따른다.
네이티브 PowerShell 실행 검증과 Codex 앱의 실제 이벤트 발화 검증은 구분한다.
