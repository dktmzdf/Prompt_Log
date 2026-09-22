# Proposal

## Why

원본 세션 JSONL은 약 30일 후 삭제된다(Claude Code `cleanupPeriodDays` 기본값 30).
그런데 **이 프로젝트에는 원본 사본이 존재한 적이 없다.**

`.jsonl` 리포트를 출력하기 때문에 원본이 보관되는 것처럼 보이지만, 그것은
`present()`로 절단된 중립 이벤트 스트림이다(`service.py:41`). 원본의 `uuid`,
`parentUuid`, 도구 짝짓기 구조는 버려지고 4KB 초과분은 잘린다.

2026-09-22 실측:

| 항목 | 값 |
|---|---|
| 원본 세션 | 287개 · 128.3MB |
| 리포트가 있는 세션 | 284개 중 **74개 (26%)** |
| 리포트 절단 | 498곳에서 **4.9MB** 유실 |

즉 74%의 세션은 30일 뒤 흔적 없이 사라지고, 남는 26%도 절단본이다. RAG 명세
([rag-spec.md](../../../docs/rag-spec.md)) 1단계가 이 문제를 다루지만, RAG 이전에
**정본을 확보하는 것 자체가 독립적으로 시급하다.**

측정 결과 절단은 목적을 달성하지 못하고 있었다. 절단이 아끼는 시간은 1.8%(18ms)에
불과하고, 절단으로 아끼는 6.5MB보다 원본을 zlib로 통째 저장하는 편(18.7MB)이
무절단 리포트(20.1MB)보다 싸다. 전체 코퍼스 128.3MB가 압축하면 30.8MB다.

## What Changes

- **SQLite 저장소 신설** (`scripts/promptlog/store.py`). 원본 JSONL 줄을 한 글자도
  변경하지 않고 적재한다.
- **증분 적재.** `session.ingested_lines` 워터마크로 새 줄만 읽는다. 세션이 길어져도
  매 턴 비용이 일정하다.
- **`Event`에 `source_id` 추가** 및 어댑터 두 개가 원본 식별자를 채우도록 수정.
  Claude는 행 `uuid`, Codex native는 item `id`, Codex legacy는 `_line_no`를 쓴다.
- **resume 중복 병합.** 식별자에 `session_id`를 섞지 않아 재개된 세션의 중복 이벤트가
  한 행으로 모인다. 귀속은 `session_event` 매핑으로 표현한다.
- **`--reindex`.** 적재된 원본에서 색인을 재생성한다. 색인 스키마를 잘못 잡아도
  원본이 남아 있으면 복구된다는 것이 이 설계의 전제이므로 필수다.
- **리포트 생성은 그대로 유지한다.** 훅에 적재 갈래를 추가할 뿐 기존 출력 경로·
  파일명·절단 동작을 변경하지 않는다. RAG 구축 이후에도 계속 생성한다. 적재 실패가
  리포트 생성을 막아서는 안 된다(AGENTS.md 6항).

BREAKING 변경은 없다. 기존 CLI·훅 계약과 리포트 출력은 전부 그대로다.

### Non-goals

다음은 이 변경의 범위가 아니다.

- 청킹·임베딩·검색·`ask.py` (rag-spec 2~4단계)
- `raw_blob` 압축 및 접기 시점. 비압축 128MB를 그대로 두어도 무방하므로 뒤로 미룬다
- 검색 조각 마스킹 (rag-spec §3.6 미결)
- 리포트 렌더 방식·절단 한도 변경

## Capabilities

### New Capabilities

- `log-store`: 에이전트 세션 원본 JSONL을 바이트 단위로 동일하게 보존하는 저장소.
  증분 적재, 재적재 멱등성, 원본 대조 가능성을 책임진다.
- `log-index`: 적재된 원본에서 파생되는 검색용 색인. 이벤트 식별자 부여, resume
  중복 병합, 세션 귀속과 턴 번호, 원본으로부터의 재생성을 책임진다.

### Modified Capabilities

없음. 이 저장소에는 아직 등록된 스펙이 없다.

## Impact

**신규**

- `scripts/promptlog/store.py` — 스키마, 적재, 재색인

**수정**

- `scripts/promptlog/models.py` — `Event.source_id` 추가 (기존 필드 불변)
- `scripts/promptlog/adapters/claude.py` — `source_id` 채움
- `scripts/promptlog/adapters/codex.py` — `source_id` 채움, `id` 제외 목록 조정
- `scripts/promptlog/service.py` — `export()`에 적재 갈래 추가
- `scripts/promptlog/cli.py` — DB 경로 및 `--reindex` 옵션
- `tests/` — 회귀 테스트 추가

**의존성**

추가 없음. `sqlite3`는 표준 라이브러리다. rag-spec §5의 외부 라이브러리 허용 정책은
2단계(임베딩)에서 처음 필요해지므로 `pyproject.toml`도 아직 만들지 않는다.

**호환성**

기존 리포트 경로·내용·`--selftest` 33건이 모두 그대로 유지되어야 한다. 저장소는
기본적으로 리포트 루트와 같은 곳에 두어 `--output`으로 함께 이동한다.
