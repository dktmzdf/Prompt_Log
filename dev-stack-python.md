# Prompt Log Python 개발 가이드

대상: Claude Code·Codex JSONL을 Markdown·JSONL로 내보내는 현재 저장소.
기본 규칙과 전체 검사 명령은 [AGENTS.md](AGENTS.md)를 따른다.
이 문서는 규칙의 적용 방법과 코드 예제를 설명하는 개발 참고서다.

## 1. 스택과 개발 도구

### 현재 구성

| 영역 | 현재 선택 | 유지 이유 |
|---|---|---|
| CLI | `argparse`, `sys` | 수동 실행과 훅 진입점을 지원함 |
| 입력 | `json`, `pathlib` | JSONL 파일을 읽고 에이전트 형식을 판별함 |
| 공통 모델 | `dataclasses`, `typing` | 에이전트별 입력을 같은 이벤트 계약으로 변환함 |
| 저장 | `tempfile`, `os`, `hashlib` | 파일별 교체와 세션별 경로 구성을 담당함 |
| 테스트 | `unittest`, `unittest.mock`, `subprocess` | 내부 동작과 실제 종료 코드를 함께 확인함 |
| 패키징 | 현재 스크립트·플러그인 디렉터리 구성 | 별도 빌드 없이 진입점을 실행함 |

현재 저장소에는 `pyproject.toml`이나 의존성 잠금 파일이 없다.
예제의 Python 3.12/3.13, pytest, pydantic, uv를 필수 구성으로 가져오지 않는다.
개발용 환경을 격리할 필요가 생기면 표준 `venv`를 사용할 수 있다.

외부 라이브러리를 쓸 수 있고 Python 3.11 이상 요구사항만 있다. 위 표는 리포트 내보내기 경로의 현재 구성이며, 검색·질의 기능은
[RAG 명세](rag-spec.md)를 따른다. `pyproject.toml`은 첫 의존성을 추가할 때 만든다.

### 자동 검사 도구를 추가할 때의 두 선택지

| 선택지 | 장점 | 비용·한계 |
|---|---|---|
| 현재 `unittest`와 코드 리뷰 유지 | 설치 부담이 없고 기존 명령을 그대로 사용함 | 스타일·타입 위반은 리뷰에 의존함 |
| 개발용 린터·타입 검사기 추가 | 반복되는 스타일·타입 검사를 자동화할 수 있음 | 도구 버전·설정·기존 위반 처리 필요 |

같은 실수가 반복되면 Ruff 또는 기존 Black 활용, mypy 또는 pyright 도입을 별도 변경으로
비교한다. 도구를 도입한다면 루트 지침의 지원 버전과 스타일에 맞추고 검사부터 수행한다.
자동 수정이나 전체 `strict` 전환은 기존 오류와 호환성 영향을 확인한 뒤 범위를 정한다.
원문의 도구 설정 예시는 현재 저장소에서 검증된 설정이 아니므로 복사하지 않았다.

## 2. 구조와 의존 방향

모듈별 책임과 데이터 흐름은 [설계 문서](docs/refactoring.md)의 `변경된 구조`를 참고한다.
실제 연결은 [service.py](scripts/promptlog/service.py)를 기준으로 확인한다.
새 기능도 이 경계를 유지한다. 예를 들어 파서가 CLI를 호출하거나,
렌더러가 사용자 홈의 로그를 직접 찾게 만들지 않는다.

`scripts/export.py`의 일부 import는 기존 호출부에 함수를 재노출하는 호환 코드다.
사용하지 않는 것처럼 보인다는 이유만으로 제거하지 않는다.
`src/` 배치로 바꾸려면 진입점·복사 설치·테스트 import의 영향을 함께 검증해야 한다.

## 3. 함수·이름·주석

들여쓰기, 이름 규칙, 길이 제한은 [루트 지침](AGENTS.md)의 3번 항목을 따른다.
새 함수는 한 가지 처리 단계가 드러나도록 이름을 짓는다.
예를 들어 파일 읽기, JSON 해석, 이벤트 집계, 파일 저장을 한 함수에 새로 합치지 않는다.

- 길이 제한을 맞추려고 여러 문장을 한 줄로 합치지 않는다.
- 분리한 함수의 이름이 책임을 설명하고 입력·출력 관계가 명확해야 한다.
- 함수 밖에서 변경되는 상태를 많이 참조하는 중첩 함수는 분리 비용을 함께 검토한다.
- 단순 변환은 컴프리헨션, 오류 처리·상태 변경이 있는 흐름은 명시적인 반복문을 쓴다.
- 주석에는 코드로 알기 어려운 이유, 원본 형식의 제약, 호환성 조건을 적는다.
- docstring에는 반환 의미, 입력 변경 여부, 발생 가능한 중요한 예외를 짧게 적는다.

