# RAG 저장소 설계 (rag-spec 1단계 상세)

> [RAG 명세](rag-spec.md) 4장 1단계의 구현 설계. 명세가 제안한 내용 중 실측으로
> 반증된 부분(§3)이 있으므로 명세보다 이 문서가 우선한다.
>
> 측정 기준일 2026-09-22. 모든 수치는 실제 로그에서 잰 값이다.

## 1. 요약

원본 JSONL은 30일 후 삭제된다. 이 저장소는 그 원본을 대신하는 정본이 되어야 한다.

핵심 결정은 하나다. **원본 JSONL을 무손실로 저장하고, 파싱된 이벤트·청크·벡터는
전부 거기서 재생성 가능한 파생물로 둔다.**

```text
        원본 JSONL (30일 후 소멸)
              |
              v
   +---------------------------------------------+
   |  session ---< raw_line / raw_blob   <- 정본  |
   |     |                                       |
   |     +---< session_event >--- event          |
   |     |          |                            |
   |     +---< turn                              |
   |                |                            |
   |             chunk --- chunk_fts (FTS5)      |
   |                |                            |
   |                +--- vector                  |
   +---------------------------------------------+
              |
              v
      리포트 .md (뷰, 절단은 여기서만)
```

`raw_*` 위쪽만 정본이다. 나머지는 언제든 `DROP` 후 재생성할 수 있고, 그것이 이
설계의 안전장치다. 스키마를 잘못 잡아도 원본이 남아 있는 한 복구된다.

## 2. 측정 결과

### 2.1 코퍼스 규모

| | 파일 | 크기 | 기간 |
|---|---|---|---|
| Claude | 107 | 46.0MB | 2026-08-25 ~ 2026-09-22 |
| Codex | 180 | 82.1MB | 2026-08-26 ~ 2026-09-22 |
| 합계 | 287 | 128.3MB | |

zlib 레벨 6으로 압축하면 **30.8MB (24.0%, 4.2배)**. JSONL은 키가 반복되어 압축이
매우 잘 먹는다.

### 2.2 아카이브 실태

| | 원본 세션 | 리포트 있음 | 리포트 없음 |
|---|---|---|---|
| Claude | 107 | 18 | 89 (83%) |
| Codex | 177 | 56 | 121 (68%) |
| 합계 | 284 | 74 | **210 (74%)** |

284개 세션 중 210개가 어디에도 저장되어 있지 않다. 원본 JSONL이 유일한 사본이고
그것은 30일 롤링 버퍼다.

저장된 74개도 온전하지 않다. 현재 리포트는 10.3MB이며, 그 과정에서 **498곳에서
4.9MB가 절단되어 버려졌다**(`.jsonl` 리포트의 이벤트 493개에 `truncated` 표시).

### 2.3 식별자

**Claude**

| 항목 | 실측 |
|---|---|
| `uuid` 보유 | 이벤트를 만드는 행(`user`/`assistant`/`system`/`attachment`)은 100% |
| `uuid` 없는 행 | 메타 행(`ai-title`, `mode`, `permission-mode`, `cost-state` 등)뿐. 파일당 51~60개 |
| assistant 행당 content 블록 | **항상 정확히 1개** (상위 5개 파일에서 3342/3342) |
| `tool_use.id` | 파일 내 유일 |

**Codex**

| 항목 | 실측 |
|---|---|
| native 세션 | 180개 중 175개. `item.id` 100% 존재, 파일 내 유일, 종류 접두사 있음(`msg_`/`rs_`/`exec-`) |
| legacy 세션 | 5개. **식별자 없음** |
| `ordinal` · `timestamp` | 모든 행에 100% |

### 2.4 resume 중복

세션을 resume하면 이전 내용이 새 파일로 복사되는데, **`uuid`는 보존되고
`sessionId`만 새 값으로 덮인다.**

```text
26b94bea.jsonl   [uuid=4432d718, sessionId=26b94bea]  ts=2026-08-27T03:07:58.671Z
0ec719f4.jsonl   [uuid=4432d718, sessionId=0ec719f4]  ts=2026-08-27T03:07:58.671Z
                        ^^^^^^^^ 보존          ^^^^^^^^ 덮어쓰기
```

