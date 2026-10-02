"""시간·슬롯 변환 유틸리티 (Streamlit 의존성 없음)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .config import KST, SLOT_MINUTES

WEEKDAYS = "월화수목금토일"


def now_kst() -> datetime:
    return datetime.now(KST)


def slot_to_str(slot: int) -> str:
    total = slot * SLOT_MINUTES
    return f"{total // 60:02d}:{total % 60:02d}"


def range_to_str(start_slot: int, end_slot: int) -> str:
    return f"{slot_to_str(start_slot)} ~ {slot_to_str(end_slot)}"


def duration_str(slots: int) -> str:
    minutes = slots * SLOT_MINUTES
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}시간 {rest}분"
    if hours:
        return f"{hours}시간"
    return f"{rest}분"


def slot_start(day: date, slot: int) -> datetime:
    return datetime.combine(day, time.min, tzinfo=KST) + timedelta(minutes=slot * SLOT_MINUTES)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def date_label(day: date, today: date) -> str:
    base = f"{day.month}/{day.day}({WEEKDAYS[day.weekday()]})"
    if day == today:
        return f"오늘 {base}"
    if day == today + timedelta(days=1):
        return f"내일 {base}"
    return base
