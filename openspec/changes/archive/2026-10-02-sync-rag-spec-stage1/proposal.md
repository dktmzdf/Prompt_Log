# Proposal

## Why

`docs/rag-spec.md`가 1단계 구현 결과와 어긋나 있다. 1단계(`add-raw-log-store`)가 실측으로
명세의 제안 몇 가지를 뒤집었고, 그 내용은 `docs/rag-store-design.md` §3에 "명세에서
수정하는 부분"으로 기록됐다. 그 문서가 "명세보다 이 문서가 우선한다"고 선언하는 방식이라,
명세 본문만 읽으면 틀린 설계(`sha256(session_id + source_id)`, `INSERT OR REPLACE`,
`log.events` 저장 등)를 그대로 따르게 된다.

2단계(청킹·임베딩)를 시작하기 전에 명세를 맞춰 둔다. 2단계 change는 이 명세를 출발점으로
읽는데, 거기서 외래키·증분 키 같은 결정이 1단계의 실제 동작을 전제로 해야 하기 때문이다.
`add-raw-log-store`와 `add-turn-content-hash`는 둘 다 이 문서 작업을 범위 밖으로 미뤄 두었다.

## What Changes

`docs/rag-spec.md` 한 파일만 고친다. 1단계가 만든 사실을 반영하고, 아직 구현되지 않은
2~4단계 설계와 확정 결정(§3.4~3.6)은 그대로 둔다.

- **저장 시점.** "파서 원본 이벤트(`log.events`)"를 "원본 JSONL 줄을 무절단 저장하고
  `event`·`turn`은 파생 색인"으로 바꾼다. 저장소 위치와 스키마 버전(현재 2)을 적는다.
- **아키텍처.** `store.py`의 역할(원본 정본, 색인, `--reindex`, 턴 지문)을 맞추고,
  재사용 목록의 틀린 항목을 바로잡는다. 세션 키는 `session_hash()`가 아니라 `session_id`
  또는 파일 stem이고(`store.py:156`), 턴 경계 규칙은 재사용이 아니라 복제와 동치
  회귀 테스트다.
- **§3.1 이벤트 ID.** `source_id`를 네임스페이스 문자열(해시 안 함)로, `session_id`는
  키에서 제외하고 귀속을 `session_event`로, 폴백을 `_line_no`로 고친다. "정렬 때문에 순서
  인덱스가 불안정하다"는 진단은 `rag-store-design.md` §3대로 삭제한다.
- **§3.2 턴 ID.** `turn_no`(세션 상대)와 `content_hash`(SHA-256, `seq`·`turn_no` 제외)를
  적는다.
- **§3.3 재수집.** `INSERT OR REPLACE`를 실제 동작으로 바꾼다. 원본 줄은 워터마크
  증분, `event`는 `ON CONFLICT DO UPDATE`, 색인은 세션 단위 재작성, 스키마는 제자리 업그레이드.
- **§4 단계.** 1단계를 완료로 표시하고 산출물과 아카이브된 change 두 개를 링크한다.
  2단계의 증분 처리는 `(session_key, turn_no, content_hash)` 키로 하고 `turn`에 외래키를
  걸지 않는다고 적는다(색인이 매 턴 지웠다 다시 쓰이므로). `NULL` 해시는 "미계산"이다.
- **§5 정책 변경.** `AGENTS.md`, `README.md`, `dev-stack-python.md` 반영을 확인했으므로
  "반영됨"으로 표시한다.
- **§1 규모.** 2026-09-21 수치 대신 `rag-store-design.md` §2.1 실측을 가리킨다.

BREAKING 변경은 없다. 코드, 테스트, 다른 문서는 건드리지 않는다.

### Non-goals

- 2~4단계 설계 변경. 청킹 단위, 임베딩 모델 비교, 검색 병합은 해당 단계에서 정한다.
- `docs/rag-store-design.md` 수정. 그 문서는 이미 정확하다. 명세를 맞춘 뒤 "명세보다 이
  문서가 우선한다"는 안내를 완화할지는 따로 정한다.
- 과거 로그 백필, Codex 보존 정책, `raw_line` 압축. 저장소 사용 방식에 대한 결정이고
  명세 갱신과 무관하다.
- 증분 색인(`add-raw-log-store` 보류 #2)과 WAL 전환 경합(`add-turn-content-hash` 보류 #1).
  둘 다 하지 않기로 했다.

## Capabilities

### New Capabilities

없음.

### Modified Capabilities

없음. 문서만 바뀌고 스펙 수준 동작은 그대로라 `.openspec.yaml`에 `skip_specs: true`를
둔다.

## Impact

- 변경 파일: `docs/rag-spec.md`
- 코드, 의존성, CLI·훅 계약, 리포트 출력, 저장소 스키마: 영향 없음.
- 다음 작업인 2단계 change가 이 문서를 기준으로 쓴다.
