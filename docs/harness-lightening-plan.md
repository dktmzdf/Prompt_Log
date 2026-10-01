# carve-harness 경량화 — 분석 및 적용 계획

> **상태: §4 적용 완료 (2026-09-17).** 83개 경로 → 5개 파일. §5 검증 전항목 통과.
> 실행 기록은 `jobs/2026-09-17-harness-lightening.md` §5 참조.
> 남은 것: 고아 미러 100파일 처리(job §7), LLM 리뷰 레이어 구현(§6).

## Context

이 저장소(`Prompt_Log-Harness_example`)는 Claude Code·Codex 세션 로그를 Markdown/JSONL 리포트로
내보내는 **순수 Python CLI**다. 표준 라이브러리만 쓰고, 빌드 단계가 없고, 혼자 쓰며, CI가 없다.

여기에 제3자 범용 하네스 `carve-harness`를 **전체 구성(83개 경로)** 으로 설치했다. 이 하네스는
Java/Spring + Next.js + 팀 오케스트레이션 + LLM 채점 평가를 전제로 설계된 물건이라,
이 프로젝트에는 대부분이 사문(死文)이거나 작동하지 않는다.

사용자가 원하는 건 딱 하나다:

> **테스트가 있고, 테스트를 통과 못하면 다시 개발을 시도한다. 2번 시도 후 실패하면 사용자에게 알린다.**

이 문서는 (1) 하네스가 실제로 뭘 하는지, (2) 지금 안 돌아가는 게 뭔지, (3) Claude+Codex+Python+JSON
환경에서 필요한 게 뭔지, (4) 이 프로젝트에 어떻게 적용할지를 정리한다.

---

## 1. 이 하네스는 정확히 뭘 하는가

하네스는 스스로를 "3기둥"이라 부른다. 실제 배선 기준으로 정리하면 이렇다.

### 1-1. 실제로 훅에 배선된 것은 6개뿐

설치된 `.claude/hooks/*.sh`는 20개지만, `settings.json`에 이벤트로 연결된 건 6개다.
나머지 14개는 라이브러리(`lib-*.sh`), 수동 CLI(`harness-audit.sh`, `logs-report.sh`, `redteam.sh`),
또는 워크플로 전용(`eval-*.sh`, `carve-validate.sh`)이다.

| 이벤트 | 스크립트 | 하는 일 | 이 프로젝트에서의 실제 효과 |
|---|---|---|---|
| PreToolUse (`Write\|Edit\|MultiEdit\|NotebookEdit\|Bash`) | `pretool-guard.sh` | 보호 경로·위험 명령·시크릿 차단, exit 2 | **작동함.** 단 대상 대부분이 이 프로젝트에 없는 것들 |
| PostToolUse (`Write\|Edit`) | `posttool-format.sh` | `.py`면 `ruff format` | `.py` 편집 시에만 동작 |
| PostToolUse (`Write\|Edit`) | `posttool-slop.sh` | `.html/.css/.svg`면 slop 린터 | **실행 0회** — 이 프로젝트엔 해당 확장자 없음 |
| **Stop** | `stop-verify.sh` (timeout 900s) | 스택 게이트(빌드·테스트) 실행, 실패 시 exit 2 | **핵심. 단 현재 무동작** (§2-1) |
| **Stop** | `checklist-gate.sh` (30s) | `specs/checklist.json` 전 항목 95점 확인 | **완전 무동작** — 해당 파일 없음 |
| SessionStart / PreCompact / SessionEnd | `session-handoff.sh` | `specs/HANDOFF.md` 저장·복원 + 배너 | 작동함 |

추가로 `node vendor/ponytail/hooks/*.js` 3개가 SessionStart·UserPromptSubmit·SubagentStart에 걸려
"게으른 시니어" 페르소나를 주입한다. 이건 하네스 본체와 무관한 별도 플러그인이다.

### 1-2. 재시도 메커니즘의 실제 동작 — 사용자 요구사항과 가장 가까운 부분

`stop-verify.sh`가 테스트 실패 시 `exit 2`를 내면 Claude Code가 그 stderr를 모델에게 피드백하고
작업을 계속시킨다. 이게 "재시도"다. 무한루프 방지는 `lib-stop-guard.sh:12-20`의
`stop_loop_yield` 하나뿐이고, 로직은 이렇다.

```bash
# stdin의 Stop 이벤트 JSON에서 .stop_hook_active 만 본다
if [ "$(jq -r '.stop_hook_active // false')" = "true" ]; then
  exit 0    # 2번째 Stop이면 검증 실패 여부와 무관하게 종료 허용
fi
```

**따라서 현재 동작은 이렇다.**

```
테스트 실패 → exit 2 → Claude 재시도 (1회)
           → 다시 Stop → stop_hook_active=true → 무조건 exit 0 → 조용히 세션 종료
```

- **재시도 횟수를 세지 않는다.** 카운터가 없다. 불린 플래그 하나뿐이다.
- **상한은 하드코딩된 사실상 1회.** env로 조정 불가.
- **사용자에게 알리지 않는다.** 마지막 yield는 exit 0 경로라 stderr가 사실상 안 보인다.
  테스트가 깨진 채로 세션이 끝나도 사용자는 모른다.

