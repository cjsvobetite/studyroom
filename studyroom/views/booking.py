"""'예약' 탭: 날짜 선택 → 방×시간 현황표 → 가능한 시간만 골라 예약."""

from __future__ import annotations

import logging
from datetime import timedelta

import streamlit as st

from .. import db
from ..rules import Rules, available_starts, occupied_slots, valid_units
from ..timeutil import date_label, duration_str, now_kst, range_to_str, slot_to_str
from ..ui import flash, schedule_grid, show_flash

logger = logging.getLogger("studyroom")


def render(user: dict) -> None:
    st.subheader("새 예약")
    show_flash("booking")

    settings = db.get_settings()
    if settings.get("global_lock") == "1":
        st.warning("현재 관리자가 전체 예약을 잠갔습니다.")
        return
    rooms = db.active_rooms()
    if not rooms:
        st.info("현재 예약 가능한 스터디룸이 없습니다.")
        return

    rules = Rules.from_settings(settings)
    now = now_kst()
    today = now.date()
    dates = [today + timedelta(days=i) for i in range(rules.booking_window_days)]
    day = st.pills(
        "날짜", dates, default=today, format_func=lambda d: date_label(d, today), key="booking_date"
    ) or today

    day_rows = db.reservations_on(day)
    me = user["student_id"]
    used = sum(r["end_slot"] - r["start_slot"] for r in day_rows if r["student_id"] == me)
    remaining = rules.daily_limit_slots - used

    st.markdown(f"**{date_label(day, today)} 예약 현황**")
    schedule_grid(rooms, day_rows, rules, day, now, me)
    st.caption(
        f"이 날짜에 남은 예약 가능 시간: **{duration_str(max(remaining, 0))}** "
        f"(하루 최대 {duration_str(rules.daily_limit_slots)})"
    )

    if remaining <= 0:
        st.info("이 날짜의 예약 한도를 모두 사용했습니다. 다른 날짜를 선택하세요.")
        return

    room_by_name = {room["name"]: room for room in rooms}
    room = room_by_name[st.selectbox("스터디룸", list(room_by_name), key="booking_room")]

    room_rows = [r for r in day_rows if r["room_id"] == room["id"]]
    own_in_room = [(r["start_slot"], r["end_slot"]) for r in room_rows if r["student_id"] == me]
    occupied = occupied_slots(room_rows)
    starts = available_starts(day, now, rules, occupied, remaining, own_in_room)

    if own_in_room:
        st.caption("같은 방에 추가로 예약하려면 기존 예약과 이어지는 시간만 선택할 수 있습니다.")
    if not starts:
        st.info("선택한 방·날짜에 예약 가능한 시간이 없습니다.")
        return

    col1, col2 = st.columns(2)
    with col1:
        start = st.selectbox(
            "시작 시간", starts, format_func=slot_to_str, key=f"booking_start_{room['id']}_{day}"
        )
    with col2:
        units = st.selectbox(
            "이용 시간",
            valid_units(start, rules, occupied, remaining, own_in_room),
            format_func=duration_str,
            key=f"booking_units_{room['id']}_{day}_{start}",
        )

    summary = f"{room['name']} · {date_label(day, today)} · {range_to_str(start, start + units)}"
    st.info(f"선택한 예약: **{summary}** ({duration_str(units)})")
    if st.button("예약하기", type="primary", width="stretch"):
        try:
            result = db.book_room(me, room["id"], day, start, units)
        except Exception:
            logger.exception("book_room RPC failed")
            st.error("예약 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하세요.")
            return
        if result.get("ok"):
            flash("booking", f"{result.get('message', '예약이 완료되었습니다.')} ({summary})")
            st.rerun()
        else:
            st.error(result.get("message", "예약할 수 없습니다."))
