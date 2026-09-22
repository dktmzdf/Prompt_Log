# Spec Delta

## Purpose

에이전트 세션 원본 로그가 출처에서 삭제된 뒤에도 바이트 단위로 동일한 사본을
보존하는 저장소. 이 저장소가 정본이며, 다른 모든 산출물은 여기서 파생된다.

## ADDED Requirements

### Requirement: 원본 무손실 보존

The system SHALL store every line of a source session log without modification,
truncation, or reordering.

#### Scenario: 적재 결과가 원본과 바이트 단위로 일치한다

- **WHEN** 세션 로그 파일을 적재한 뒤 저장된 줄을 원래 순서로 이어붙인다
- **THEN** 결과가 원본 파일과 바이트 단위로 일치한다

#### Scenario: 4KB를 넘는 내용도 잘리지 않는다

- **WHEN** 4KB를 초과하는 도구 출력이나 파일 내용을 담은 세션을 적재한다
- **THEN** 저장된 내용에 절단이 없다

#### Scenario: 파싱되지 않는 줄도 보존된다

- **WHEN** 세션 파일에 JSON으로 파싱되지 않는 줄이 포함되어 있다
- **THEN** 파서가 그 줄을 건너뛰더라도 원문 그대로 저장된다

### Requirement: 증분 적재

The system SHALL ingest only the lines that have not been ingested for that
session, so that per-turn cost does not grow with session length.

#### Scenario: 이어붙은 세션은 새 줄만 적재된다

- **WHEN** 이미 적재한 세션 파일에 줄이 추가된 뒤 다시 적재한다
- **THEN** 추가된 줄만 새로 저장되고 기존 줄은 다시 쓰이지 않는다

#### Scenario: 턴당 비용이 세션 길이에 비례하지 않는다

- **WHEN** 여러 턴에 걸쳐 매 턴 적재가 실행된다
- **THEN** 각 적재가 저장하는 줄 수는 그 턴에 추가된 줄 수에 비례한다

### Requirement: 재적재 멱등성

Re-ingesting an unchanged session SHALL NOT duplicate or alter stored content.

#### Scenario: 변경 없는 파일을 두 번 적재한다

- **WHEN** 내용이 바뀌지 않은 세션 파일을 두 번 적재한다
- **THEN** 저장된 줄 수와 내용이 첫 적재 직후와 같다

### Requirement: 적재 실패는 리포트 생성을 막지 않는다

An ingestion failure SHALL NOT prevent report generation and SHALL be reported
on `stderr` without blocking the agent.

#### Scenario: 적재 중 오류가 발생한다

- **WHEN** 저장소 적재가 실패한다
- **THEN** 리포트는 정상적으로 생성되고, 오류 내용이 `stderr`에 표시되며, Stop 훅이
  에이전트 실행을 차단하지 않는다

### Requirement: 기존 리포트 동작 보존

Introducing the store SHALL NOT change report paths, file names, or report
content.

#### Scenario: 저장소 도입 전후 리포트가 같다

- **WHEN** 저장소를 도입한 뒤 같은 세션을 내보낸다
- **THEN** 리포트의 경로·파일명·내용이 도입 전과 동일하다

#### Scenario: 기존 회귀 검사가 통과한다

- **WHEN** 기존 자기검사를 실행한다
- **THEN** 기존 검사 항목이 하나도 실패하지 않는다

### Requirement: 저장소 위치

The store SHALL default to the report root and SHALL follow the output root
when it is overridden, so that tests and backfills can target a temporary
location.

#### Scenario: 출력 루트를 재지정한다

- **WHEN** 출력 루트를 지정해 내보내기를 실행한다
- **THEN** 저장소도 그 루트 아래에 생성되며 기본 위치는 사용되지 않는다