"3회 시도 후 에스컬레이션"은 `carve-verify-loop.js:226-228`에 있지만, 이건 **훅이 아니라
Workflow 도구를 명시적으로 호출했을 때만** 도는 별도 경로다.

### 1-3. 검증 루프(`/verify-loop`)는 테스트가 아니라 LLM 채점 기반

사용자가 원하는 것과 이름이 비슷해서 헷갈리기 쉬운데, 성격이 다르다.

- 항목 점수 = 5축 합: `exists`25 + `match`25 + **`test`25** + `contract`15 + `no_regress`10
- 이 중 **`test` 축만 결정론**이다 (`carve-verify-loop.js:109-117`). 나머지 75점은 evaluator LLM의 주관 판정.
- 테스트가 깨지면 `test=0`이라 나머지 만점이어도 75 < 95 → 통과 불가. 여기까진 맞다.
- **그러나 역은 성립하지 않는다.** 테스트가 전부 통과해도 LLM이 6점만 깎으면 루프가 계속 돈다.
- 상한: 항목당 3회 / 외곽 8회 (`carve-verify-loop.js:19-20`). **"2회"는 어디에도 없다.**

즉 이건 "테스트 통과하면 끝"이 아니라 "LLM이 95점 줄 때까지"다. 사용자 요구와 다르다.

### 1-4. 설치 규모

`.claude/harness-manifest.txt` 기준 **83개 경로**.

| 구성 | 개수 | 비고 |
|---|---|---|
| hooks | 28 | 훅 20 + tests 33개 + stacks + `.githooks` + `settings.json` |
| commands | 14 | ponytail 6개 포함 |
| md (규칙·문서) | 13 | |
| orchestrator | 11 | 에이전트 7 + 워크플로 + `docs/md` 3 |
| skills | 10 | |
| core/기타 | 7 | VERSION, install.sh, vendor, packs 등 |

---

## 2. 지금 당장 돌아가지 않는 기능

사실 확인된 것만. 추측 아님.

### 2-1. Python 게이트 — 무동작 (가장 중요)

`.claude/stacks/python.sh:17`이 첫 줄에서 빠져나간다.

```bash
{ [ -f pyproject.toml ] || [ -f requirements.txt ] || [ -f setup.py ] || [ -f setup.cfg ]; } || return 0
```

이 저장소엔 **네 파일이 하나도 없다**(표준 라이브러리만 쓰는 스크립트 구조). 실측 결과:

```
stack_gate  rc=0   ← 아무것도 안 돌고 즉시 통과
stack_detect rc=1  ← 파이썬 프로젝트로 인식조차 안 됨
```

`install.sh setup`이 출력한 `Python: 게이트 활성 (ruff OK)`는 **ruff 설치 여부만 보고 한 판단이라
사실과 다르다.** 즉 지금 Stop 훅은 테스트를 전혀 안 돌리고 있다.

참고로 마커 파일을 만들어 게이트를 켜면 `ruff check .`에서 **43개 에러**가 나와 매 턴 Stop이 차단된다
(pytest는 36 passed로 정상). 그냥 켜면 안 된다.

### 2-2. eval 파이프라인 — 골든셋 glob 미스매치로 0건

- `carve-eval.js:15`의 GLOB은 `specs/goldenset/*.json`
- 실제 파일은 `specs/goldenset/starters/python.json` — **하위 디렉토리라 매치 안 됨**
- 게다가 그 파일 주석에 "이 파일은 수정하지 않는다(하네스 자산)"라고 쓰여 있다. 설계상 직접 채점 대상이 아니다.
- 결과: `/eval`을 치면 `{suiteScore: null, note: 'empty goldenset'}`로 즉시 종료
- `specs/eval-score.json`(baseline)도 없어서 `eval-gate.sh`는 항상 `unable`

### 2-3. checklist-gate — 완전 무동작

`specs/checklist.json`과 `specs/.checklist-active` 둘 다 없다. `checklist-gate.sh:31-36`의
가드에 걸려 항상 exit 0. 로그상 `Stop checklist fail` 0건.

### 2-4. eval-gate.sh의 CRLF 버그 (Windows 고유)

`.claude/hooks/tests/eval-gate.test.sh`의 `all-full` 케이스가 실패한다. 직접 추적한 결과:

```
+ '[' 3 -ge 3 ']'          ← NCASES=3 정상
+ '[' 0 = 3 ']'            ← NZERO 정상 (중간 필드)
+ '[' $'3\r' = 3 ']'       ← NFULL에 캐리지 리턴이 붙어 비교 실패
```

**근본 원인**: 이 환경의 jq(WinGet 네이티브 Windows 빌드)는 출력을 **CRLF**로 낸다
(`od -c` 확인: `o k \r \n`). `read -r A B C < <(jq ...)`는 마지막 변수에 `\r`이 남는다.
그래서 "전 케이스 100점 = 골든셋이 너무 쉽다" 경고가 영영 안 뜬다.

해당 패턴은 `.claude/hooks/eval-gate.sh:77`, `:84` 2곳뿐이다(`$(jq ...)` 형태는 Git Bash가
CR을 걸러줘서 무사). 단, eval 자체를 안 쓸 거면 무시해도 되는 버그다.

