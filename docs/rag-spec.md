# 프롬프트 로그 RAG 명세

## 1. 요약

세션 로그에 RAG를 붙여 **"언제 내가 무엇을 했는지"** 를 질문할 수 있게 만든다.
기존 리포트 파이프라인 옆에 무절단 저장과 검색 계층을 추가하는 작업이다.

**이 작업의 1차 목적은 RAG를 직접 만들어보며 배우는 것이다.** 본격적인 RAG 프로젝트를
시작하기 전에 RAG가 무엇인지 체득하려는 것이므로, **효율만 따져 단계를 생략하면 안 된다.**
이 코퍼스 규모에서는 키워드 검색만으로도 검색 품질이 충분하지만, 그러면 배울 것이
없어지므로 **벡터 검색을 반드시 포함한다.** 오버엔지니어링으로 보이는 부분이 있어도
학습 목적상 의도된 것이다.

구현자는 C#으로 Claude Code류 에이전트를 프레임워크 없이 직접 구현한 경험이 있다
(프롬프트 조립, 스트리밍, 도구 루프, 컨텍스트 압축, MCP 클라이언트, 권한 엔진).
그 프로젝트에 임베딩·벡터·청킹·검색은 한 건도 없다. 즉 **생성(G) 쪽은 이미 손으로 다
짜봤고 검색(R) 쪽만 처음이다.** 설명과 설계는 이 전제로 한다 — 프롬프트 조립과 도구 루프는
아는 것으로 취급하고, 청킹·임베딩·유사도·병합에 무게를 둔다. Python 자체는 학습 중인
언어다.

확정된 결정:

| 항목 | 결정 | 근거 |
|---|---|---|
| 의존성 정책 | 외부 라이브러리 허용, Python 3.11 이상 유지 | 표준 라이브러리 전용은 파이썬 학습 목적이었고 하드 요구사항이 아니었음 |
| 저장 시점 | **원본 JSONL 줄**을 무절단 저장하고, `event`·`turn`은 거기서 만든 파생 색인 | 원본이 있으면 activity·model·usage를 언제든 다시 파싱할 수 있어 무엇을 남길지가 비가역 결정이 아니게 됨 ([상세](rag-store-design.md) §1·§3) |
| 저장 기술 | SQLite | RAG는 검색 가능해야 함. JSONL+gzip은 검색 대상이 못 됨 |
| 저장소 위치 | 리포트 루트의 `promptlog.db`. `--output`을 바꾸면 함께 옮겨 감. 스키마 버전 2 | 테스트·백필이 임시 위치를 쓸 수 있어야 함 |
| 임베딩 위치 | **로컬 모델** | 로그를 외부로 보내지 않기로 결정. 하드웨어(RAM 15.3GB)는 임베딩 모델에 충분 |
| 외부 전송 범위 | 검색된 조각만 Claude API로 | 전체 코퍼스는 외부로 나가지 않음 |
| 구현 방식 | **직접 구현** (LangChain 등 미사용) | 추상화 뒤로 숨으면 학습 목적을 달성할 수 없음 |
| 생성 모델 | Claude API | 로컬 생성 모델은 이 하드웨어(4코어, 내장 GPU)에 부적합 |

측정된 규모는 [RAG 저장소 설계](rag-store-design.md) §2.1의 실측(2026-09-22 기준)을 따른다.

## 2. 아키텍처

기존 파이프라인 `parse → assemble → present → write_reports`는 **변경하지 않는다.**
`parse()`가 만든 `log.events`와 원본 파일의 줄을 소비하는 갈래를 추가한다.

```text
원본 JSONL → adapters/parse() → log.events ─┬→ assemble → present → write_reports  (기존, 무변경)
                                            │
                                            └→ store.py   원본 줄(정본) + event·turn 색인 (SQLite)
                                                 ↓
                                               chunk.py   턴 단위 청킹
                                                 ↓
                                               embed.py   로컬 임베딩 → 벡터
                                                 ↓
질문 → search.py  벡터 + FTS5 + 메타데이터 → RRF 병합 → ask.py  Claude API → 답변 + 인용
```

`store.py`는 원본 파일의 줄을 `raw_line`에 그대로 적재하고(정본), 같은 `log.events`로
`event`·`session_event`·`turn` 색인을 만든다. `service.py`의 `export()`가 `archive()`로
이 둘을 호출하며, 저장 실패는 리포트 생성을 막지 않는다. 색인은 `--reindex`
(`service.reindex()`)로 저장된 원본 줄만으로 다시 만들 수 있다.

새로 추가하는 파일:

