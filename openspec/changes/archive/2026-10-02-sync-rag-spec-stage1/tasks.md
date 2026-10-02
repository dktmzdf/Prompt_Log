# Tasks

## 1. 저장 시점과 아키텍처 (§1, §2)

- [x] 1.1 §1 확정 결정 표의 "저장 시점" 행을 "원본 JSONL 줄을 무절단 저장, `event`·`turn`은
  파생 색인"으로 바꾸고, 저장소 위치(리포트 루트의 `promptlog.db`)와 스키마 버전(현재 2)을
  적는다. "측정된 규모" 단락은 `rag-store-design.md` §2.1 실측을 가리키게 한다. 표에서
  `log.events` 저장 서술이 사라지고 링크 대상 절이 실제로 있는지 `grep`으로 확인한다.
- [x] 1.2 §2의 `store.py` 역할 설명과 다이어그램을 원본 정본·색인·`--reindex`·턴 지문에 맞춘다.
  "재사용하는 기존 구현" 목록은 design.md 결정 2·4대로 고친다. 세션 키는
  `session_key_of()`(`session_id` 또는 파일 stem), 턴 경계 규칙은 복제와 동치 회귀 테스트다.
  동치를 지키는 테스트가 `tests/`에 실제로 있는지 찾아 이름을 적고, 코드의 해당 함수
  이름이 문서와 일치하는지 `grep`으로 확인한다.

## 2. 핵심 설계 결정 (§3.1~3.3)

- [x] 2.1 §3.1 이벤트 ID를 `source_id` 네임스페이스 문자열(해시 안 함), `session_id` 제외와
  `session_event` 귀속, 폴백 `_line_no`로 고치고 "정렬 불안정" 진단을 삭제한다. 형식 전체는
  `rag-store-design.md` §4로 링크한다. 문서의 형식 예시가 `adapters/`의 실제 발급 코드
  (`models.py`의 `source_ids`)와 같은지 읽어서 확인한다.
- [x] 2.2 §3.2 턴 ID에 `turn_no`(세션 상대)와 `content_hash`(SHA-256, `seq`·`turn_no` 제외)를
  적는다. `store.fingerprint()`가 실제로 그렇게 계산하는지 코드로 확인한다.
- [x] 2.3 §3.3 재수집을 실제 동작으로 고친다. 원본 줄은 `ingested_lines` 워터마크 증분, `event`는
  `ON CONFLICT DO UPDATE`, 색인은 세션 단위 재작성, 스키마는 `BEGIN IMMEDIATE` 제자리
  업그레이드, 복구는 `--reindex`다. `store.py`의 `upsert_events`·`write_index`·`ensure_schema`와
  `service.reindex`를 읽어 서술과 일치하는지 확인한다.

## 3. 단계와 정책 (§4, §5)

- [x] 3.1 §4 1단계를 완료로 표시하고 산출물을 한 단락으로 요약한 뒤, 아카이브된
  `2026-10-01-add-raw-log-store`와 `2026-10-02-add-turn-content-hash`를 링크한다. 두 링크
  경로가 실제로 존재하는지 확인한다.
- [x] 3.2 §4 2단계의 "이미 임베딩된 청크는 건너뛰는 증분 처리" 문장에 1단계가 만든 제약을
  덧붙인다. 증분 키는 `(session_key, turn_no, content_hash)`이고, `turn`이 매 턴 지웠다
  다시 쓰이므로 외래키를 걸면 cascade로 임베딩이 지워질 수 있고, `NULL` 해시는 "미계산"이다.
  design.md 결정 4대로 "2단계 설계에서 확정"이라고 표시하며, 해결 방법은 쓰지 않는다.
- [x] 3.3 §5 정책 변경의 반영 위치 표를 "반영됨"으로 표시한다. `AGENTS.md` 2항, `README.md`
  108행 부근, `docs/dev-stack-python.md` 20~25행 부근을 `grep`으로 다시 읽어 실제로
  반영돼 있는지 확인한 뒤 표시한다.

## 4. 최종 검증

- [x] 4.1 `docs/rag-store-design.md` §3 표의 5개 행이 모두 `rag-spec.md` 본문에 반영됐는지 행마다
  대조하고, 같은 주제에서 두 문서의 서술이 다른 곳이 없는지 확인한다. 결과를 보고한다.
  (5행 모두 반영됨. 3.2의 증분 키 서술이 상세 설계 §5와 어긋나 `rag-spec.md`를 고쳤고,
  상세 문서 쪽 낡은 곳은 design.md 보류 #3에 기록했다.)
- [x] 4.2 `python -B scripts/export.py --selftest`가 통과하고, `git diff --stat`에
  `docs/rag-spec.md`만 나타나는지 확인한다(코드·테스트·다른 문서 무변경). `rag-spec.md`의 상대
  링크가 모두 실제 파일을 가리키는지 확인한다.