### 2-5. 안 맞는 에이전트·규칙 (내용이 다른 스택 전용)

| 대상 | 문제 |
|---|---|
| `security-reviewer.md` | 절반이 Spring Gateway 전용. 참조하는 `.claude/rules/java-spring/gateway-testing.md` **없음** |
| `pr-test-analyzer.md` | 본문 전체가 Spring Gateway 전용(WireMock, Testcontainers) |
| `fable-*` 4개 | Workflow 도구 + worktree 격리 전제. 일반 세션에서 자동 동작 안 함 |
| `fable-researcher` | WebSearch/WebFetch 필수 |
| `.claude/rules/safety.md` (**항상 로드**) | 59줄 중 대부분이 Flyway·JPA·`build.gradle`·`next.config.ts`·Redis·`ai_analysis_logs` 등 해당 없음. 실제 유효한 건 rm -rf·git·시크릿 4~5줄 |
| `.claude/rules/README.md` | 존재하지 않는 규칙 6종(`database.md`, `java-spring/`, `react-next/`, `go/`, `rust/`)을 나열 |
| `docs/rules/.../README.md` | "10종 스택 문서" 주장, 실제 2종(python, fastapi). fastapi도 이 프로젝트와 무관 |
| `docs/evaluator/python-example/` | **OpenAI API**(`OPENAI_API_KEY`, `gpt-4o-mini`) 쓰는 데모. carve-eval과 코드 연결 없음 |
| `version-changelog` 스킬 | `CHANGELOG.md` 없어서 대응 게이트 미발화 |
| `carve-guide` 스킬 | 참조하는 `carve-workflow-guide.html` 이 저장소에 없음 |

### 2-6. git 커밋 차단 — **결함이 아니라 의도된 동작. 유지한다.**

> **사용자 확정 (2026-09-17)**: "막는 게 맞다. 내가 명시적으로 허용할 때만 올릴 거임."
> 따라서 아래는 고칠 문제가 아니라 **지켜야 할 요구사항**이다. §4의 제거 계획이 이걸
> 깨뜨리지 않도록 설계를 바꿨다(§4-1-A).

**⚠️ 함정**: `uninstall.sh`를 그냥 돌리면 이 보호가 **조용히 꺼진다.**

`lib-protected.sh:19`가 `.claude/**` 자기보호를 **매니페스트 존재 조건**으로 걸어놨다:

```bash
if [ -f "$(dirname "${BASH_SOURCE[0]}")/../harness-manifest.txt" ]; then
  PROTECTED_RE="(${PROTECTED_RE}|\.claude/(hooks/|stacks/|settings\.json|harness-manifest\.txt))"
fi
```

그런데 `uninstall.sh:63`이 `harness-manifest.txt`를 지운다. 게다가 `.githooks` 자체와
`lib-protected.sh`도 매니페스트 대상이라 같이 사라지고, `core.hooksPath`도 해제된다(`uninstall.sh:47`).
즉 **제거 후엔 `git add .`이 그냥 통과해버린다.** 이걸 막으려고 §4-2에서 자립형 `pre-commit`을
새로 쓴다.

#### 현재 차단 동작 (유지 대상)

`.githooks/pre-commit:19-31`이 `PROTECTED_RE` 매치 경로의 스테이징을 차단하는데,
이 저장소는 `.claude/harness-manifest.txt`가 있어서 보호 범위가 확장된다.

```
.claude/(hooks/|stacks/|settings\.json|harness-manifest\.txt)
```

그런데 `.gitignore`의 하네스 블록은 **매니페스트·버전 기록만 무시하고
`.claude/hooks/`·`.claude/settings.json`은 무시하지 않는다.** 결과:

| 시나리오 | 결과 |
|---|---|
| `git add . && git commit` | **차단** — `.claude/hooks/*`, `settings.json`이 staged됨 |
| `git add scripts/ tests/ && git commit -m "fix: ..."` | 통과 |
| `git commit -m "하네스 정리"` | **차단** — Conventional Commits 강제 (`commit-msg:25`) |
| `git commit --no-verify`로 우회 | 세션 내에서는 `pretool-guard.sh`가 Bash 단계에서 차단 |

즉 **하네스 파일 자체를 커밋하려면 매번 수동으로 경로를 골라내야 한다.** — 이게 바로 원하는 동작이다.

### 2-7. 기타

- ~~**shellcheck 미설치**~~ → **해결됨 (2026-09-17)**. `winget install koalaman.shellcheck`로
  **0.11.0 설치 완료**. 설치 직후 전수 검사 결과 `.claude/hooks/*.sh`·`.claude/stacks/*.sh`·`.githooks/*`
  전부 `-S error` **통과(rc=0)** — 켜도 새로 막히는 건 없다.
  (PATH 반영은 셸 재시작 후. 그전까지는 `command -v shellcheck`가 실패해 게이트가 스킵된다.)
- `.claude/hooks/tests/*.test.sh` **33개 전수 실행**이 매 Stop마다 도는 실제 부하였고, 이번 세션의
  연쇄 차단(`carve-harness-create` → `carve-validate` → `eval-gate` → `eval-java`) 원인이었다.
  현재 `bash.sh`에서 `CARVE_RUN_SELFTESTS=1`일 때만 돌도록 꺼둔 상태다.
  - 근본 원인 중 하나는 **팩 제외의 비대칭**이다: java-spring 팩 미선택으로 `.claude/hooks/eval-java.sh`는
    빠졌는데 그것을 검사하는 `tests/eval-java.test.sh`는 안 빠져서 12/12 실패했다.
