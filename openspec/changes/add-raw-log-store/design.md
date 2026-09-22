# Design

## Context

동기와 실측 수치는 [proposal.md](proposal.md)와 상세 설계 문서
[docs/rag-store-design.md](../../../docs/rag-store-design.md)에 있다. 여기서는 그
위에서 결정해야 하는 기술 선택만 다룬다.

설계를 제약하는 현재 상태는 넷이다.

1. 파이프라인이 `parse → assemble → present → write_reports`로 고정되어 있고
   `service.export()`가 CLI와 Stop 훅 양쪽의 유일한 진입점이다(`service.py:34-41`).
   적재 갈래를 여기 한 곳에만 붙이면 두 경로가 모두 덮인다.
2. `present()`는 이미 "report-only limits"로 설계되어 `assemble()` 결과를 변경하지
   않는다. 절단은 표시 계층에 갇혀 있으므로 적재 갈래는 절단 이전 데이터를 쓴다.
3. Stop 훅은 여러 개가 병렬로 실행될 수 있고, 여러 세션이 동시에 진행될 수 있다.
4. 원본 로그는 append-only다. 재개 시 새 파일이 생기고 이전 내용이 복사되며, 이때
   `uuid`는 보존되고 `sessionId`만 덮인다.

## Goals / Non-Goals

**Goals:**

- 원본을 정본으로 만들고 나머지를 전부 재생성 가능한 파생물로 강등한다. 스키마를
  잘못 잡아도 복구되게 한다.
- 매 턴 적재 비용을 세션 길이와 무관하게 유지한다.
- 리포트 경로를 건드리지 않는다. 롤백이 "적재 호출 제거 + DB 파일 삭제"로 끝나게 한다.

**Non-Goals:**

- 성능 최적화. 실측상 세션 1회 export가 최악 150ms이고 절단 유무 차이가 18ms이므로
  최적화 여지를 찾기 전이다.
- 조상 세션 필터(`adapters/claude.py:16-24`) 제거. 실측상 107개 파일 전부에서 0건
  발화하는 죽은 코드지만, 제거는 별도 변경으로 다룬다.
- 스키마를 rag-spec 2~4단계까지 미리 만드는 것. 청킹·벡터·FTS5 테이블은 해당 단계에서
  추가한다. 대신 `PRAGMA user_version`으로 확장 경로만 열어둔다.

## Decisions

### 1. 원본을 저장하고 색인을 파생물로 둔다

rag-spec §2는 `log.events`(파싱 결과)를 저장 대상으로 삼았다. 대신 **원본 JSONL 줄**을
저장하고 이벤트를 색인으로 둔다.

- 대안 A — 파싱 이벤트만 저장: 저장 시점에 무엇을 남길지가 **비가역 결정**이 된다.
  `activity`·`usage`를 빼기로 했다가 필요해지면 30일 뒤에는 복구 경로가 없다.
- 대안 B — 원본만 저장하고 색인 없음: 검색이 불가능하다.
- **선택 — 둘 다.** 원본 30.8MB(압축 기준)를 내는 대가로 모든 스키마 결정이 가역이
  된다. 측정상 원본 통째 저장(18.7MB/20개 세션)이 무절단 리포트(20.1MB)보다 싸다.

### 2. `source_id`는 네임스페이스가 박힌 문자열, 해시하지 않는다

```text
  claude:uuid:{uuid}#{kind}                 전역 유일
  codex:item:{item_id}#{kind}               전역 유일
  codex:line:{session_key}:{line_no}#{kind} 파일 스코프 (Codex legacy 5개 세션)
```

- `#{kind}`가 필요한 이유: 한 원본 행이 이벤트를 여러 개 만든다. assistant 행 하나가
  `model`·`usage`·본문·`activity`를 내고, user 행 하나가 `prompt`와 `denial`을 낸다.
  실측상 assistant 행의 content 블록은 항상 정확히 1개이므로, 같은 행에서 같은 kind가
  둘 이상 나오는 경우는 `usage` iterations뿐이다. 그때만 순번을 덧붙인다.