이 쌍에서 공유된 `uuid` 632개 중 **630개는 `message` 본문이 완전히 동일**했다.
다른 것은 `sessionId`·`slug`·`promptId` 등 세션 귀속 필드뿐이다.

중복 규모:

| | 중복 |
|---|---|
| Claude | 123 / 2382 tool_use id (5%) |
| **Codex** | 총 출현 9,304 중 **잉여 사본 4,256 (45.7%)**. 30개 체인, 180개 중 100개 파일 |

### 2.5 절단의 실제 효과

가장 큰 세션 20개(원본 76.4MB)로 절단 유무를 비교했다.

| | 시간 | 출력 |
|---|---|---|
| 절단 ON | 1,021ms | 13.6MB |
| 절단 OFF | 1,038ms | 20.1MB |
| 차이 | **+18ms** | +6.5MB |
| (참고) 원본 20개를 zlib로 통째 저장 | | **18.7MB** |

세션 1회 export 비용:

| 원본 | 줄 | 시간 |
|---|---|---|
| 9.0MB | 3,887 | 150ms |
| 6.8MB | 3,379 | 121ms |
| 4.9MB | 2,080 | 76ms |
| 평균 세션(약 450KB) | | 10ms 안팎 |

**결론 두 가지.**

1. 절단은 속도에 기여하지 않는다(1.8%). 비용은 렌더링이 아니라 파싱(`read_jsonl` +
   `parse` + `deepcopy`)이고 그것은 절단 여부와 무관하다. 매 턴 최악 150ms는
   Stop 훅에서 체감되지 않는다.
2. 절단이 아끼는 6.5MB보다, **원본을 통째로 압축해 넣는 편(18.7MB)이 무절단
   리포트(20.1MB)보다 싸다.**

따라서 절단은 저장 정책이 아니라 **표시 옵션**으로 남긴다. 현재 코드가 이미 그렇게
되어 있다 — `present()`의 독스트링이 "report-only limits"이고 `assemble()` 결과를
변경하지 않는다. 문제는 그 렌더 결과가 매 턴 디스크에 굳어 정본 행세를 한 것이다.

## 3. 명세에서 수정하는 부분

| rag-spec 위치 | 명세 내용 | 수정 | 근거 |
|---|---|---|---|
| §3.1 | `event_id = sha256(session_id + source_id)` | **`session_id`를 키에서 제외** | §2.4 — 섞으면 resume 중복이 그대로 저장된다(Codex 45.7%) |
| §3.1 | 폴백은 "파싱 순서 인덱스" | **`_line_no` 사용** | `readers.read_jsonl()`이 이미 부착 중(`readers.py:23`). 정렬·필터와 무관하게 안정적 |
| §3.1 | "Claude 어댑터는 timestamp만으로 정렬하므로 순서 인덱스가 불안정" | 진단이 어긋남 | Python `sorted()`는 stable이라 동순위는 파일 순서를 유지한다. 실제 불안정 요인은 정렬이 아니라 resume 복사다 |
| §3.2 | 턴 경계 규칙을 "재사용" | 현재 구조에서는 **복제 + 동치 회귀 테스트** | `assemble()` 루프(`assemble.py:136-149`)가 경계 판정·날짜 버킷·상태를 한 덩어리로 돌려 순수 함수로 추출되지 않는다 |
| §2 | `log.events`를 무절단 저장 | **원본 JSONL을 무절단 저장.** `event`는 색인 | 원본이 있으면 `activity`·`usage`·`model`을 언제든 재파싱할 수 있다. 저장 여부가 비가역 결정이 아니게 된다 |

부수 발견: `adapters/claude.py:16-24`의 조상 세션 필터
(`r.get("sessionId", session_id) == session_id`)는 **107개 파일 전부에서 한 행도
걸러내지 않았다.** resume 시 `sessionId`가 덮어쓰기되므로 이 조건은 실제 데이터에서
성립하지 않는다. 합성 로그로 만든 회귀 테스트(`조상세션제외`)만 통과한다.

