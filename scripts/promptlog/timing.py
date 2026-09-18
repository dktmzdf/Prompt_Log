"""Local time and activity duration helpers."""

from datetime import datetime

IDLE_GAP = 30 * 60


def local_dt(iso):
    """기록은 UTC다. 그대로 찍으면 09:42가 00:42로 보인다. 로컬로 옮긴다."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def hhmmss(iso):
    dt = local_dt(iso)
    return dt.strftime("%H:%M:%S") if dt else (iso[11:19] if iso else "")


def fmt_dur(seconds):
    mins = int(seconds // 60)
    return f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"


def fmt_span(times):
    """시작·끝(로컬 시각)과 달력 간격, 그리고 실제 활동 시간.

    resume한 세션은 달력 간격이 "173시간 44분"으로 나온다. 그건 작업 시간이 아니라
    첫 기록과 마지막 기록 사이일 뿐이다. 30분 이상 빈 구간을 뺀 값을 함께 낸다.
    """
    stamps = sorted(d for d in (local_dt(t) for t in times) if d)
    if not stamps:
        return "", "", "", ""
    first, last = stamps[0], stamps[-1]
    active = 0.0
    for prev, cur in zip(stamps, stamps[1:]):
        gap = (cur - prev).total_seconds()
        if gap < IDLE_GAP:
            active += gap
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        first.strftime(fmt),
        last.strftime(f"{fmt} %Z"),
        fmt_dur((last - first).total_seconds()),
        fmt_dur(active),
    )