- `logs/2026-09-16.jsonl:23`에 `}` 한 글자짜리 깨진 줄이 있어 jq 전수 파싱이 실패한다. `log-event.sh`의 `>>` append에 잠금이 없어 Stop 훅 2개가 병렬로 쓰면서 인터리브된 것으로 보인다.
- ponytail 커맨드가 `.claude/commands/*.md` 6개 + `vendor/ponytail/commands/*.toml` 6개 + 플러그인 활성으로 **3중 중복**이다.

---

## 3. Claude + Codex + Python + JSON 만 쓸 때 필요한 것

이 기준으로 83개 경로를 분류하면 이렇게 된다.

### 3-1. 반드시 남길 것 (사용자 요구사항의 본체)

| 대상 | 이유 |
|---|---|
| `.claude/settings.json` | 훅 배선의 유일한 정본 |
| `.claude/hooks/stop-verify.sh` (또는 이를 대체할 최소 훅) | **테스트 게이트 = 요구사항 그 자체** |
| `.claude/hooks/lib-stop-guard.sh` | 루프 가드. 단 재시도 카운트를 넣으려면 수정 필요 |
| `.claude/stacks/python.sh` | 테스트 실행 정의. 단 §2-1 때문에 수정 필요 |
| `.claude/hooks/log-event.sh` | 다른 훅들이 호출하는 최소 로깅 |
| `CLAUDE.md` / `AGENTS.md` | 프로젝트 지침. Codex도 `AGENTS.md`를 읽는다 |
| `codex.md` / `.cursorrules` | AGENTS.md를 가리키는 1줄 포인터. Codex 병행 사용 시 유효 |
| **`.githooks/pre-commit` + `core.hooksPath`** | **§2-6 — 사용자 명시 요구.** 보호 경로·시크릿 커밋 차단. 단 매니페스트 의존을 끊은 자립형으로 다시 쓴다 |

### 3-2. 남겨도 되는 것 (저비용 + 실제 효용)

| 대상 | 이유 |
|---|---|
| `.claude/hooks/pretool-guard.sh` | 실수로 `rm -rf`·force push·시크릿 커밋 막는 값어치. 단 차단 목록의 절반은 사문 |
| `.claude/hooks/session-handoff.sh` | 세션 인계. 이 프로젝트 성격(로그 도구)과 잘 맞음 |
| `.claude/rules/common/security.md`, `testing.md` | 짧고 언어 무관 |
| `.githooks/commit-msg` | Conventional Commits. 쓸지 말지는 취향 (§4-2-5에 그대로 유지안 포함) |

### 3-3. 잘라낼 것

| 대상 | 개수 | 이유 |
|---|---|---|
| eval 파이프라인 전체 (`eval-*.sh`, `carve-validate.sh`, `specs/goldenset/`, `docs/evaluator/`) | 7+ | §2-2. 골든셋 LLM 채점은 사용자가 명시적으로 "너무 갔다"고 함 |
| `.claude/agents/` 7개 | 7 | fable 4개는 Workflow 전제, security-reviewer·pr-test-analyzer는 Spring 전용, evaluator는 eval용 |
| `.claude/workflows/` 3개 | 3 | Workflow 도구 옵트인 전제. 한 번 돌면 11~14 에이전트 스폰 |
| `docs/md/` 3개 | 3 | 위 워크플로 가이드 |
| `checklist-gate.sh` + `checklist-loop` 스킬 + `/verify-loop` | 3 | §1-3, §2-3. LLM 채점 루프 |
| `eval-goldenset`, `eval-init`, `carve-harness-create`, `carve-guide`, `theme-factory`, `anti-ai-slop`, `version-changelog` 스킬 | 7 | 시각물·평가·하네스 메타 작업용. 이 프로젝트에 해당 없음 |
| `posttool-slop.sh` | 1 | `.html/.css/.svg` 전용 — 실행 0회 |
| ponytail 커맨드 6개 | 6 | vendor 플러그인과 3중 중복 |
| `docs/rules/code-convention/dev-stack-fastapi.md` | 1 | 웹 프레임워크 무관 |
| `.claude/hooks/tests/` 33개 | 1(디렉토리) | 하네스 자체 회귀 테스트. 하네스를 개발하지 않는 한 불필요하고, 지금 Stop 차단의 원인 |

### 3-4. 없는데 필요한 것 — 재시도 카운터

사용자 요구 "2번 시도 후 알림"에 해당하는 기능이 **하네스에 없다**(§1-2). 새로 만들어야 한다.

확인된 Claude Code 훅 계약 (공식 문서 v2.1.248 + **이 세션의 실측**):

| 종료 코드 | 동작 | 사용자에게 보이는가 |
|---|---|---|
| `exit 0` | 종료 허용 | ❌ 안 보임 (transcript 모드에서만) |
| `exit 2` | **종료 차단**, stderr가 Claude에게 피드백 → 재시도 | ✅ **보인다** — 이 세션에서 `Stop hook feedback:` 로 계속 떠온 그것 |
| `exit 1`/기타 | 비차단 에러 | 훅 에러 공지로 표시 (문서 기반, 미실측) |