## 4. 식별자 설계

`source_id`를 네임스페이스가 박힌 문자열로 만든다. 해시하지 않고 원문을 키로 쓴다.

```text
  namespace  :        raw id             #  kind
  ---------- : ---------------------     - --------
  claude:uuid:4432d718-...               #tool       전역 유일
  codex:item :exec-f1967466-...          #tool       전역 유일
  codex:line :01a0c168:0472              #prompt     legacy 5개 전용, 파일 스코프
```

**`#kind` 판별자가 필요한 이유.** 한 행이 이벤트를 여러 개 만든다.

```text
assistant 행 (uuid=U) ->  model 이벤트
                      ->  usage 이벤트 (iterations면 N개)
                      ->  answer / thinking / tool  (블록이 1개이므로 정확히 하나)
                      ->  activity 이벤트

user 행 (uuid=V)      ->  prompt 이벤트    (claude.py:51)
                      ->  denial 이벤트    (claude.py:53)  <- 같은 uuid, 다른 kind
```

`uuid` 단독으로는 충돌한다. `uuid#kind`면 충돌하지 않는다 — 한 행에서 같은 kind가
두 번 나오는 경우는 `usage` iterations뿐이고, 그때는 순번을 덧붙인다.

**해시 대신 원문을 키로 쓰는 이유.** `source_id`는 이미 유일한 문자열이다. 고정폭
말고 얻는 것이 없고, 원문이면 `WHERE source_id LIKE 'codex:item:exec-%'` 같은
조회가 그대로 되고 충돌 가능성이 0이다.

## 5. 스키마

```sql
-- 정본 ---------------------------------------------------------
CREATE TABLE session (
  session_key  TEXT PRIMARY KEY,        -- session_id 원문
  agent        TEXT NOT NULL,           -- claude | codex
  source_path  TEXT NOT NULL,
  cwd TEXT, branch TEXT, client TEXT, version TEXT, history_mode TEXT,
  ts_first TEXT, ts_last TEXT,
  ingested_lines INTEGER NOT NULL DEFAULT 0,   -- 증분 적재 워터마크
  ingested_at  TEXT
);

CREATE TABLE raw_line (                  -- 활성 세션
  session_key TEXT NOT NULL REFERENCES session,
  line_no     INTEGER NOT NULL,
  body        TEXT NOT NULL,             -- 원본 줄 그대로. 한 글자도 변경하지 않는다
  PRIMARY KEY (session_key, line_no)
) WITHOUT ROWID;

CREATE TABLE raw_blob (                  -- 끝난 세션 (zlib 4.2배)
  session_key TEXT PRIMARY KEY REFERENCES session,
  method TEXT NOT NULL, line_count INTEGER NOT NULL, body BLOB NOT NULL
);

-- 색인 (재생성 가능) --------------------------------------------
CREATE TABLE event (
  source_id  TEXT PRIMARY KEY,           -- §4의 문자열 키
  kind       TEXT NOT NULL,
  ts         TEXT,
  text       TEXT,                       -- 본문. tool은 output
  tool_name  TEXT,
  status     TEXT,
  payload    TEXT                        -- 나머지 전부 JSON
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

-- 검색 (재생성 가능) --------------------------------------------
CREATE TABLE chunk (
  chunk_id     INTEGER PRIMARY KEY,
  session_key  TEXT NOT NULL,
  turn_no      INTEGER NOT NULL,
  part         INTEGER NOT NULL DEFAULT 0,   -- 길이 초과 분할
  ts           TEXT,
  text         TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE(session_key, turn_no, part)
);
CREATE INDEX ix_chunk_hash ON chunk(content_hash);

CREATE TABLE vector (
  content_hash TEXT NOT NULL,
  model        TEXT NOT NULL,
  dim          INTEGER NOT NULL,
  vec          BLOB NOT NULL,
  PRIMARY KEY (content_hash, model)
);

CREATE VIRTUAL TABLE chunk_fts USING fts5(
  text, content=chunk, content_rowid=chunk_id, tokenize='trigram'
);
```

