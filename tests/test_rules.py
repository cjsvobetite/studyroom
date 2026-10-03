from datetime import date, datetime, timedelta

import pytest

from studyroom.config import KST
from studyroom.rules import (
    Rules,
    account_block_reason,
    available_starts,
    occupied_slots,
    parse_roster,
    reservation_phase,
    sanitize_search,
    valid_units,
    visible_slots,
    validate_new_password,
)

DAY = date(2026, 10, 2)
MORNING = datetime(2026, 10, 2, 8, 10, tzinfo=KST)  # 08:10 → 08:30(slot 17)부터 예약 가능


def test_rules_from_settings_clamps_and_defaults():
    rules = Rules.from_settings({"booking_window_days": "99", "daily_limit_slots": "abc"})
    assert rules.booking_window_days == 56
    assert Rules.from_settings({"booking_window_days": "28"}).booking_window_days == 28
    assert rules.daily_limit_slots == 4
    assert Rules.from_settings({"open_slot": "20", "close_slot": "10"}).slots == range(0, 48)


def test_occupied_slots():
    assert occupied_slots([{"start_slot": 2, "end_slot": 4}, {"start_slot": 10, "end_slot": 11}]) == {2, 3, 10}


def test_valid_units_stops_at_booked_slot_and_close():
    rules = Rules(close_slot=40)
    assert valid_units(20, rules, {22}, 4, []) == [1, 2]
    assert valid_units(38, rules, set(), 4, []) == [1, 2]


def test_valid_units_requires_adjacency_in_same_room():
    rules = Rules()
    own = [(20, 22)]
    assert valid_units(22, rules, {20, 21}, 2, own) == [1, 2]
    assert valid_units(17, rules, {20, 21}, 3, own) == [3]
    assert valid_units(30, rules, {20, 21}, 2, own) == []


def test_available_starts_skips_past_and_booked():
    rules = Rules(open_slot=0, close_slot=24)
    starts = available_starts(DAY, MORNING, rules, {18, 19}, 4, [])
    assert starts == [17, 20, 21, 22, 23]


def test_available_starts_empty_when_no_quota():
    assert available_starts(DAY, MORNING, Rules(), set(), 0, []) == []


def test_reservation_phase():
    row = {"res_date": "2026-10-02", "start_slot": 16, "end_slot": 18}  # 08:00~09:00
    assert reservation_phase(row, MORNING) == "ongoing"
    assert reservation_phase(row, MORNING - timedelta(hours=1)) == "upcoming"
    assert reservation_phase(row, MORNING + timedelta(hours=1)) == "past"


def test_account_block_reason():
    now = MORNING
    assert account_block_reason({"is_active": True}, now) is None
    assert "비활성" in account_block_reason({"is_active": False}, now)
    future = (now + timedelta(days=1)).isoformat()
    assert "정지" in account_block_reason({"is_active": True, "suspend_until": future}, now)
    past = (now - timedelta(days=1)).isoformat()
    assert account_block_reason({"is_active": True, "suspend_until": past}, now) is None


@pytest.mark.parametrize(
    ("new", "confirm", "ok"),
    [
        ("short", "short", False),
        ("20261234", "20261234", False),
        ("goodpass1", "goodpass2", False),
        ("goodpass1", "goodpass1", True),
    ],
)
def test_validate_new_password(new, confirm, ok):
    assert (validate_new_password(new, confirm, "20261234") is None) is ok


def test_sanitize_search_strips_filter_syntax():
    assert sanitize_search(" 2026,name.eq.x)(*% ") == "2026nameeqx"
    assert sanitize_search("홍길동") == "홍길동"


def test_parse_roster():
    records = [
        {" 학번 ": "2026001", "비밀번호": "pw1", "이름": "가"},
        {" 학번 ": "", "비밀번호": "pw2", "이름": "나"},
        {" 학번 ": "2026003", "비밀번호": "nan", "이름": "다"},
        {" 학번 ": "2026001", "비밀번호": "pw9", "이름": None},
    ]
    entries = parse_roster(records, [" 학번 ", "비밀번호", "이름"])
    assert [(e.student_id, e.password, e.name) for e in entries] == [("2026001", "pw9", "")]


def test_parse_roster_requires_columns():
    with pytest.raises(ValueError):
        parse_roster([], ["이름"])


def test_visible_slots_hides_past_hours_today_only():
    evening = datetime(2026, 10, 2, 17, 40, tzinfo=KST)
    assert visible_slots(Rules(), DAY, evening) == range(34, 48)
    assert visible_slots(Rules(), DAY + timedelta(days=1), evening) == range(0, 48)
    assert visible_slots(Rules(open_slot=36, close_slot=44), DAY, evening) == range(36, 44)
    assert not visible_slots(Rules(close_slot=30), DAY, evening)