- `stop_hook_active`는 **불린이지 카운터가 아니다.** "이미 한 번 차단했는가"만 알려준다.
- stdin에 재시도 횟수 필드는 **없다.** 직접 세야 한다.
- `session_id`는 stdin으로 들어오고 재시도 간 유지된다 → 카운터 키로 쓸 수 있다.
- Stop 훅 여러 개는 **병렬** 실행되고, 하나가 exit 2여도 나머지는 계속 돈다.

→ 사용자 알림은 **`exit 2` + stderr**가 가장 확실하다(실측 근거 있음). 최종 알림도 exit 2로 내되,
"더 고치지 말고 사용자에게 보고하라"는 지시를 담고, 그 다음 패스에서 exit 0으로 종료를 허용한다.

---

## 4. 적용 방안

### 4-1. 경로 선택 — 왜 `prune`이 아니라 `uninstall`인가

| 방법 | 제거 가능 범위 | 문제 |
|---|---|---|
| `install.sh prune --keep-list` | 83개 중 **48개** | **`.claude/hooks/**` 전체가 PROTECTED**(`install.sh:596`)라 훅 20개와 자체 테스트 33개를 못 지운다. 이게 지금 Stop을 계속 막는 바로 그 부분이다. keep-list는 `grep -qxF` 완전일치라 부모 디렉토리를 적으면 자식이 전부 날아가는 함정도 있다 |
| **`uninstall.sh --yes`** (권장) | 매니페스트 83개 전부 + `.gitignore` 블록 + `core.hooksPath` 원복 | 되돌리려면 재설치(curl 한 줄). 기본이 드라이런이라 안전 |

`uninstall.sh`가 안전한 근거 (`uninstall.sh:6-8, 25, 27-37`):
- 삭제 범위가 **매니페스트로 한정**된다. 설치 때 SKIP된 파일은 목록에 없어서 안 건드린다.
- **`AGENTS.md`는 매니페스트에 없다** → 사용자가 직접 쓴 저장소 기여 가이드는 보존된다.
- `logs/`, `specs/HANDOFF.md` 등 런타임 산출물은 사용자 데이터로 보고 남긴다.
- `--yes` 없이 실행하면 삭제 목록만 출력한다.

**사라지는 것 중 값어치 있던 것** (인지하고 선택할 것):
- `pretool-guard.sh` — `rm -rf`·force push·시크릿 커밋 차단. Claude Code 자체 권한 시스템이 일부 대체하지만 동일하지 않다.
- `.githooks/commit-msg` — Conventional Commits 강제.
- `session-handoff.sh` — 세션 인계 자동 저장.

### 4-1-A. 커밋 차단은 반드시 살려낸다 (§2-6 사용자 확정)

`uninstall.sh`는 `.githooks`·`lib-protected.sh`·`harness-manifest.txt`를 전부 지우고
`core.hooksPath`까지 해제하므로, **그대로 두면 커밋 차단이 사라진다.** 되살리는 방법은 둘:

| 방식 | 내용 | 평가 |
|---|---|---|
| 하네스 파일 복원 | `.githooks/pre-commit` + `.claude/hooks/lib-protected.sh`를 백업했다 되살리고, `.claude/harness-manifest.txt`를 빈 파일로 남겨 조건을 만족시킴 | 매니페스트를 "가짜로" 남겨야 하고, `lib-protected.sh` 67줄 중 대부분(SQL·docker·npm 패턴)이 사문 |
| **자립형 pre-commit 새로 작성** (권장) | 필요한 패턴만 담은 ~30줄짜리 `pre-commit` 하나. 외부 의존 없음, 매니페스트 조건 없음 | 읽기 쉽고, 지울 것도 없고, `.claude/` 보호가 **무조건** 켜짐 |

→ **자립형으로 간다** (§4-2-5).

이 셋 중 `pretool-guard.sh`·`session-handoff.sh`를 남기고 싶으면 uninstall 전에 백업해뒀다
되살리면 된다. 기본 계획은 **커밋 게이트만 자립형으로 재작성하고 나머지는 전부 제거**다.

### 4-2. 만들 것 — 훅 파일 1개 + 설정 1개

#### (1) `.claude/hooks/verify.sh` (신규, 약 45줄)