예: `truncate()`는 문자열을 요약하지 않고 일부를 남기는 함수다.
이 차이는 오류 원문 추적이라는 목적과 연결되므로 주석으로 설명할 가치가 있다.

## 4. 타입과 데이터 모델

### 원본 JSON과 공통 이벤트를 구분한다

원본 JSON은 누락 필드, 예상과 다른 타입, 새 이벤트 종류를 포함할 수 있다.
어댑터에서 자료형을 확인하고 필요한 값을 추출한 뒤 공통 `Event`를 만든다.
타입 주석을 붙였다는 이유로 외부 입력이 검증됐다고 간주하지 않는다.
Python 런타임은 타입 주석을 자동으로 강제하지 않는다.
[Python 3.10 typing 문서](https://docs.python.org/3.10/library/typing.html)를 기준으로 한다.

- 모듈 간에 전달하는 안정된 데이터는 기존 데이터 클래스를 우선 활용한다.
- 원본 레코드나 가변 구조를 표현하는 `dict`까지 모두 클래스로 바꾸지는 않는다.
- `Event.input: Any`는 여러 종류의 도구 입력을 보존하기 위한 현재 계약이다.
  이를 임의로 `dict[str, str]`로 좁히면 배열·중첩 객체 등을 표현하지 못한다.
- 새로운 경계 함수에는 가능한 입력·반환 타입을 명시하되, 기존 코드를 일괄 변경하지 않는다.
- 타입을 맞추기 위해 본문을 문자열로 강제 변환하거나 정보를 버리지 않는다.

### 가변 기본값과 객체 소유권

함수 기본값의 빈 리스트·딕셔너리를 호출 간 공유하지 않는다.
데이터 클래스의 가변 필드는 `field(default_factory=...)`를 사용한다.
이 팩터리는 각 객체에 필요한 기본값을 만든다.
[Python 3.10 dataclasses 문서](https://docs.python.org/3.10/library/dataclasses.html) 참고.

현재 모델이 서로 독립적인 기본값을 갖는지 확인하는 최소 예제다.
아래 Python 예제들은 저장소 루트에서 각각 독립적으로 실행할 수 있다.

```python
from scripts.promptlog.models import Event, Session, SessionLog

first = SessionLog(Session("codex", "sample-a"))
second = SessionLog(Session("codex", "sample-b"))
first.events.append(Event("prompt", text="hello"))

assert len(first.events) == 1
assert second.events == []
```

원문의 `frozen=True`를 현재 `Event`에 일괄 추가하지 않는다.
예를 들어 Codex legacy 파서는 나중에 도착한 도구 결과를 기존 이벤트의 `output`에 연결한다.
불변 모델로 바꾸려면 해당 연결 방식과 관련 테스트를 함께 설계해야 한다.

## 5. 예외 처리와 실행 경계

예외를 잡을 위치는 어떤 복구나 보고를 할 수 있는지에 따라 정한다.

| 위치 | 현재 처리 또는 보완 기준 | 이유 |
|---|---|---|
| 내부 변환 함수 | 처리 가능한 구체 예외만 잡거나 호출자에게 전달 | 구현 결함을 정상 결과로 숨기지 않음 |
| JSONL 읽기 | 현재는 파싱 실패 줄·객체가 아닌 값을 건너뜀 | 기존 입력 허용 정책 유지 |
| CLI·훅 실행 경계 | 루트 지침 6번의 실패 정책으로 변환 | 호출자가 실행 결과를 구분할 수 있게 함 |

[cli.py](scripts/promptlog/cli.py)의 `except Exception`은 실행 경계에서
실패를 모드별로 보고하기 위한 것이다. 내부 함수에 같은 패턴을 무조건 확산하지 않는다.
아무 타입도 지정하지 않는 `except:`와 동일하게 취급해 일괄 삭제하지 않는다.

새 변환 함수에서 오류 의미를 바꿔 전달할 때는 원인 예외를 보존한다.
다음은 엄격한 JSON 객체 입력 검증을 설명하는 독립 예제다.
현재 `read_jsonl()`의 허용 정책을 대체하는 구현은 아니다.

```python
import json


def parse_object(text: str) -> dict[str, object]:
    """JSON 객체를 반환한다. 파싱 실패나 비객체 입력이면 ValueError."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("유효한 JSON이 필요함") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON 객체가 필요함")
    return value


assert parse_object('{"kind": "prompt"}') == {"kind": "prompt"}
for text in ("{bad", "[]"):
    try:
        parse_object(text)
    except ValueError as exc:
        assert (exc.__cause__ is not None) == (text == "{bad")
    else:
        raise AssertionError("잘못된 입력을 허용함")
```

현재 리더는 마지막 미완성 줄만 골라 건너뛰는 것이 아니다.
파일 중간의 파싱 불가 줄도 건너뛴다. 이를 변경한다면 호환성 변경으로 다뤄야 한다.
오류 보고에 개인 대화 전체나 도구 인자를 그대로 덤프하지 않는다.

## 6. 파일·경로·문자열 처리

### 입력과 리소스

- 파일 핸들은 `with`로 수명을 관리한다.
- 현재 JSONL 리더의 `utf-8-sig`는 BOM이 있는 UTF-8 입력도 처리하기 위한 선택이다.
- 리포트는 현재 저장 코드처럼 UTF-8과 명시적인 줄바꿈 정책을 사용한다.
- 파일시스템 접근에는 `Path`를 사용한다. 로그 속 경로는 다른 OS에서 온 문자열일 수 있다.
  현재 OS의 경로 해석만으로 Windows·POSIX 원본 경로를 모두 처리한다고 가정하지 않는다.

### 저장과 재실행

[storage.py](scripts/promptlog/storage.py)는 대상 폴더에 임시 파일을 완성하고,
닫은 뒤 `os.replace()`로 결과 파일을 교체한다. 실패하면 임시 파일 정리를 시도한다.
예제의 짧은 `write_text()` 호출로 대체하기 전에 실패 시 기존 파일 보존 조건을 확인한다.

- 프로젝트 이름에는 점이나 Windows 예약 이름이 들어올 수 있다. `safe_slug()`를 활용한다.
- 점이 있는 기본 이름에 `with_suffix()`를 쓰면 의도한 이름 일부를 바꿀 수 있다.
  현재 구현이 확장자를 덧붙이는 이유를 유지한다.
- 원자적 교체 단위는 파일 하나다. Markdown·JSONL 두 파일 전체의 동시 성공은 보장하지 않는다.
- 재실행 경로의 안정성과 동시 실행 잠금은 별도 문제다. 현재 잠금 구현은 없다.

### 출력 절단과 복사

현재 `present()`는 집계 결과를 깊은 복사한 뒤 출력 길이 제한을 적용한다.
원본 이벤트를 수정하지 않는 성질을 유지해야 한다.
UTF-8 바이트 한도는 문자 개수와 다르므로 한글·이모지 경계도 검증한다.
정확한 계약은 [README](README.md)와 [text.py](scripts/promptlog/text.py)를 따른다.

## 7. 상태와 의존성 전달

시간·출력 위치·파일 읽기 같은 외부 조건을 계산 로직에 숨기지 않는다.
값을 인자로 전달하면 테스트에서 다른 값을 넣어 같은 함수를 검증할 수 있다.
이 프로젝트의 `export(..., report_root=...)`가 출력 위치를 전달하는 예다.
테스트에서는 임시 디렉터리를 전달해 사용자 홈의 실제 리포트 위치를 사용하지 않게 한다.

- 모듈 전역에는 정책 상수나 고정된 매핑을 두고, 세션별 가변 상태는 호출 안에서 만든다.
- 테스트를 쉽게 하려고 객체 계층이나 의존성 주입 프레임워크를 새로 만들 필요는 없다.
- `ADAPTERS` 같은 기존 매핑은 프로덕션 요청 중 임의로 변경하지 않는다.
- 입력을 변경하는 함수는 변경 범위를 명시하고, 결과를 만드는 함수와 구분한다.
- 대용량 최적화는 측정 후 결정한다. 현재 읽기·집계·출력 복사는 메모리를 사용한다.
  스트리밍 전환은 정렬·도구 결과 연결·누적 토큰 계산에 미치는 영향을 함께 검토한다.

## 8. 테스트 작성과 검증 명령

### 무엇을 검증할지

원문의 테스트 가이드에서 정상·경계·오류 입력, 동작 중심 검증 원칙을 가져왔다.
현재 프로젝트는 작은 내부 유틸리티도 직접 테스트하므로 이를 일률적으로 금지하지 않는다.
출력 결과와 상태 변화가 목적이고, 내부 호출 횟수 검사는 필요한 제어 흐름에 한정한다.

| 변경 영역 | 확인할 동작 | 기존 테스트의 출발점 |
|---|---|---|
| 어댑터 | 새 입력 형식·결과 없는 도구·native/legacy 처리 | `tests/test_core.py`의 `AdapterTests` |
| 날짜·대화 | 자정 경계에서도 턴 보존·프롬프트 이전 이벤트 | 같은 파일의 `CommonCoreTests` |
| 토큰 | 누적값 반복·리셋·날짜 변경·개별 사용량 | 같은 파일의 `TokenTests` |
| 텍스트·읽기 | BOM·손상 줄·중첩 입력·한글 바이트 경계 | 같은 파일의 `TextAndReaderTests` |
| 저장 | 동일 경로 재실행·원본 보존·교체 실패 | `tests/test_cli_storage.py`의 `StorageTests` |
| 실행 경계 | CLI 실패·훅 오류·실제 자기검사 실패 | 같은 파일의 `CLITests` |

버그 수정은 가능한 경우 최소 재현 입력을 만들고 수정 전 실패 원인을 확인한다.
인수 조건과 확인한 근거는 [작업 양식](task-acceptance.md)에 기록한다.

다음은 중첩 도구 입력의 절단이 원본을 변경하지 않는지 확인하는 실행 예제다.
기존 테스트와 목적이 겹치므로 `tests/`에 새 테스트로 추가하지 않았다.

```python
import unittest

from scripts.promptlog.text import truncate_input


class PresentationExample(unittest.TestCase):
    def test_nested_text_preserves_source(self):
        text = "가" * 2000
        source = {"changes": [{"diff": text}]}
        shown, cuts = truncate_input(source)
        self.assertEqual(source["changes"][0]["diff"], text)
        self.assertNotEqual(shown["changes"][0]["diff"], text)
        self.assertIn("changes[0].diff", cuts)
        self.assertGreater(cuts["changes[0].diff"], 0)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(PresentationExample)
result = unittest.TextTestRunner().run(suite)
assert result.wasSuccessful()
```

표준 테스트 구성은 [Python 3.10 unittest 문서](https://docs.python.org/3.10/library/unittest.html)
기준이다. 전체 검사 명령은 루트 지침 2번을 따른다. 아래는 부분 검사와 공백 검사다.

```powershell
# 파일 입출력·CLI 관련 테스트만 먼저 확인
python -B -m unittest tests.test_cli_storage -v

# 추적 파일의 공백 오류 확인: 미추적 파일 검사는 별도로 필요
git diff --check
```

테스트 통과는 현재 검증 사례의 근거다. 모든 Python 버전·OS·원본 형식을 증명하지 않는다.

## 9. 외부 입력과 프로세스 실행

로그에 기록된 도구 명령은 내보낼 데이터다.

- 로그 본문과 도구 입력 문자열을 `eval` 또는 `exec`로 실행하지 않는다.
- 외부에서 받은 로그·캐시를 `pickle`로 역직렬화하지 않는다.
- 외부 입력을 셸 명령 문자열에 이어 붙여 실행하지 않는다.

프로세스 실행이 필요하면 `tests/test_cli_storage.py`처럼 Python 실행 파일과
각 인자를 목록으로 전달하고, 시간 제한과 종료 코드를 확인한다.
문자열을 셸에 넘기는 방식은 셸의 해석 규칙까지 고려해야 하므로 별도 판단이 필요하다.
인자 목록도 대상 프로그램 자체의 위험한 옵션까지 검증해 주는 것은 아니다.
[Python 3.10 subprocess 문서](https://docs.python.org/3.10/library/subprocess.html) 참고.

## 10. 현재 코드와 가이드 사이의 차이

이 문서의 작성이 전체 코드의 규칙 준수를 의미하지는 않는다.

- `models.py`에는 타입 주석이 있지만, `service.py` 등의 함수 시그니처에는 생략된 곳이 있다.
- `cli.main()`은 32줄, `render.render_md()`는 80줄로 30줄 규칙을 넘는다.
  AST의 함수 정의 시작부터 마지막 문장까지 세었으며 내부 공백·주석도 포함한 길이다.

이 차이를 해소할 때는 타입 보강, 함수 분리, 동작 변경을 작은 작업으로 나눈다.
원본 구조와 알려진 한계는 [리팩터링 문서](docs/refactoring.md)를 참고한다.

## 출처

예제 루트: `C:/Work/Example/example`.
주 출처: `template/language/dev-stack-python.md`의 스택·타입·예외·리소스·상태 관리 원칙과
`6.2 MUST NOT`의 외부 입력 처리 규칙. 현재 구조와 실행 정책에 맞춰 작성했다.
보조 출처: `template/testing.md`의 `What to Test`, `What NOT to Test`.
웹·Java 전용 설정과 추가 도구의 강제 설치는 포함하지 않았다.

## 검증 기록 (2026-09-15)

- Windows PowerShell·Python 3.11.9에서 기존 전체 자기검사 33개 통과, 종료 코드 0.
  기존 회귀검사 래퍼 하나가 포함한 내부 26개 항목도 실행됐다.
- 독립 Python 예제 3개 실행 통과. 정상·잘못된 JSON·기본값 독립성·원본 보존을 확인했다.
- 예제의 Python 3.10 문법 파싱과 함수 30줄 이하 검사 통과.
  문법 파싱은 Python 3.10 런타임에서 실제 실행한 결과와는 구분한다.
- 문서 상대 링크 존재, 한 줄 100자 이하, 줄 끝 공백 없음 확인.
- 실제 에이전트 훅, Python 3.10 런타임, 다른 OS, 개발 도구 설치는 이번 검증 범위 밖이다.