### 5.1 당연해 보이지 않는 선택

**`turn_no`가 `event`가 아니라 `session_event`에 있다.** 턴 번호는 세션 상대적이다.
resume로 공유된 이벤트가 A세션에서는 5번 턴, B세션에서는 3번 턴일 수 있다.
`event`에 박으면 거짓이 된다.

**`vector`가 `chunk_id`가 아니라 `content_hash`로 묶인다.**

```text
  chunk A (세션1, 턴5) --+
                         +--> content_hash = abc123 --> vector 1개
  chunk B (세션2, 턴3) --+
```

§2.4의 Codex 중복 45.7%가 임베딩 단계에서 자동으로 회수된다. 중복 청크가 들어와도
벡터는 한 번만 계산된다. PK에 `model`이 있어 명세 §3.5의 "임베딩 모델 후보 두 개
비교"를 같은 DB에서 나란히 할 수 있다.

**`event`가 넓은 컬럼 대신 `payload` JSON을 쓴다.** 필터·정렬에 쓰는 것만
컬럼(`kind`, `ts`, `tool_name`, `status`)으로 두고 나머지는 JSON. `Event`
데이터클래스의 필드 17개를 전부 펴면 대부분 NULL이다. 원본이 따로 있으므로 `event`는
검색에 필요한 만큼만 가지면 된다.

**FTS5 토크나이저가 `unicode61`이 아니라 `trigram`이다.** FTS5 기본 토크나이저는
공백·문장부호로 자르는데, 한국어는 조사가 붙어 "로그를"과 "로그가"가 다른 토큰이
된다. 검색이 거의 걸리지 않는다. `trigram`은 3글자씩 겹쳐 자르므로 CJK에서 동작하는
대신 인덱스가 커지고 짧은 질의에 노이즈가 늘어난다.

> 명세 §4 3단계에서 FTS5 단독 검색을 비교 대상으로 삼는데, 토크나이저를 잘못 고르면
> "FTS5가 원래 약하다"는 **틀린 결론**을 얻게 된다. 두 토크나이저를 모두 만들어
> 비교한다.

## 6. 훅 동작

```text
  현재    훅 -> 파싱 -> 절단 -> .md/.jsonl 파일로 굳힘
  변경 후 훅 -> 파싱 ─┬─> 절단 -> .md/.jsonl 파일로 굳힘   (그대로 유지)
                      └─> 증분 적재 -> SQLite              (추가)
```

**리포트 생성은 RAG를 구축한 뒤에도 계속한다.** 훅에 적재 갈래를 하나 더 붙이는
것이지, 리포트를 대체하는 것이 아니다. 기존 출력 경로·파일명·절단 동작을 모두 그대로
둔다(AGENTS.md 6항 "재실행 시 동일한 리포트 경로 유지").

이유는 셋이다.

1. RAG는 조각을 돌려주고 리포트는 통째로 보여준다. 무엇을 찾는지 모를 때는 리포트가
   필요하다.
2. 명세 §4 4단계의 검증 — *"인용한 세션과 시각이 실제 로그와 맞는지 확인"* — 에는
   사람이 읽을 수 있는 대조본이 있어야 한다. 원본 JSONL 9MB를 눈으로 훑을 수 없다.
3. RAG는 Claude API가 필요하다. 리포트는 파일 하나 열면 끝난다. 실패 양상이 다르다.

적재 실패가 리포트 생성을 막아서는 안 된다(AGENTS.md 6항). 적재 갈래의 오류는
`stderr`로 보고하고 리포트 경로는 계속 진행한다.

증분 적재는 `session.ingested_lines`를 워터마크로 쓴다. 새 줄만 읽어 `raw_line`에
INSERT하므로 세션이 길어져도 매 턴 비용이 일정하다.

## 7. 검증

명세 §4 1단계의 검증 항목에 다음을 추가한다.

- 합성 로그로 저장 → 조회 왕복.
- 기존 `python -B scripts/export.py --selftest` 33건이 그대로 통과(기존 출력 경로
  무변경).
