---
name: prompt-log
description: Claude Code 세션 기록을 사람이 읽는 리포트로 내보낸다. 사용자가 "프롬프트 로그", "세션 리포트", "이번 세션 뭐 했는지 정리", "작업 기록 뽑아줘", "prompt log", "세션 로그 백필"이라고 하거나 과거 세션에서 무엇을 했는지 되짚어야 할 때 사용한다.
---

# prompt-log

Claude Code는 이미 모든 세션을 `~/.claude/projects/<프로젝트>/<세션ID>.jsonl`에 남긴다 —
프롬프트 전문, 도구 호출 인자, 도구 결과, 토큰, 권한 거부까지. 문제는 사람이 읽을 수 없다는
것뿐이다. 이 스킬은 그 기록을 읽어 리포트로 뽑는다. **새로 로그를 쌓지 않는다.**

출력은 `~/claude-prompt-logs/<프로젝트>/<날짜>/<HH-MM>_<세션8자>/<프로젝트>-<날짜>-<세션8자>.{md,jsonl}`:

- **`.md`** — 세션 헤더(기간·모델·토큰·건수), 도구 사용 표, 수정된 파일 목록,
  프롬프트 타임라인(프롬프트 전문 + 각 도구가 무엇을 했는지 한 줄씩)
- **`.jsonl`** — 재현·오류추적용 이벤트 스트림. 도구 결과를 요약하지 않고 4KB에서 절단만 한다

`Stop` 훅이 매 턴 현재 세션 리포트를 자동 갱신한다. 훅은 `hooks/hooks.json`에 있고,
마켓플레이스로 설치된 플러그인에서 자동으로 로드된다. `plugin.json`에 `hooks` 필드를
넣으면 안 된다 — 그 필드가 있으면 로드에 실패한다(ce126c6에서 제거).

훅이 도는지 의심되면 리포트 파일의 mtime을 보면 된다. 갱신이 멈춰 있으면 **설치된 캐시가
옛 커밋인지부터 확인한다** — 캐시는 `claude plugin update`를 해야 갱신된다.

```bash
grep hooks ~/.claude/plugins/cache/prompt-log/prompt-log/*/.claude-plugin/plugin.json
# 출력이 있으면 옛 캐시다:
claude plugin update prompt-log@prompt-log
```

## 수동 실행

과거 세션을 뽑거나 결과를 확인할 때:

`EX=~/.claude/plugins/cache/prompt-log/prompt-log/*/scripts/export.py` (설치본) 또는
저장소의 `scripts/export.py`를 쓴다.

```bash
# 전체 백필
python3 $EX --all

# 특정 세션
python3 $EX <경로>/<세션ID>.jsonl

# 로직 검증
python3 $EX --selftest
```

## 손대기 전에 알아야 할 것

`scripts/export.py`를 수정한다면 아래는 실측으로 확인된 제약이고, 어기면 조용히 깨진다.
전부 `--selftest`가 지킨다.

- **JSONL은 시간순이 아니다** (compact/resume 때문). `timestamp` 정렬이 필수다.
- **첨부가 붙은 프롬프트는 `content`가 배열이다.** "문자열이면 사람 프롬프트"로 거르면
  이미지 붙인 프롬프트가 통째로 누락된다. `tool_result` 블록 유무로 구분한다.
- **도구 호출↔결과를 순서로 짝지으면 4% 어긋난다.** `tool_use_id`로 맞춘다.
- **경로에 박힌 UUID는 지우지 않는다.** 지우면 재현이 불가능해진다.
  구조적 UUID 필드(`uuid`, `parentUuid`, `requestId` 등)만 출력에서 뺀다.
- **슬러그는 첫 cwd로 고정한다.** 마지막 cwd로 두면 훅이 매 턴 도는 동안 같은 세션
  리포트가 여러 폴더로 흩어진다 (52세션 중 10개가 세션 도중 cwd가 바뀐다).
- **슬래시 커맨드를 버리면 안 된다.** 그 뒤 도구 호출이 직전 프롬프트에 잘못 붙는다
  (실측 41건). 이름만 남겨 귀속 경계를 만든다.
- **기록은 UTC다.** 그대로 찍으면 09:42가 00:42로 보인다.
- **resume는 조상 세션 전체를 새 파일로 복사한다.** 레코드의 `sessionId`가 파일명과 다르면
  버린다. 안 버리면 같은 대화가 세션 수만큼 중복 리포트로 나온다 (실측 116묶음 중 26개가
  중복 사본, 최대 4벌). 복사본만 있고 원본 파일이 없는 세션은 0개라 버려도 잃는 게 없다.