```bash
#!/usr/bin/env bash
# Stop: 테스트 게이트. 실패 시 최대 2회 재시도를 유도하고, 그 후엔 사용자에게 보고시킨다.
set -o pipefail

MAX_RETRY=2
# 에이전트 중립: Claude Code는 CLAUDE_PROJECT_DIR, Codex는 없으므로 git 루트로 폴백 (§4-2-4)
DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
INPUT=$(cat)

# jq가 없으면 세션을 막지 않는다(best-effort). tr -d '\r' 는 Windows jq의 CRLF 출력 대응(§2-4).
if command -v jq >/dev/null 2>&1; then
  SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "nosession"' | tr -d '\r')
else
  SID=nosession
fi
CNT="$DIR/logs/.verify-$SID"

OUT=$(cd "$DIR" && python -B scripts/export.py --selftest 2>&1); RC=$?

if [ "$RC" -eq 0 ]; then
  rm -f "$CNT"
  exit 0
fi

N=$(cat "$CNT" 2>/dev/null || echo 0)
N=$((N + 1))
mkdir -p "$DIR/logs" && printf '%s\n' "$N" > "$CNT"
TAIL=$(printf '%s\n' "$OUT" | tail -15)

if [ "$N" -le "$MAX_RETRY" ]; then
  printf '[verify] 테스트 실패 (%d/%d 시도) — 원인을 고치고 다시 시도하라.\n%s\n' \
    "$N" "$MAX_RETRY" "$TAIL" >&2
  exit 2
fi

if [ "$N" -eq $((MAX_RETRY + 1)) ]; then
  printf '[verify] 테스트가 %d회 시도 후에도 실패했다. 더 고치지 말고, 실패 내용과 시도한 것을 사용자에게 보고하고 멈춰라.\n%s\n' \
    "$MAX_RETRY" "$TAIL" >&2
  exit 2
fi

exit 0   # 이미 보고했다 — 세션 종료 허용
```

동작 순서:

| Stop 패스 | 테스트 | 카운터 | 종료 코드 | 결과 |
|---|---|---|---|---|
| 1 | 실패 | 1 | 2 | Claude 재시도 |
| 2 | 실패 | 2 | 2 | Claude 재시도 |
| 3 | 실패 | 3 | 2 | **"사용자에게 보고하고 멈춰라"** → Claude가 사용자에게 알림 |
| 4 | 실패 | 4 | 0 | 세션 종료 허용 (이미 알렸음) |
| 언제든 | 통과 | 삭제 | 0 | 정상 종료 |

카운터는 테스트가 통과할 때만 리셋되므로 무한루프가 없다. `stop_hook_active`는 쓰지 않는다 — 카운터가
종료를 보장하기 때문이다.

#### (2) `.claude/settings.json` (신규, 최소)

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/verify.sh\"",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

#### (3) `.gitignore` 한 줄

uninstall이 하네스 블록을 지우므로, 카운터 파일용으로 `logs/` 한 줄만 다시 추가한다.

#### (4) Codex 쪽 — 같은 훅을 그대로 재사용한다

Codex CLI에도 훅이 있다. 이벤트 이름·설정 구조·stdin 계약이 Claude Code와 거의 같아서,
**`verify.sh` 한 파일을 두 에이전트가 공유**할 수 있다.

| 항목 | Claude Code | Codex CLI |
|---|---|---|
| 설정 위치 | `.claude/settings.json` | `.codex/hooks.json` 또는 `.codex/config.toml`의 `[hooks]` 테이블 |
| Stop 차단 | `exit 2` + stderr | `exit 2` + stderr, 또는 stdout에 `{"decision":"block","reason":"..."}` |
| 차단의 의미 | stderr가 모델에게 피드백됨 | `reason`이 **새 사용자 프롬프트로 주입**되어 턴이 계속됨 |
| stdin 필드 | `session_id`, `cwd`, `transcript_path`, `stop_hook_active` | 동일 + `turn_id`, `last_assistant_message` |
| 훅 타입 | `command` | `command` + `mcp_tool` |
| Windows | — | `commandWindows`로 별도 명령 지정 가능 |

지원 이벤트도 거의 동일하다: `SessionStart` · `SessionEnd` · `PreToolUse` · `PermissionRequest` ·
`PostToolUse` · `UserPromptSubmit` · `SubagentStart` · `SubagentStop` · `PreCompact` · `PostCompact` ·
`Stop` · `Interrupt`.

**추가할 파일**: `.codex/hooks.json`

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/verify.sh\"",
            "timeout": 120,
            "statusMessage": "테스트 검증 중"
          }
        ]
      }
    ]
  }
}
```

**그래서 `verify.sh`의 `DIR` 결정은 에이전트 중립이어야 한다.** §4-2-1의 해당 줄을 이렇게 쓴다:

```bash
DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
```

Codex에는 `CLAUDE_PROJECT_DIR`이 없으므로 git 루트로 폴백한다.

> **미검증**: Codex 훅 계약은 공식 문서(learn.chatgpt.com/docs/hooks) 기준이고 이 환경에서
> 실제로 돌려보지는 않았다. `exit 2` 경로가 Claude Code와 동일하게 동작하는지는 §5-3에 준하는
> 실세션 확인이 필요하다. `decision: "block"` + `reason` 방식이 Codex의 정식 경로이므로,
> `exit 2`가 기대대로 안 되면 그쪽으로 바꾸면 된다.

`AGENTS.md`에 테스트 명령이 이미 문서화돼 있는 것(`python -B scripts/export.py --selftest`)은
그대로 두면 된다 — 훅이 없을 때의 폴백이자, 모델이 읽는 설명으로 여전히 유효하다.

#### (5) `.githooks/pre-commit` — 자립형으로 재작성 (§2-6, §4-1-A)

외부 의존(`lib-protected.sh`)도, 매니페스트 조건도 없다. `.claude/` 보호가 무조건 켜진다.

```bash
#!/usr/bin/env bash
# 커밋 게이트 — 에이전트 무관(Claude Code·Codex·사람 모두 git 경유면 적용).
# bash+git만 사용, jq 불필요, 오프라인 안전.
# 활성화: git config core.hooksPath .githooks

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
fail=0