- 실제 세션 하나를 적재한 뒤 `raw_line`을 이어붙인 결과가 원본 파일과 **바이트 단위로
  일치**하는지 확인.
- resume 쌍(`26b94bea` ↔ `0ec719f4`)을 둘 다 적재한 뒤 `event` 행 수가 합이 아니라
  합집합인지 확인. 기대: 공유 `uuid` 632개만큼 적다.
- 증분 적재: 같은 파일을 두 번 적재해도 `raw_line` 행 수가 늘지 않는지.

## 8. 미결 항목

| 항목 | 내용 |
|---|---|
| `raw_line` → `raw_blob` 접는 시점 | 마지막 수정 후 N일? 아예 접지 않고 128MB를 그대로 두어도 무방하다 |
| `--reindex` 경로 | 원본에서 색인을 재생성하는 명령. 이 설계의 전제이므로 1단계에 포함해야 한다 |
| 청크 단위 | 턴 하나 통째(프롬프트+답변+도구)인가, 프롬프트·답변만인가. 도구 출력은 길고 노이즈가 많지만 "그때 뭐가 실패했지"에는 필요하다. 2단계 사안 |
| Codex 보존 정책 | `~/.codex/config.toml`에 보존 관련 항목이 없다. 30일이라는 근거를 확인하지 못했다. Codex 쪽 미저장분이 121개로 더 크다 |
| 마스킹 | 명세 §3.6의 미결 항목 그대로. 검색된 조각에 키·토큰·경로가 섞일 수 있다 |

---

## 부록. 원본 JSONL 사본은 어디에 있는가

**없다. 만들어진 적이 없다.**

이 프로젝트가 `.jsonl` 파일을 출력하기 때문에 원본이 복사되고 있다고 오해하기 쉽다.
실제로는 다른 물건이다.

```text
  원본 JSONL                          출력 .jsonl 리포트
  ------------------------------      ------------------------------
  type/uuid/parentUuid/sessionId      seq/agent/session_id/kind/ts/...
  isSidechain/cwd/version/gitBranch   (중립 이벤트 스키마)
  message.content[] 블록 원형          text/summary/input/output/status
  도구 UUID로 짝지어진 tool_result     짝은 이미 풀려 status로 접힘
  절단 없음                            4KB 초과분 절단됨
```

경로를 따라가면 이렇다.

```text
service.export()                        service.py:34-41
    rows = read_jsonl(path)             원본 읽기
    log  = adapter.parse(rows)          원본 -> 중립 Event
    write_reports(present(assemble(log)), ...)
                  ^^^^^^^                    ^^^^^^^^
                  절단 적용                   날짜 버킷 + 집계
```

`write_reports()`가 받는 것은 이미 `present()`를 거친 절단본이고, `.md`와 `.jsonl`
**둘 다 그 절단본에서 만들어진다**(`storage.py:78-84`). `render_jsonl()`의
독스트링도 그것을 명시한다 — *"원본 도구 UUID는 버리고 세션 식별 정보만 붙인다"*.
설계상 의도된 손실이다.

실측하면 이렇다.

- 리포트 102 md + 102 jsonl = 10.3MB
- 절단 지점 498곳, 버려진 총량 **4.9MB**
- `.jsonl` 이벤트 중 `truncated` / `truncated_input` 표시가 붙은 것 **493개**

그리고 애초에 284개 세션 중 74개분만 존재한다(§2.2).

정리하면 현재 상태는 이렇다.

```text
  원본 JSONL  --- 30일 후 소멸 --->  없음
      |
      +--> 리포트 (26%만, 그나마 절단)  --- 영구 --->  이것이 전부
```

30일이 지난 세션에서 4KB를 넘던 도구 출력이나 `Write`의 `content`는 복구할 방법이
없다. **이 설계의 `raw_line` / `raw_blob` 테이블이 그 사본을 처음으로 만드는
것이다.** 기존 `.jsonl` 리포트가 그 역할을 한다고 가정하면 안 된다.
