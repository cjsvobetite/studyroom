"""예약 규칙·검증 등 순수 로직 (DB, Streamlit 의존성 없음 → 단위 테스트 대상)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .config import DEFAULT_SETTINGS, PASSWORD_MIN_LENGTH, SLOTS_PER_DAY
from .timeutil import parse_iso, slot_start


def _int_setting(settings: dict[str, str], key: str, low: int, high: int) -> int:
    try:
        value = int(str(settings.get(key, DEFAULT_SETTINGS[key])).strip())
    except ValueError:
        value = int(DEFAULT_SETTINGS[key])
    return max(low, min(high, value))


@dataclass(frozen=True)
class Rules:
    booking_window_days: int = 7
    daily_limit_slots: int = 4
    open_slot: int = 0
    close_slot: int = SLOTS_PER_DAY

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> Rules:
        open_slot = _int_setting(settings, "open_slot", 0, SLOTS_PER_DAY - 1)
        close_slot = _int_setting(settings, "close_slot", 1, SLOTS_PER_DAY)
        if close_slot <= open_slot:
            open_slot, close_slot = 0, SLOTS_PER_DAY
        return cls(
            booking_window_days=_int_setting(settings, "booking_window_days", 1, 30),
            daily_limit_slots=_int_setting(settings, "daily_limit_slots", 1, SLOTS_PER_DAY),
            open_slot=open_slot,
            close_slot=close_slot,
        )

    @property
    def slots(self) -> range:
        return range(self.open_slot, self.close_slot)


def occupied_slots(reservations: list[dict]) -> set[int]:
    taken: set[int] = set()
    for row in reservations:
        taken.update(range(row["start_slot"], row["end_slot"]))
    return taken


def valid_units(
    start: int,
    rules: Rules,
    occupied: set[int],
    remaining: int,
    own_in_room: list[tuple[int, int]],
) -> list[int]:
    """start 에서 시작할 수 있는 이용 칸 수 목록. (DB의 book_room 규칙과 동일)"""
    units: list[int] = []
    for unit in range(1, remaining + 1):
        end = start + unit
        if end > rules.close_slot or (end - 1) in occupied:
            break
        if own_in_room and not any(e == start or s == end for s, e in own_in_room):
            continue
        units.append(unit)
    return units


def available_starts(
    day: date,
    now: datetime,
    rules: Rules,
    occupied: set[int],
    remaining: int,
    own_in_room: list[tuple[int, int]],
) -> list[int]:
    if remaining <= 0:
        return []
    return [
        slot
        for slot in rules.slots
        if slot not in occupied
        and slot_start(day, slot) > now
        and valid_units(slot, rules, occupied, remaining, own_in_room)
    ]


def reservation_phase(row: dict, now: datetime) -> str:
    """'upcoming' | 'ongoing' | 'past'"""
    day = date.fromisoformat(row["res_date"]) if isinstance(row["res_date"], str) else row["res_date"]
    if slot_start(day, row["end_slot"]) <= now:
        return "past"
    if slot_start(day, row["start_slot"]) <= now:
        return "ongoing"
    return "upcoming"


def account_block_reason(user: dict, now: datetime) -> str | None:
    if not user.get("is_active", False):
        return "승인되지 않았거나 비활성화된 계정입니다. 학생회에 문의하세요."
    suspended = parse_iso(user.get("suspend_until"))
    if suspended and now < suspended:
        return f"{suspended:%Y-%m-%d %H:%M}까지 이용이 정지된 계정입니다."
    return None


def validate_new_password(new: str, confirm: str, student_id: str) -> str | None:
    if len(new) < PASSWORD_MIN_LENGTH:
        return f"새 비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
    if new == student_id:
        return "학번(아이디)과 같은 비밀번호는 사용할 수 없습니다."
    if new != confirm:
        return "새 비밀번호 확인이 일치하지 않습니다."
    return None


def sanitize_search(term: str) -> str:
    """PostgREST 필터 문법에 영향을 주는 문자를 제거한다."""
    return re.sub(r"[^\w-]", "", term.strip())[:40]


ROSTER_COLUMNS = {
    "student_id": ("학번", "student_id", "id"),
    "password": ("비밀번호", "비번", "password", "pw"),
    "name": ("이름", "name"),
}


@dataclass(frozen=True)
class RosterEntry:
    student_id: str
    password: str
    name: str


def parse_roster(records: list[dict], columns: list[str]) -> list[RosterEntry]:
    """엑셀 행(dict 목록)을 검증된 명단으로 바꾼다. 학번이 중복되면 마지막 행을 쓴다."""
    normalized = {str(c).strip().lower(): c for c in columns}

    def find(field: str):
        return next((normalized[a.lower()] for a in ROSTER_COLUMNS[field] if a.lower() in normalized), None)

    id_col, pw_col, name_col = find("student_id"), find("password"), find("name")
    if not id_col or not pw_col:
        raise ValueError("엑셀에 '학번'과 '비밀번호' 열이 필요합니다.")

    entries: dict[str, RosterEntry] = {}
    for row in records:
        sid = _cell(row.get(id_col))
        password = _cell(row.get(pw_col))
        if not sid or not password:
            continue
        entries[sid] = RosterEntry(sid, password, _cell(row.get(name_col)) if name_col else "")
    return list(entries.values())


def _cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text
