"""'내 예약' 탭: 오늘 / 다가오는 예약 / 지난 예약."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from .. import db
from ..config import PAST_RESERVATION_DAYS
from ..rules import reservation_phase
from ..timeutil import date_label, now_kst, range_to_str
from ..ui import flash, show_flash


def describe(row: dict, today: date) -> str:
    room_name = (row.get("rooms") or {}).get("name", "스터디룸")
    day = date.fromisoformat(row["res_date"])
    return f"{room_name} · {date_label(day, today)} · {range_to_str(row['start_slot'], row['end_slot'])}"


@st.dialog("예약 취소")
def confirm_cancel(row: dict, actor: dict, scope: str) -> None:
    today = now_kst().date()
    st.write(f"**{describe(row, today)}**")
    st.write("이 예약을 취소할까요? 취소 후에는 되돌릴 수 없습니다.")
    col1, col2 = st.columns(2)
    if col1.button("예약 취소", type="primary", width="stretch"):
        ok, message = db.cancel_reservation(row["id"], actor["student_id"])
        flash(scope, message, "success" if ok else "error")
        st.rerun()
    if col2.button("닫기", width="stretch"):
        st.rerun()


def render(user: dict) -> None:
    st.subheader("내 예약")
    show_flash("my_reservations")

    now = now_kst()
    today = now.date()
    rows = db.user_reservations(user["student_id"], since=today - timedelta(days=PAST_RESERVATION_DAYS))
    groups: dict[str, list[dict]] = {"today": [], "upcoming": [], "past": []}
    for row in rows:
        phase = reservation_phase(row, now)
        row["_phase"] = phase
        if phase == "past":
            groups["past"].append(row)
        elif row["res_date"] == today.isoformat():
            groups["today"].append(row)
        else:
            groups["upcoming"].append(row)

    if not groups["today"] and not groups["upcoming"]:
        st.info("예정된 예약이 없습니다. '예약' 탭에서 새로 예약하세요.")

    for key, title in (("today", "오늘"), ("upcoming", "다가오는 예약")):
        if groups[key]:
            st.markdown(f"##### {title}")
            for row in groups[key]:
                reservation_card(row, user, today)

    if groups["past"]:
        with st.expander(f"지난 예약 (최근 {PAST_RESERVATION_DAYS}일 · {len(groups['past'])}건)"):
            for row in reversed(groups["past"]):
                st.write(describe(row, today))


def reservation_card(row: dict, user: dict, today: date) -> None:
    with st.container(border=True):
        col1, col2 = st.columns([4, 1], vertical_alignment="center")
        label = describe(row, today)
        if row["_phase"] == "ongoing":
            label += " · :green[**이용 중**]"
        col1.markdown(label)
        if row["_phase"] == "upcoming":
            if col2.button("취소", key=f"cancel_{row['id']}", width="stretch"):
                confirm_cancel(row, user, "my_reservations")
        else:
            col2.caption("취소 불가")