| 파일 | 역할 |
|---|---|
| `scripts/promptlog/store.py` | SQLite 스키마와 제자리 업그레이드, 원본 줄 적재(정본), 색인(`event`·`session_event`·`turn`), 턴 지문(`content_hash`) |
| `scripts/promptlog/chunk.py` | 턴 경계 계산과 길이 초과 시 분할 |
| `scripts/promptlog/embed.py` | 로컬 임베딩 모델 래퍼, 배치 인코딩 |
| `scripts/promptlog/search.py` | 코사인 유사도, FTS5, 메타데이터 필터, RRF 병합 |
| `scripts/ask.py` | 질의 CLI. Claude API에 `search_log` 도구를 제공 |

기존 구현과의 관계:

- 세션 키는 `store.py`의 `session_key_of()` — `session_id`, 없으면 원본 파일 stem이다.
  `storage.py`의 `session_hash()`는 리포트 경로 이름에만 쓰이고 저장소 키로는 쓰지 않는다.
- `assemble()`의 턴 경계 규칙(prompt 이벤트마다 새 턴)은 재사용하지 않고
  `store.assign_turns()`에 복제했다. `assemble()`의 루프가 경계 판정·날짜 버킷·상태를
  한 덩어리로 돌려 추출되지 않기 때문이다. 두 구현이 어긋나는지는
  `tests/test_store.py`의 `TurnTests.test_turn_boundaries_match_assemble`이 지킨다.
- `models.py`의 `Event` 계약 — 필드를 추가하되 기존 필드는 그대로 둔다. 추가한 필드는
  `source_id`다.

## 3. 핵심 설계 결정

### 3.1 이벤트 ID

원본에는 쓸 수 있는 식별자가 있다. Claude는 행 `uuid`를, Codex는 item `id`를 가진다.
`Event`에 `source_id` 필드를 추가했고(`models.py`), 어댑터가 `source_ids()`로 원본 ID를
채운다.

`source_id`는 네임스페이스가 박힌 문자열이고 **해시하지 않는다.** 한 원본 행이 이벤트를
여러 개 만들므로 끝에 `#kind`를 붙인다.

- `claude:uuid:{uuid}#{kind}`, `codex:item:{id}#{kind}` — `uuid`와 item `id`는 재개
  복사본에서도 보존되므로 전역 유일하다.
- `claude:line:{session_id}:{_line_no}#{kind}`, `codex:line:{session_key}:{_line_no}#{kind}`
  — 원본에 id가 없는 행(Claude 메타 행, Codex legacy 세션)의 폴백이며 파일 스코프다.
  파싱 순서가 아니라 물리적 줄 번호 `_line_no`를 쓰므로 정렬과 무관하다.
- 같은 행에서 같은 kind가 둘 이상 나올 때만(예: `usage` iterations) `#kind.N`으로 순번을
  붙인다.

키에 `session_id`를 섞지 않는다. 재개하면 `uuid`는 보존되고 `sessionId`만 덮어써지므로,
섞으면 같은 사건이 두 벌 저장된다(Codex 기준 45.7%). 한 이벤트가 여러 세션에 속한다는
사실은 `session_event(session_key, source_id, seq, turn_no)` 매핑에 그대로 기록한다.
형식 전체와 실측은 [RAG 저장소 설계](rag-store-design.md) §3·§4를 본다.

### 3.2 턴 ID

턴은 `(session_key, turn_no)`다. 첫 프롬프트 이전은 턴 0이고 prompt 이벤트마다 새 턴이
시작된다. 턴 번호는 세션 상대적이라, 공유 이벤트가 세션마다 다른 번호의 턴에 속할 수 있다.
규칙은 `assemble()`과 같지만 날짜 버킷과 통계 집계를 뺀 `store.assign_turns()`에 복제돼
있고, 두 구현이 어긋나는지는 회귀 테스트가 지킨다(§2).

턴마다 `content_hash`를 둔다. 턴에 속한 이벤트의 색인 행(`source_id`, `kind`, `ts`,
`text`, `tool_name`, `status`, `payload`)을 순서대로 직렬화한 SHA-256 16진 문자열(64자)이다.
`seq`와 `turn_no`는 넣지 않는다. 앞쪽에 이벤트가 끼어들면 뒤의 `seq`가 전부 밀려 모든
턴의 지문이 바뀌기 때문이다.

- 같은 저장 내용이면 export를 반복하든 `--reindex`를 하든 같은 값이 나온다.
- 지문은 그 세션이 파싱한 결과가 아니라 `event` 표가 말하는 턴 내용을 따른다. 재개
  세션이 공유 이벤트를 바꾸면 다른 세션의 해당 턴 지문도 함께 맞춰진다.
