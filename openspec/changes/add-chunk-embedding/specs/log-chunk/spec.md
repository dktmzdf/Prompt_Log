# Spec Delta

## Purpose

턴 색인에서 검색 결과로 돌려줄 텍스트 조각(청크)을 만든다. 청크는 사용자가 무엇을
물었고 무슨 일이 있었는지를 검색하기 좋게 담고, 색인처럼 언제든 다시 만들 수 있는
파생물이다.

## ADDED Requirements

### Requirement: 청크 내용

A chunk's text SHALL consist only of the user prompt text, the assistant answer
text, and one digest line per tool call, in event order. It SHALL NOT contain
tool output, thinking, or model, usage, and activity records.

#### Scenario: 도구 결과 본문은 청크에 없다

- **WHEN** 한 턴에 4KB를 넘는 출력을 낸 도구 호출이 있다
- **THEN** 그 턴의 청크 텍스트에 도구 출력의 어떤 부분도 들어 있지 않고, 도구 호출은
  한 줄 요약으로만 나타난다

#### Scenario: 생각 과정은 청크에 없다

- **WHEN** 한 턴에 thinking 이벤트가 있다
- **THEN** 그 이벤트의 텍스트는 청크에 들어 있지 않다

#### Scenario: 도구 한 줄 요약은 도구와 대상과 실패 여부를 담는다

- **WHEN** 한 턴에 성공한 도구 호출과 실패한 도구 호출이 있다
- **THEN** 각 호출이 도구 이름과 로그에 있는 핵심 인자(파일 경로, 명령 설명 등)로 한
  줄씩 나타나고, 실패한 호출만 실패로 표시된다

#### Scenario: 세션 작업 디렉터리 아래 경로는 상대 경로로 줄어든다

- **WHEN** 도구 호출의 대상 경로가 세션의 작업 디렉터리 아래에 있다
- **THEN** 한 줄 요약에는 그 디렉터리를 뺀 상대 경로가 나타난다

#### Scenario: 답변이 없는 턴은 프롬프트만으로 만든다

- **WHEN** 한 턴에 프롬프트만 있고 답변이 없다
- **THEN** 프롬프트를 담은 청크가 만들어진다

#### Scenario: 본문이 없는 턴은 청크를 만들지 않는다

- **WHEN** 한 턴의 이벤트가 모두 제외 대상이라 청크에 넣을 텍스트가 없다
- **THEN** 그 턴에는 청크가 만들어지지 않는다

### Requirement: 길이 한도와 분할

No chunk SHALL exceed the configured token limit. A turn that exceeds it SHALL
be split at event boundaries, an event that alone exceeds it SHALL be split at
paragraph boundaries, and a paragraph that still exceeds it SHALL be split at
token boundaries, so that no chunk loses content to truncation.

#### Scenario: 한도 안의 턴은 청크 하나다

- **WHEN** 한 턴의 텍스트가 한도 이하다
- **THEN** 그 턴의 청크는 하나이고 부분 번호가 첫 번째다

#### Scenario: 한도를 넘는 턴은 이벤트 경계에서 나뉜다

- **WHEN** 한 턴의 이벤트들을 합친 텍스트가 한도를 넘고 각 이벤트는 한도 이하다
- **THEN** 이벤트가 중간에서 잘리지 않고 순서대로 여러 청크에 나뉘어 담기며, 모든 청크가
  한도 이하다

#### Scenario: 이어지는 청크도 어느 질문의 것인지 알 수 있다

- **WHEN** 한 턴이 여러 청크로 나뉜다
- **THEN** 두 번째 이후 청크에도 그 턴의 프롬프트 앞부분이 들어 있다

#### Scenario: 한도를 넘는 이벤트 하나는 문단 경계에서 나뉜다

- **WHEN** 답변 하나가 단독으로 한도를 넘고 문단 구분이 있다
- **THEN** 문단 경계에서 나뉘고 모든 청크가 한도 이하다

#### Scenario: 문단 하나가 한도를 넘으면 토큰 경계에서 나뉜다

