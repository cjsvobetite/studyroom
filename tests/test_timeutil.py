from datetime import date, datetime

from studyroom.config import KST
from studyroom.timeutil import date_label, duration_str, parse_iso, range_to_str, slot_start, slot_to_str


def test_slot_to_str():
    assert slot_to_str(0) == "00:00"
    assert slot_to_str(19) == "09:30"
    assert slot_to_str(48) == "24:00"


def test_range_and_duration():
    assert range_to_str(18, 20) == "09:00 ~ 10:00"
    assert duration_str(1) == "30분"
    assert duration_str(2) == "1시간"
    assert duration_str(3) == "1시간 30분"


def test_slot_start_is_kst():
    assert slot_start(date(2026, 10, 2), 3) == datetime(2026, 10, 2, 1, 30, tzinfo=KST)


def test_parse_iso():
    assert parse_iso(None) is None
    assert parse_iso("2026-10-02T00:00:00Z") == datetime(2026, 10, 2, 9, 0, tzinfo=KST)
    assert parse_iso("2026-10-02T09:00:00") == datetime(2026, 10, 2, 9, 0, tzinfo=KST)


def test_date_label():
    today = date(2026, 10, 2)  # 금요일
    assert date_label(today, today) == "오늘 10/2(금)"
    assert date_label(date(2026, 10, 3), today) == "내일 10/3(토)"
    assert date_label(date(2026, 10, 5), today) == "10/5(월)"