- 해시(rag-spec §3.1의 `sha256(...)[:16]`) 대신 원문을 키로 쓰는 이유: 고정폭 외에
  얻는 것이 없고, 원문이면 접두사 조회가 그대로 되고 충돌 가능성이 0이다.
- 네임스페이스를 문자열 안에 넣으면 파일 스코프 폴백만 스코프를 갖고 나머지는 전역
  유일이라는 사실이 키 자체에 드러난다.

### 3. 식별자에 `session_id`를 섞지 않는다

rag-spec §3.1은 `sha256(session_id + source_id)`를 제안했다. 실측 결과 이것은 중복을
만든다. 재개 시 `uuid`는 보존되고 `sessionId`만 덮이므로, `session_id`를 섞으면 같은
사건이 두 벌이 된다. Codex 기준 총 출현 9,304건 중 **잉여 사본이 4,256건(45.7%)**이다.

귀속은 `session_event(session_key, source_id, seq, turn_no)` 매핑으로 표현한다.
"한 이벤트가 두 세션에 속한다"가 거짓이 아니라 사실 그대로 기록된다.

`turn_no`를 `event`가 아니라 매핑에 두는 이유도 같다. 턴 번호는 세션 상대적이므로
공유 이벤트가 A세션 5번 턴, B세션 3번 턴일 수 있다.

### 4. 폴백 식별자는 파싱 순서가 아니라 `_line_no`

rag-spec §3.1은 "파싱 순서 인덱스"를 폴백으로 제안하며 정렬 불안정을 한계로 적었다.
`readers.read_jsonl()`이 이미 `_line_no`를 부착하고 있고(`readers.py:23`) Codex
어댑터는 그것을 정렬 키로 쓴다(`codex.py:11`). 파일의 물리적 줄 번호이므로 정렬·필터와
무관하고 append-only 파일에서는 재적재해도 변하지 않는다.

### 5. 턴 경계 규칙은 복제하고 동치를 테스트로 강제한다

`assemble()`의 루프(`assemble.py:136-149`)는 경계 판정·날짜 버킷 생성·`current`/
`pending` 상태를 한 덩어리로 돌려서 순수 함수로 추출되지 않는다.

- 대안 — `assemble()`을 리팩터링해 경계 판정을 분리: 기존 파이프라인을 건드리게 되고,
  이 변경의 "리포트 무변경" 목표와 충돌한다.
- **선택 — 복제.** 대신 같은 입력에 대해 두 구현의 턴 경계가 일치하는지 검증하는 회귀
  테스트를 둔다. 규칙이 어긋나면 테스트가 깨진다.

### 6. `event`는 좁은 컬럼 + `payload` JSON

필터·정렬에 쓰는 것만 컬럼으로 두고 나머지는 JSON 문자열로 접는다. `Event`
데이터클래스의 필드 17개를 전부 펴면 대부분 NULL이고, 원본이 따로 있으므로 색인은
검색에 필요한 만큼만 가지면 된다.

### 7. 스키마 (1단계 범위)

```sql
PRAGMA user_version = 1;

CREATE TABLE session (
  session_key  TEXT PRIMARY KEY,
  agent        TEXT NOT NULL,
  source_path  TEXT NOT NULL,
  cwd TEXT, branch TEXT, client TEXT, version TEXT, history_mode TEXT,
  ts_first TEXT, ts_last TEXT,
  ingested_lines INTEGER NOT NULL DEFAULT 0,
  ingested_at  TEXT
);

CREATE TABLE raw_line (
  session_key TEXT NOT NULL REFERENCES session,
  line_no     INTEGER NOT NULL,
  body        TEXT NOT NULL,
  PRIMARY KEY (session_key, line_no)
) WITHOUT ROWID;

CREATE TABLE event (
  source_id  TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  ts         TEXT,
  text       TEXT,
  tool_name  TEXT,
  status     TEXT,
  payload    TEXT
);

CREATE TABLE session_event (
  session_key TEXT NOT NULL REFERENCES session,
  source_id   TEXT NOT NULL REFERENCES event,
  seq         INTEGER NOT NULL,
  turn_no     INTEGER,
  PRIMARY KEY (session_key, source_id)
);
CREATE INDEX ix_se_order ON session_event(session_key, seq);

CREATE TABLE turn (
  session_key TEXT NOT NULL REFERENCES session,
  turn_no     INTEGER NOT NULL,
  ts_start TEXT, ts_end TEXT, prompt_text TEXT,
  PRIMARY KEY (session_key, turn_no)
);
```

