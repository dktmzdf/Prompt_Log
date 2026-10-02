# log-index Specification

## Purpose

적재된 원본에서 파생되어 검색을 가능하게 하는 이벤트 색인. 원본이 정본이므로 색인은
언제든 폐기하고 재생성할 수 있어야 하며, 그 성질이 이 능력의 핵심 계약이다.

## Requirements

### Requirement: 이벤트 식별

Each parsed event SHALL carry a stable identifier derived from the source
record, independent of which session file it was read from.

#### Scenario: 같은 원본 레코드는 같은 식별자를 얻는다

- **WHEN** 같은 원본 레코드가 두 개의 서로 다른 세션 파일에 들어 있고 둘 다 적재한다
- **THEN** 그 레코드에서 나온 이벤트의 식별자가 동일하다

#### Scenario: 한 레코드가 만드는 여러 이벤트는 서로 구분된다

- **WHEN** 하나의 원본 레코드가 서로 다른 종류의 이벤트를 여러 개 만든다
- **THEN** 각 이벤트가 서로 다른 식별자를 얻는다

#### Scenario: 원본에 식별자가 없는 경우

- **WHEN** 원본 레코드에 식별자가 없다
- **THEN** 해당 파일 안에서 안정적인 위치 기반 식별자를 부여하고, 그 식별자의 유효
  범위가 그 파일로 한정됨을 식별자 자체로 알 수 있다

### Requirement: 재개 세션 중복 병합

Events shared between a session and a session that resumed it SHALL be stored
once, not once per session.

#### Scenario: 재개된 세션 쌍을 모두 적재한다

- **WHEN** 한 세션과 그 세션을 재개한 세션을 모두 적재한다
- **THEN** 두 파일이 공유하는 이벤트가 한 번만 저장되고, 이벤트 총 개수가 두 세션의
  합이 아니라 합집합이다

### Requirement: 세션 귀속과 턴 번호

Session membership and turn number SHALL be recorded per session, not stored on
the event itself, because the same event can belong to more than one session at
a different turn position.

#### Scenario: 공유된 이벤트의 턴 번호가 세션마다 다르다

- **WHEN** 한 이벤트가 두 세션에 속하고 각 세션에서 턴 위치가 다르다
- **THEN** 세션별로 서로 다른 턴 번호가 기록되고 어느 쪽도 다른 쪽을 덮어쓰지 않는다

#### Scenario: 사용자 프롬프트가 턴 경계를 만든다

- **WHEN** 사용자 프롬프트 이벤트가 나타난다
- **THEN** 새 턴이 시작되고 다음 프롬프트 전까지의 이벤트가 그 턴에 귀속된다

#### Scenario: 첫 프롬프트 이전의 이벤트

- **WHEN** 세션의 첫 프롬프트보다 앞선 이벤트가 있다
- **THEN** 그 이벤트도 유실되지 않고 조회 가능한 위치에 귀속된다

### Requirement: 색인 재생성

The index SHALL be fully reconstructible from stored raw content alone, without
access to the original source files.

#### Scenario: 원본 파일 없이 재색인한다

- **WHEN** 색인을 전부 삭제하고 재생성을 실행하며 원본 세션 파일에는 접근할 수 없다
- **THEN** 저장된 원본 내용만으로 색인이 복원된다

#### Scenario: 재색인은 반복해도 같은 결과를 낸다

- **WHEN** 같은 저장 내용에 대해 재색인을 두 번 실행한다
- **THEN** 두 번의 결과 색인이 동일하다

#### Scenario: 재색인은 원본을 변경하지 않는다

- **WHEN** 재색인을 실행한다
- **THEN** 저장된 원본 내용이 한 바이트도 변경되지 않는다

### Requirement: 턴 변경 감지

Each indexed turn SHALL carry a content fingerprint that reflects the content
currently stored for that turn, including events it shares with other sessions,
so that a downstream consumer can tell which turns changed since it last
processed them without keeping index rows alive.

#### Scenario: 영향받지 않은 턴은 지문이 그대로다

- **WHEN** 세션 파일에 줄이 추가된 뒤 다시 적재하고, 추가된 줄이 기존 어떤 턴의 내용도
  바꾸지 않는다
- **THEN** 기존 턴의 지문이 모두 이전 적재 직후와 같다

#### Scenario: 내용이 바뀐 턴만 지문이 바뀐다

- **WHEN** 추가된 줄이 기존 한 턴에 속한 이벤트의 내용을 바꾼다 (예: 이전 턴의 도구
  호출에 도구 결과가 짝지어진다)
- **THEN** 그 턴의 지문만 바뀌고 다른 턴의 지문은 그대로다

#### Scenario: 앞쪽에 끼어든 이벤트는 그 구간의 지문만 바꾼다

- **WHEN** 정렬상 첫 프롬프트 이전 위치로 들어가는 이벤트가 추가되어 뒤따르는
  이벤트들의 세션 내 순번이 밀린다
- **THEN** 첫 프롬프트 이전 구간의 지문만 바뀌고, 이후 턴의 턴 번호와 지문은 그대로다

#### Scenario: 새 턴은 지문과 함께 생긴다

- **WHEN** 새 사용자 프롬프트가 추가된 세션을 다시 적재한다
- **THEN** 새 턴이 지문을 가진 채로 기록된다

#### Scenario: 재색인은 지문을 바꾸지 않는다

- **WHEN** 같은 저장 내용에 대해 재색인을 실행한다
- **THEN** 모든 턴의 지문이 재색인 전과 같다

#### Scenario: 다른 세션이 공유 이벤트를 바꾸면 이 세션의 해당 턴 지문도 바뀐다

- **WHEN** 두 세션이 어떤 이벤트를 공유하고, 한쪽 세션에서 그 이벤트의 내용이 바뀐다
  (예: 재개 세션에서 공유 도구 호출에 결과가 붙는다)
- **THEN** 다른 세션에서 그 이벤트가 속한 턴의 지문도 저장된 내용에 맞게 바뀌고, 그
  세션의 나머지 턴 지문은 그대로다

#### Scenario: 지문은 저장된 턴 내용과 항상 일치한다

- **WHEN** 세션들을 어떤 순서로 적재하거나 재적재하거나 재색인하든
- **THEN** 모든 턴의 지문이 저장된 이벤트 내용으로 다시 계산한 값과 같다

#### Scenario: 이벤트를 공유하지 않는 세션은 영향받지 않는다

- **WHEN** 다른 세션을 적재한다
- **THEN** 그 세션과 이벤트를 공유하지 않는 세션의 지문은 바뀌지 않는다

#### Scenario: 아직 계산되지 않은 지문은 구분된다

- **WHEN** 지문 도입 이전에 색인되어 아직 다시 적재되지 않은 턴이 있다
- **THEN** 그 턴의 지문은 계산된 지문과 구분되는 빈 값이며, 해당 세션을 다시 적재하거나
  재색인하면 채워진다