# 보호 경로: 에이전트 설정·훅은 명시적 허용 없이 올리지 않는다(사용자 확정).
PROTECTED_RE='(\.env($|[./])|\.claude/|\.codex/|secret)'
# 하드코딩 시크릿 (길이 앵커 — 산문의 'sk-' 한 단어는 걸리지 않는다)
SECRETS_RE='(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.)'

while IFS=$'\t' read -r status p1 p2; do
  [ -n "$p1" ] || continue
  for path in "$p1" "$p2"; do
    [ -n "$path" ] || continue
    if printf '%s' "$path" | grep -Eq "$PROTECTED_RE"; then
      echo "[gate] 보호 경로 커밋 차단 ($status): $path" >&2
      fail=1
    fi
  done
done < <(git diff --cached --name-status)

if git diff --cached -U0 | grep -E '^\+' | grep -Eq "$SECRETS_RE"; then
  echo "[gate] 스테이징된 변경에 하드코딩 시크릿 감지 — 커밋 차단" >&2
  fail=1
fi

[ "$fail" -eq 0 ] || {
  echo "[gate] 차단됨. 올리려면 이 훅을 의도적으로 우회하거나 경로를 빼라." >&2
  exit 1
}
exit 0
```

**명시적으로 허용해 올리고 싶을 때**는 그때만 우회한다:
```bash
git -c core.hooksPath=/dev/null commit -m "chore: 에이전트 설정 반영"
```
(`--no-verify`는 `pretool-guard`가 남아 있으면 세션 내에서 차단되지만, 그 훅도 제거 대상이다.)

`.githooks/commit-msg`(Conventional Commits, 72자 제한)는 **원본 그대로 두면 된다** —
외부 의존이 없고 39줄뿐이다. uninstall 전에 백업했다가 되살린다.

### 4-3. 실행 순서

1. `.githooks/commit-msg`를 임시 백업 (uninstall 대상이지만 그대로 재사용한다)
   — 그 외 지우기 아까운 것(`pretool-guard.sh` 등)이 있으면 같이 백업
2. `bash uninstall.sh` — **드라이런.** 삭제 목록 83개 확인
3. `bash uninstall.sh --yes` — 실제 제거 + `core.hooksPath` 해제 + `.gitignore` 블록 제거
4. `.claude/hooks/verify.sh` 작성 (§4-2-1)
5. `.claude/settings.json` 작성 (§4-2-2)
6. `.codex/hooks.json` 작성 (§4-2-4) — Codex도 같은 게이트를 쓰게
7. **`.githooks/pre-commit` 자립형으로 새로 작성 (§4-2-5)** + `commit-msg` 복원
8. **`git config core.hooksPath .githooks` 재설정** — uninstall이 해제하므로 반드시 다시
9. `.gitignore`에 `logs/` 추가
10. §5로 검증 (§5-5 커밋 차단 확인 포함)

**주의**: 3번 전까지는 `pretool-guard.sh`가 살아 있어서 `.claude/hooks/`·`settings.json`에 대한
Bash 쓰기를 차단한다(`rm`, `>` 등). 4·5번은 3번 **이후에** 해야 한다.

---

## 5. 검증 방법

### 5-1. 정상 경로 — 테스트 통과 시 조용히 지나가는가

```bash
printf '{"session_id":"t1","hook_event_name":"Stop"}' | bash .claude/hooks/verify.sh; echo "rc=$?"
```
기대: `rc=0`, 출력 없음, `logs/.verify-t1` 없음.

### 5-2. 실패 경로 — 2회 재시도 후 알림까지

테스트를 일시적으로 깨뜨리고(예: `scripts/promptlog/` 의 함수 하나를 잠깐 고장) 4번 연속 실행:

```bash
for i in 1 2 3 4; do
  printf '{"session_id":"t2","hook_event_name":"Stop"}' | bash .claude/hooks/verify.sh
  echo "--- pass $i: rc=$? counter=$(cat logs/.verify-t2 2>/dev/null)"
done
```

기대:

| pass | rc | counter | stderr |
|---|---|---|---|
| 1 | 2 | 1 | `테스트 실패 (1/2 시도)` |
| 2 | 2 | 2 | `테스트 실패 (2/2 시도)` |
| 3 | 2 | 3 | `사용자에게 보고하고 멈춰라` |
| 4 | 0 | 4 | 없음 |

그 뒤 고장을 되돌리고 5-1을 다시 돌려 `rc=0` + 카운터 파일 삭제를 확인한다.

### 5-3. 실제 세션에서

1. 아무 파일이나 사소하게 고치고 턴을 끝내 → Stop 훅이 조용히 통과하는지 확인
2. 테스트를 고의로 깨고 턴을 끝내 → `Stop hook feedback:` 으로 실패 메시지가 뜨고 Claude가 재시도하는지 확인
3. 재시도로도 못 고치는 상태를 만들어 → 3번째에 "사용자에게 보고" 메시지가 뜨고 Claude가 실제로 보고하며 멈추는지 확인

### 5-4. 제거가 깨끗한지

```bash
git status --porcelain | wc -l     # 하네스 산물 17건이 빠졌는지
ls .claude                          # hooks/ settings.json 만 남았는지
git config core.hooksPath           # .githooks 여야 함 (§4-3 8번에서 재설정)
cat AGENTS.md | head -3             # 프로젝트 기여 가이드가 보존됐는지
python -B scripts/export.py --selftest   # 33 tests OK
```

### 5-5. 커밋 차단이 살아 있는가 (§2-6 — 제일 중요한 회귀 지점)

uninstall이 이 보호를 꺼뜨리는 게 이 계획의 최대 리스크다. 반드시 확인한다.

```bash
# (1) 보호 경로 차단 — 실패해야 정상
git add .claude/settings.json && git commit -m "test: should block"
#   기대: [gate] 보호 경로 커밋 차단 (A): .claude/settings.json  / exit 1
git reset

