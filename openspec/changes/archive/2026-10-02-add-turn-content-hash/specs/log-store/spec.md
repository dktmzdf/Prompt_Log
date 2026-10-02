# Spec Delta

## ADDED Requirements

### Requirement: 저장소 스키마 업그레이드

Opening a store created by an earlier schema version SHALL upgrade it in place
without losing or altering stored raw lines, and a store from a newer schema
version SHALL be refused without modification.

#### Scenario: 이전 버전 저장소를 연다

- **WHEN** 이전 스키마 버전으로 만들어진 저장소를 연다
- **THEN** 저장소가 현재 버전으로 업그레이드되고, 저장된 원본 줄과 세션 정보가 바이트
  단위로 그대로이며, 기존 색인 행도 유지된다

#### Scenario: 업그레이드된 저장소를 다시 연다

- **WHEN** 이미 업그레이드된 저장소를 다시 연다
- **THEN** 오류 없이 열리고 저장 내용이 바뀌지 않는다

#### Scenario: 업그레이드가 중간에 실패한다

- **WHEN** 업그레이드 도중 오류가 발생한다
- **THEN** 저장소는 업그레이드 이전 버전과 내용 그대로 남는다

#### Scenario: 더 새로운 버전의 저장소를 연다

- **WHEN** 현재 코드보다 새로운 스키마 버전의 저장소를 연다
- **THEN** 저장소를 변경하지 않고 오류로 거부한다
