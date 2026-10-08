# Spec Delta

## Purpose

청크 텍스트를 로컬 모델로 벡터화해 보관한다. 벡터는 텍스트 내용과 모델에 묶여
재색인이나 턴 재작성에 영향받지 않고, 계산은 Stop 훅과 분리된 명령에서만 일어나며,
로그 내용은 외부로 나가지 않는다.

## ADDED Requirements

### Requirement: 텍스트와 모델 단위의 벡터 저장

The system SHALL store at most one vector per chunk text hash and model, keyed by
that pair and not by the chunk's session or turn, and each stored vector SHALL be
L2-normalized and record its model name and dimension.

#### Scenario: 같은 텍스트와 모델은 한 번만 저장된다

- **WHEN** 같은 텍스트를 가진 청크 두 개를 같은 모델로 임베딩한다
- **THEN** 벡터는 한 개만 저장되고 두 청크가 모두 그것을 가리킨다

#### Scenario: 모델이 다르면 벡터가 공존한다

- **WHEN** 같은 청크를 서로 다른 두 모델로 임베딩한다
- **THEN** 모델마다 벡터가 따로 저장되고 서로를 덮어쓰지 않는다

#### Scenario: 저장된 벡터는 정규화되어 있다

- **WHEN** 저장된 임의의 벡터를 읽는다
- **THEN** 길이(L2 노름)가 1과 같다고 볼 수 있을 만큼 가깝고, 기록된 차원이 벡터의
  실제 길이와 같다

### Requirement: 증분 계산

The embedding command SHALL compute vectors only for chunk text and model pairs
that have none, SHALL keep existing vectors when turns are rewritten or the
index is rebuilt, and SHALL be safe to interrupt and rerun.

#### Scenario: 변경이 없으면 아무것도 계산하지 않는다

- **WHEN** 모든 청크의 벡터가 이미 있는 상태에서 임베딩 명령을 다시 실행한다
- **THEN** 모델이 한 번도 호출되지 않고 저장된 벡터가 바뀌지 않는다

#### Scenario: 새 텍스트만 계산한다

- **WHEN** 세션에 새 턴이 추가되어 청크가 늘어난 뒤 임베딩 명령을 실행한다
- **THEN** 새 청크의 벡터만 계산된다

#### Scenario: 텍스트가 바뀐 청크는 새 벡터를 얻는다

- **WHEN** 청크 텍스트가 바뀌어 텍스트 해시가 달라진다
- **THEN** 바뀐 텍스트의 벡터가 새로 계산된다

#### Scenario: 재색인은 벡터를 지우지 않는다

- **WHEN** 벡터가 있는 상태에서 색인을 재생성한 뒤 청크를 갱신하고 임베딩 명령을 실행한다
- **THEN** 이미 있던 벡터가 그대로 남고 새로 계산되는 것이 없다

#### Scenario: 중간에 중단해도 이어서 할 수 있다

- **WHEN** 임베딩 명령이 일부 배치만 저장한 채 중단된 뒤 다시 실행된다
- **THEN** 이미 저장된 벡터는 다시 계산되지 않고 나머지만 계산된다

### Requirement: 모델별 입력 규칙

The system SHALL encode each text with the pooling, normalization, and text
prefixes that its model requires, and SHALL NOT silently truncate a text that
exceeds the model's maximum input length.

#### Scenario: 모델마다 정해진 풀링과 접두어를 쓴다

- **WHEN** 청크와 질문을 임베딩한다
- **THEN** 접두어가 필요한 모델에서는 청크에 문서용 접두어, 질문에 질문용 접두어가
  붙고, 접두어가 필요 없는 모델에서는 아무것도 붙지 않으며, 풀링 방식은 모델이 정한
  방식이다

#### Scenario: 질문과 청크는 같은 공간에서 비교된다

- **WHEN** 같은 모델로 질문과 청크를 임베딩해 내적을 구한다
- **THEN** 내적은 두 정규화된 벡터의 코사인 유사도와 같다

#### Scenario: 입력 한도를 넘는 텍스트는 조용히 잘리지 않는다

- **WHEN** 모델의 최대 입력 길이를 넘는 텍스트를 임베딩하려 한다
- **THEN** 그 텍스트는 잘린 채 저장되지 않고, 오류나 경고로 드러난다

### Requirement: 훅과의 격리

Embedding SHALL run only from its own command and SHALL NOT run in, delay, or
depend on the Stop hook, and a missing embedding dependency or model SHALL NOT
affect report export or index writing.

#### Scenario: 훅은 임베딩을 하지 않는다

- **WHEN** Stop 훅이 세션을 내보낸다
- **THEN** 모델을 불러오거나 벡터를 계산하지 않고, 훅의 동작과 시간이 임베딩 도입 전과
  같다

#### Scenario: 임베딩 의존성이 없어도 내보내기는 동작한다

- **WHEN** 임베딩에 필요한 라이브러리가 설치되어 있지 않은 환경에서 세션을 내보낸다
- **THEN** 리포트 생성과 색인 적재는 정상이다

#### Scenario: 임베딩 명령은 의존성이 없으면 분명히 실패한다

- **WHEN** 임베딩에 필요한 라이브러리나 모델 파일이 없는 상태에서, 내려받기를 요청하지
  않고 임베딩 명령을 실행한다
- **THEN** 무엇이 없는지 `stderr`에 알리고 0이 아닌 종료 코드를 돌려주며, 아무 것도
  내려받지 않고, 저장소의 원본 줄과 색인은 바뀌지 않는다

### Requirement: 로그 내용의 외부 전송 금지

Embedding SHALL run entirely on the local machine, and no chunk text, prompt, or
other log content SHALL be sent over the network. Only model files MAY be
downloaded.

#### Scenario: 임베딩 중 네트워크로 로그 내용이 나가지 않는다

- **WHEN** 모델 파일이 이미 내려받아진 상태에서 네트워크 연결을 막고 임베딩 명령을 실행한다
- **THEN** 연결 시도 없이 끝까지 정상적으로 동작한다

#### Scenario: 내려받는 것은 모델 파일뿐이다

- **WHEN** 내려받기를 명시적으로 요청했고 모델 파일이 없다
- **THEN** 요청에는 모델을 식별하는 값만 있고 청크 텍스트나 질문은 포함되지 않는다