# (2) 일반 경로는 통과해야 정상
git add README.md && git commit -m "docs: 통과 확인" --dry-run
#   기대: 차단 메시지 없음

# (3) 시크릿 차단 — 실패해야 정상
#     문자열을 런타임에 조립한다(이 문서 자체가 패턴에 걸리지 않도록)
printf 'KEY = "sk-%s"\n' "$(printf 'a%.0s' $(seq 25))" > _secret_probe.py
git add _secret_probe.py && git commit -m "test: should block"
#   기대: [gate] 하드코딩 시크릿 감지 / exit 1
git reset && rm -f _secret_probe.py
```

> 참고: 이 계획서를 쓰는 중에 실제로 (3)의 리터럴 때문에 `pretool-guard.sh`가 Edit을
> 차단했다. 가드가 의도대로 동작한다는 실측 증거다.

---

## 6. 다음 단계 (설계만 — 이번에 구현하지 않음)

> **사용자 지시 (2026-09-17)**: "적용 방안을 추가한 후 LLM 리뷰를 추가할 거야.
> 적용할 때 LLM 리뷰는 먼저 Worker랑 다른 에이전트나 다른 LLM를 호출 후,
> Reviewer는 리뷰만 하고 Worker는 지적받은 거 수정하는 방식으로 할 거임.
> 알아만 두고 구현은 적용 방안을 하고 나서 합시다."

§4가 끝난 뒤에 얹을 레이어다. 여기서는 확정된 요구사항과 열린 질문만 기록한다.

### 6-1. 확정된 요구사항

1. **Reviewer는 Worker와 다른 주체여야 한다** — 다른 에이전트, 또는 아예 다른 LLM.
   같은 세션·같은 컨텍스트가 자기 작업을 채점하면 독립성이 없다.
2. **역할 분리가 엄격하다.**
   - Reviewer: **리뷰만.** 코드를 고치지 않는다 → 도구를 읽기 전용(Read/Grep/Bash)으로 제한해
     구조적으로 보장한다.
   - Worker: 지적받은 것만 수정한다.
3. **순서**: Worker 작업 → Reviewer 호출 → 지적 → Worker 수정.

### 6-2. §4와의 접점

테스트 게이트와 층이 다르다. 섞지 않는다.

| 층 | 판정 | 주체 | 실패 시 |
|---|---|---|---|
| 1. 테스트 게이트 (§4) | 결정론 (`--selftest` 종료 코드) | 스크립트 | 2회 재시도 → 사용자 알림 |
| 2. LLM 리뷰 (§6) | 주관 | Reviewer 에이전트 | 지적 → Worker 수정 |

**테스트가 먼저다.** 깨진 코드를 LLM에게 리뷰시키는 건 낭비다.
`verify.sh`가 green인 뒤에 리뷰를 건다.

### 6-3. 결정해야 할 것 (구현 전 사용자 확인 필요)

| 질문 | 선택지 |
|---|---|
| 발동 방식 | (a) 슬래시 커맨드로 명시 호출 (b) Stop 훅 체인에 자동 (c) 커밋 직전 |
| Reviewer 주체 | (a) Claude Code 서브에이전트(다른 모델 지정) (b) Codex CLI 헤드리스 호출 (c) 둘 다 — 교차 리뷰 |
| 반복 상한 | §4와 같은 2회? 아니면 1회 리뷰 후 종료? |
| 리뷰 범위 | 변경분(diff)만 vs 관련 파일 전체 |
| 차단 여부 | 지적사항이 있으면 커밋/종료를 막을지, 보고만 할지 |

### 6-4. 주의점

- **§4에서 `.claude/agents/`를 전부 지운다.** 리뷰어 에이전트는 그때 최소 정의로 새로 쓴다.
  기존 `security-reviewer`·`evaluator`는 Spring/골든셋 전용이라 재활용 가치가 없다(§2-5).
- 다른 LLM을 부르려면 그쪽 CLI와 인증이 필요하다. Codex CLI는 PATH에 있다
  (하네스의 `eval-run.sh`가 `claude -p` 헤드리스를 쓰던 것과 같은 패턴).
- **Reviewer에게 Edit/Write를 주지 않는 것이 이 설계의 핵심이다.** 프롬프트로 부탁하지 말고
  도구 목록으로 강제한다.