- **WHEN** 문단 구분 없이 한도를 넘는 텍스트 하나가 있다
- **THEN** 토큰 경계에서 나뉘고 모든 청크가 한도 이하다

#### Scenario: 나뉜 청크를 이으면 원래 내용이 모두 남아 있다

- **WHEN** 한 턴을 분할한다
- **THEN** 청크들에서 이어붙인 내용이 분할 전 텍스트의 모든 이벤트 본문을 빠짐없이
  포함한다 (이어지는 청크의 프롬프트 앞부분 반복은 제외)

### Requirement: 필터용 메타데이터

Each chunk SHALL record its session, turn number, part number, time span, the
set of tool names used, and the number of failed tool calls, so that retrieval
can filter on them. Time SHALL NOT appear in the chunk text.

#### Scenario: 청크가 필터에 쓸 값을 가진다

- **WHEN** 도구를 쓴 턴의 청크를 조회한다
- **THEN** 세션, 턴 번호, 부분 번호, 시작·끝 시각, 사용한 도구 이름 목록, 실패한 도구
  호출 수가 기록되어 있다

#### Scenario: 시각은 청크 텍스트에 들어가지 않는다

- **WHEN** 타임스탬프가 있는 이벤트로 청크를 만든다
- **THEN** 청크 텍스트에 날짜나 시각 값이 들어 있지 않다

### Requirement: 바뀐 턴만 다시 만들기

Chunks SHALL be rebuilt only for turns whose fingerprint or chunking rules
changed since they were built, and a turn whose indexed content did not change
SHALL keep its chunks untouched.

#### Scenario: 바뀌지 않은 턴의 청크는 그대로다

- **WHEN** 청크를 만든 뒤 아무 것도 바뀌지 않은 저장소에서 다시 청크를 갱신한다
- **THEN** 어떤 청크 행도 다시 쓰이지 않는다

#### Scenario: 새 턴만 청크가 추가된다

- **WHEN** 세션에 새 프롬프트가 추가되어 다시 적재한 뒤 청크를 갱신한다
- **THEN** 기존 턴의 청크는 그대로이고 새 턴의 청크가 추가된다

#### Scenario: 도구 결과만 바뀐 턴은 청크 텍스트가 같다

- **WHEN** 이전 턴의 도구 호출에 결과가 붙어 그 턴의 지문이 바뀐 뒤 청크를 갱신한다
- **THEN** 그 턴의 청크가 다시 만들어지지만 청크 텍스트와 텍스트 해시는 이전과 같다

#### Scenario: 지문이 아직 비어 있는 턴도 청크가 만들어진다

- **WHEN** 지문 도입 이전에 색인되어 지문이 비어 있는 턴이 있다
- **THEN** 그 턴의 청크도 만들어지고, 지문이 채워진 뒤 갱신하면 같은 텍스트가 나온다

#### Scenario: 청크 규칙이 바뀌면 모든 청크가 다시 만들어진다

- **WHEN** 청크 길이 한도나 텍스트 형식 같은 청크 규칙이 이전에 청크를 만들 때와 다르다
- **THEN** 턴의 지문이 같아도 모든 청크가 새 규칙으로 다시 만들어진다

#### Scenario: 사라진 턴의 청크는 지워진다

- **WHEN** 색인에서 어떤 턴이 더 이상 존재하지 않는다
- **THEN** 그 턴의 청크가 지워진다

### Requirement: 청크 텍스트 해시와 재생성

Each chunk SHALL carry a deterministic hash of its text, and the chunks SHALL be
fully reconstructible from the index alone.

#### Scenario: 같은 텍스트는 같은 해시를 낸다

- **WHEN** 같은 저장 내용에서 청크를 두 번 만든다
- **THEN** 모든 청크의 텍스트와 텍스트 해시가 같다

#### Scenario: 재색인 뒤에도 같은 청크가 나온다

- **WHEN** 청크를 만든 뒤 색인을 비우고 재색인한 다음 청크를 갱신한다
- **THEN** 청크의 텍스트와 텍스트 해시가 재색인 전과 같다