`raw_blob`(zlib 압축본)은 스키마에 넣지 않는다. 비압축 128MB는 감당 가능하고, 접는
시점을 정하지 못했으며, 압축은 나중에 `user_version` 2로 추가하면 된다.

### 8. 동시성

Stop 훅이 병렬로 돌고 여러 세션이 동시에 진행될 수 있으므로 `journal_mode=WAL`과
`busy_timeout`을 설정한다. 적재는 세션 단위 트랜잭션 하나로 묶어 중간 상태가 남지
않게 한다.

### 9. 의존성 없음

`sqlite3`는 표준 라이브러리다. rag-spec §5가 허용한 외부 라이브러리는 2단계(임베딩)에서
처음 필요하므로 `pyproject.toml`도 그때 만든다.

## Risks / Trade-offs

- **턴 경계 규칙 복제로 두 구현이 어긋난다** → 동치 회귀 테스트로 강제한다. 어긋나면
  테스트가 실패한다.
- **재개 병합이 내용이 다른 이벤트를 덮어쓴다** → 실측상 공유 `uuid` 632건 중 2건은
  `message` 본문이 달랐다(0.3%). 마지막 적재가 이긴다. 원본 두 벌이 `raw_line`에
  그대로 남아 있으므로 필요하면 대조할 수 있다.
- **적재가 Stop 훅 지연을 늘린다** → 증분이므로 매 턴 새 줄만 INSERT한다. 파싱은
  리포트 생성이 어차피 하고 있어 추가 비용이 아니다.
- **DB 손상 시 색인과 원본을 함께 잃는다** → 원본 로그는 30일간 출처에 남아 있으므로
  그 안에는 재적재할 수 있다. 그 이후를 대비한 백업 정책은 이 변경의 범위 밖이다.
- **`WITHOUT ROWID`에 큰 `body`를 넣으면 오버플로 페이지가 늘어난다** → 이 규모에서는
  문제가 되지 않는다. 문제가 되면 일반 테이블로 되돌린다.
- **중복 병합이 "두 세션에 각각 존재했다"는 사실을 흐린다** → `session_event`가 두
  행을 유지하므로 사실은 보존된다.

## Migration Plan

1. 스키마는 `PRAGMA user_version`으로 버전을 관리한다. 1단계가 버전 1이다.
2. 기존 리포트 경로·내용이 변하지 않으므로 별도 데이터 이관이 없다.
3. 롤백은 `export()`에서 적재 호출을 제거하고 DB 파일을 지우는 것으로 끝난다. 리포트
   파이프라인은 영향을 받지 않는다.
4. 도입 후 `--all` 백필로 기존 284개 세션을 적재한다. 원본 로그가 남아 있는 동안에만
   가능한 작업이므로 도입 직후 실행한다.

## Open Questions

- `raw_line`을 압축 블롭으로 접는 시점과 조건. 접지 않아도 동작하므로 뒤로 미룰 수
  있다.
- Codex의 rollout 파일 보존 정책. `~/.codex/config.toml`에 관련 항목이 없어 근거를
  확인하지 못했다. 미저장분이 121개로 Claude보다 많아 백필 우선순위에 영향을 주지만,
  스키마나 접근 방식은 바꾸지 않는다.
