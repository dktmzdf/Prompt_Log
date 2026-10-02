# Spec Delta

## ADDED Requirements

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