- 스키마 업그레이드 직후의 기존 턴은 `NULL`이고, 그 세션의 다음 적재나 `--reindex`가
  채운다.

2단계가 이 값을 어떻게 쓰는지는 §4에 적는다.

### 3.3 재수집

세션 파일은 시간이 지나며 커지고 Stop 훅은 매 턴 같은 세션을 다시 내보낸다. 같은 세션을
몇 번 내보내도 결과가 같다는 점은 `storage.py`의 "재실행 시 같은 경로에 갱신" 규칙과 같은
성질이며, 저장소는 층마다 다르게 갱신한다.

- **원본 줄(`raw_line`)**: `session.ingested_lines` 워터마크 이후의 새 줄만 적재한다.
  종결자 없는 마지막 줄은 아직 쓰이는 중일 수 있어 저장은 하되 완료로 세지 않고, 다음
  적재가 같은 `line_no`를 덮어쓴다.
- **이벤트(`event`)**: `source_id` 기준 `ON CONFLICT DO UPDATE`로 제자리 갱신한다.
  `INSERT OR REPLACE`는 행을 지웠다 다시 넣으며 `session_event` 외래키 검사를 매번 불러,
  매 턴 세션 전체를 다시 색인하면 O(n²)이 되기 때문에 쓰지 않는다.
- **색인(`session_event`, `turn`)**: 세션 단위로 지우고 다시 쓴다. 증분으로 쓰지 않는
  이유는 새 줄이 기존 턴의 내용을 바꿀 수 있어서다(도구 결과 짝짓기, 정렬상 앞쪽에
  끼어드는 행). 어느 턴이 바뀌었는지는 `content_hash`로 판정한다(§3.2).
- **스키마**: 버전은 `PRAGMA user_version`이다. 새 파일은 최신 스키마로 바로 만들고,
  이전 버전 저장소는 `BEGIN IMMEDIATE` 안에서 버전을 다시 확인한 뒤 `MIGRATIONS`를
  실행하는 제자리 업그레이드로 올린다. 실패하면 이전 버전 그대로 남고, 더 새로운
  버전은 변경 없이 거부한다.
- **복구**: 색인은 원본 줄에서 재생성되는 파생물이다. `--reindex`가 저장된 원본 줄만으로
  `event`·`session_event`·`turn`을 전부 다시 만든다. 비우기와 재생성이 한 트랜잭션이라
  실패하면 기존 색인으로 돌아가고, 같은 입력이면 결과가 같다.

### 3.4 벡터 저장

SQLite BLOB에 벡터를 넣고 numpy 브루트포스로 코사인 유사도를 계산한다. 청크 1만 개 ×
768차원이면 약 30MB이고 행렬곱 한 번이면 끝난다.

sqlite-vec이나 faiss를 쓰면 이 계산이 라이브러리 뒤로 숨는데, **유사도를 직접 계산해보는
것이 이번 학습의 핵심**이므로 처음에는 직접 구현한다. 규모가 커지면 그때 교체한다.

### 3.5 임베딩 모델

한국어와 코드가 섞여 있으므로 다국어 모델이 필요하다. 후보는 multilingual-e5-small/base,
bge-m3 등이다. **어느 것이 이 코퍼스에 맞는지는 검증되지 않았다.** 2단계에서 실제로
비교한다. 설치 용량(torch 경유 1~2.5GB, ONNX 경로 수백 MB)도 그때 함께 확인한다.

### 3.6 프라이버시 경계

로컬에서 끝나는 것: 원본 로그, SQLite 저장, 청킹, 임베딩, 벡터 검색.
외부로 나가는 것: 검색으로 선택된 조각과 질문만. **전체 코퍼스를 외부로 보내는 경로는
만들지 않는다.**

단, 선택된 조각에도 키·토큰·경로가 섞일 수 있다. 마스킹 규칙은 아직 설계되지 않은
미결 항목이다.

## 4. 단계

각 단계는 독립적으로 멈출 수 있다. 단계마다 확인을 받고 다음으로 넘어간다.

### 1단계 — SQLite 무절단 저장 (`store.py`) — 완료

원본 JSONL 줄을 무손실로 적재하고(`raw_line`), 이벤트 색인(`event`·`session_event`·`turn`)을
그 위에 파생물로 만들었다. `Event.source_id`와 두 어댑터의 원본 ID 발급, export 경로(Stop
훅 포함)의 적재 연결, 원본 줄만으로 색인을 되살리는 `--reindex`가 여기에 들어간다. 이어서
턴 내용 해시(`content_hash`)와 스키마 버전 2 업그레이드를 더했다.

- [`add-raw-log-store`](../openspec/changes/archive/2026-10-01-add-raw-log-store/):
  저장소 골격, 무손실 적재, `source_id`, 색인 적재, 파이프라인 연결, `--reindex`.
- [`add-turn-content-hash`](../openspec/changes/archive/2026-10-02-add-turn-content-hash/):
  `turn.content_hash`, 공유 이벤트 지문 재계산, `session_event(source_id)` 인덱스,
  스키마 v1 → v2.

기존 출력 경로·리포트 내용이 그대로인지는 `--selftest`와 `tests/`의 단위 테스트가 지킨다.
상세 설계는 [RAG 저장소 설계](rag-store-design.md)를 본다.

### 2단계 — 청킹과 임베딩 (`chunk.py`, `embed.py`)

턴 단위 청킹, 길이 초과 시 분할, 로컬 모델로 배치 인코딩, 벡터를 SQLite에 저장.
이미 임베딩된 청크는 건너뛰는 증분 처리.

1단계가 만든 제약. 세부는 2단계 설계에서 확정한다.

- 어느 턴이 바뀌었는지는 `turn.content_hash`로 판정한다. 해시는 재색인해도 바뀌지 않으므로
  `--reindex`가 임베딩을 무효화하지 않는다. 이 값은 [RAG 저장소 설계](rag-store-design.md)
  §5가 제안한 `chunk.content_hash`(청크 텍스트의 해시, 아직 구현되지 않음)와 이름만 같은
  다른 값이다. 임베딩 표의 키를 턴 지문으로 둘지 청크 해시로 둘지는 2단계 설계에서 정한다.
- `turn`과 `session_event` 행은 export마다 세션 단위로 지워졌다 다시 쓰이고
  `foreign_keys`는 켜져 있다. 임베딩 표가 `turn`을 외래키로 참조하면 삭제 때 cascade로
  임베딩이 지워지거나 외래키 위반으로 적재가 실패할 수 있다(코드를 읽고 추론한 것이며
  재현하지 않았다).
- `content_hash`가 `NULL`인 턴은 "아직 계산되지 않음"이지 "바뀌지 않음"이 아니다.

검증: 청크 수·차원·용량을 실측해 기록. 임베딩 모델 후보 두 개를 같은 질의로 비교.

### 3단계 — 하이브리드 검색 (`search.py`)

코사인 유사도 직접 구현, FTS5 인덱스, 기간·도구·상태 메타데이터 필터, RRF로 병합.

검증(**학습의 핵심 지점**): 같은 질문 세트를 **벡터만 / FTS5만 / 하이브리드** 세 방식으로
돌려 결과를 비교한다. 특히 "지난주에 뭐 했지" 같은 시간 질의에서 벡터 검색이 실패하는
것을 직접 확인한다. 임베딩에는 시간 개념이 없다는 것을 실험으로 체감하는 단계다.

### 4단계 — Claude API 연결 (`scripts/ask.py`)

`search_log` 도구 정의, 질문 → 도구 호출 → 검색 → 답변 + 인용(세션·시각 표시).

검증: 실제 질문 몇 개로 끝에서 끝까지 실행. 인용한 세션과 시각이 실제 로그와 맞는지 확인.

### 전체 검증

`python -B scripts/export.py --selftest`. 

## 5. 정책 변경

표준 라이브러리 전용 규칙을 폐기하고 외부 라이브러리를 허용한다. Python 3.11 이상
요구사항은 유지한다. 의존성은 목적을 설명하고 최소한으로 추가한다.

반영 위치:

| 파일 | 변경 | 상태 |
|---|---|---|
| `AGENTS.md` 2항 | 표준 라이브러리 전용 문구 제거, 외부 라이브러리 허용 명시 | 반영됨 |
| `README.md` | "Python 3.10 이상과 표준 라이브러리만" → 3.11 기준으로 통일하고 전용 서술 제거 | 반영됨. 108행. 리포트 내보내기 경로가 표준 라이브러리만 쓴다는 현황 서술은 남아 있다 |
| `dev-stack-python.md` | 의존성 파일이 없다는 서술에 정책 변경 한 줄 추가 | 반영됨. 25행 부근 |

`pyproject.toml`은 첫 의존성을 실제로 추가할 때 만든다. `harness-lightening-plan.md`의
표준 라이브러리 언급은 특정 시점의 분석 기록이므로 수정하지 않는다.

