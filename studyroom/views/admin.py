"""관리자 탭: 운영 / 스터디룸 / 사용자 / 예약."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from io import BytesIO

import pandas as pd
import streamlit as st
from werkzeug.security import generate_password_hash

from .. import db
from ..config import (
    BOOKING_WINDOW_CHOICES,
    PASSWORD_MIN_LENGTH,
    SLOTS_PER_DAY,
    SUSPEND_DAY_OPTIONS,
    USERS_PAGE_SIZE,
)
from ..rules import Rules, parse_roster, reservation_phase, sanitize_search
from ..timeutil import duration_str, now_kst, parse_iso, range_to_str, slot_to_str, window_label
from ..ui import flash, show_flash
from .mine import confirm_cancel

logger = logging.getLogger("studyroom")


def render(user: dict) -> None:
    st.subheader("관리자")
    overview, rooms, users, reservations = st.tabs(["운영", "스터디룸", "사용자", "예약"])
    with overview:
        admin_overview()
    with rooms:
        admin_rooms(user)
    with users:
        admin_users(user)
    with reservations:
        admin_reservations(user)


# ---------------------------------------------------------------- 운영

@st.dialog("전체 예약 잠금")
def confirm_lock(lock: bool) -> None:
    if lock:
        st.write("모든 사용자의 **새 예약이 즉시 차단**됩니다. 기존 예약은 유지됩니다.")
    else:
        st.write("전체 예약 잠금을 해제하면 사용자가 다시 예약할 수 있습니다.")
    col1, col2 = st.columns(2)
    if col1.button("잠그기" if lock else "잠금 해제", type="primary", width="stretch"):
        db.save_settings({"global_lock": "1" if lock else "0"})
        flash("admin_overview", "전체 예약을 잠갔습니다." if lock else "전체 예약 잠금을 해제했습니다.")
        st.rerun()
    if col2.button("닫기", width="stretch"):
        st.rerun()


def admin_overview() -> None:
    today = now_kst().date()
    today_count, upcoming_count = db.count_upcoming_reservations(today)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("사용자", db.count_users(), border=True)
    m2.metric("활성 스터디룸", len(db.active_rooms()), border=True)
    m3.metric("오늘 예약", today_count, border=True)
    m4.metric("예정 예약", upcoming_count, border=True)

    show_flash("admin_overview")
    settings = db.get_settings()
    locked = settings.get("global_lock") == "1"

    st.markdown("#### 예약 잠금")
    if locked:
        st.warning("현재 전체 예약이 잠겨 있습니다.")
    else:
        st.caption("현재 예약을 받고 있습니다.")
    if st.button("전체 예약 잠금 해제" if locked else "전체 예약 잠그기", type="secondary" if locked else "primary"):
        confirm_lock(not locked)

    st.markdown("#### 예약 규칙")
    rules = Rules.from_settings(settings)
    time_choices = list(range(SLOTS_PER_DAY + 1))
    with st.form("rules_form"):
        c1, c2 = st.columns(2)
        window_choices = sorted(set(BOOKING_WINDOW_CHOICES) | {rules.booking_window_days})
        window = c1.selectbox(
            "예약 가능 기간",
            window_choices,
            index=window_choices.index(rules.booking_window_days),
            format_func=window_label,
            help="오늘부터 며칠 뒤까지 미리 예약할 수 있는지 정합니다. 예: 2주 → 오늘 포함 14일",
        )
        limit = c2.selectbox(
            "1인 하루 최대 이용 시간",
            list(range(1, 17)),
            index=min(rules.daily_limit_slots, 16) - 1,
            format_func=duration_str,
        )
        c3, c4 = st.columns(2)
        open_slot = c3.selectbox("운영 시작", time_choices[:-1], index=rules.open_slot, format_func=slot_to_str)
        close_slot = c4.selectbox(
            "운영 종료", time_choices[1:], index=rules.close_slot - 1,
            format_func=lambda s: "24:00" if s == SLOTS_PER_DAY else slot_to_str(s),
        )
        if st.form_submit_button("규칙 저장"):
            if close_slot <= open_slot:
                st.error("운영 종료 시간은 시작 시간보다 늦어야 합니다.")
            else:
                db.save_settings({
                    "booking_window_days": int(window),
                    "daily_limit_slots": limit,
                    "open_slot": open_slot,
                    "close_slot": close_slot,
                })
                flash("admin_overview", "예약 규칙을 저장했습니다. 이미 잡힌 예약에는 영향이 없습니다.")
                st.rerun()

    st.markdown("#### 화면 설정")
    with st.form("announcement_form"):
        announcement = st.text_area("공지 (상단 배너에 표시)", value=settings.get("announcement", ""), height=100)
        logo_url = st.text_input("로고 이미지 URL (선택)", value=settings.get("logo_url", ""))
        if st.form_submit_button("화면 설정 저장"):
            if logo_url and not logo_url.startswith("https://"):
                st.error("로고 URL은 https:// 로 시작해야 합니다.")
            else:
                db.save_settings({"announcement": announcement, "logo_url": logo_url.strip()})
                flash("admin_overview", "화면 설정을 저장했습니다.")
                st.rerun()


# ---------------------------------------------------------------- 스터디룸

def upcoming_room_reservation_ids(room_id: int) -> list[int]:
    now = now_kst()
    rows = db.room_reservations_from(room_id, now.date())
    return [row["id"] for row in rows if reservation_phase(row, now) == "upcoming"]


@st.dialog("스터디룸 비활성화")
def confirm_deactivate_room(room: dict, actor: dict) -> None:
    ids = upcoming_room_reservation_ids(room["id"])
    st.write(f"**{room['name']}**을(를) 비활성화하면 더 이상 새 예약을 받지 않습니다.")
    cancel_all = False
    if ids:
        st.warning(f"이 방에 앞으로 예정된 예약이 **{len(ids)}건** 있습니다.")
        cancel_all = st.checkbox("예정된 예약도 모두 취소", value=True)
    col1, col2 = st.columns(2)
    if col1.button("비활성화", type="primary", width="stretch"):
        db.set_room_active(room["id"], False)
        if cancel_all:
            db.cancel_reservations(ids, actor["student_id"], now_kst())
        suffix = f" 예정 예약 {len(ids)}건을 취소했습니다." if cancel_all else ""
        flash("admin_rooms", f"{room['name']}을(를) 비활성화했습니다.{suffix}")
        st.rerun()
    if col2.button("닫기", width="stretch"):
        st.rerun()


def admin_rooms(actor: dict) -> None:
    show_flash("admin_rooms")
    with st.form("room_add", clear_on_submit=True):
        name = st.text_input("새 스터디룸 이름")
        if st.form_submit_button("스터디룸 추가"):
            name = name.strip()
            if not name:
                st.error("이름을 입력하세요.")
            else:
                try:
                    db.add_room(name)
                except Exception:
                    logger.exception("add_room failed")
                    st.error("스터디룸을 추가하지 못했습니다. 같은 이름이 이미 있는지 확인하세요.")
                else:
                    flash("admin_rooms", f"{name}을(를) 추가했습니다.")
                    st.rerun()

    for room in db.all_rooms():
        with st.container(border=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            state = ":green[활성]" if room["is_active"] else ":gray[비활성]"
            c1.markdown(f"**{room['name']}** · {state}")
            if room["is_active"]:
                if c2.button("비활성화", key=f"room_off_{room['id']}", width="stretch"):
                    confirm_deactivate_room(room, actor)
            elif c2.button("활성화", key=f"room_on_{room['id']}", width="stretch", type="primary"):
                db.set_room_active(room["id"], True)
                flash("admin_rooms", f"{room['name']}을(를) 활성화했습니다.")
                st.rerun()


# ---------------------------------------------------------------- 사용자

def import_roster(file, overwrite: bool) -> tuple[int, int, int]:
    """(신규, 갱신, 짧은 비밀번호 수). 기존 사용자의 활성 상태는 바꾸지 않는다."""
    df = pd.read_excel(BytesIO(file.getvalue()), dtype=str)
    entries = parse_roster(df.to_dict("records"), list(df.columns))
    if not entries:
        raise ValueError("반영할 행이 없습니다. 학번과 비밀번호가 채워져 있는지 확인하세요.")

    existing = db.existing_users([e.student_id for e in entries])
    inserts: list[dict] = []
    updates: list[dict] = []
    short = 0
    progress = st.progress(0.0, text="비밀번호를 암호화하는 중…")
    for i, entry in enumerate(entries, start=1):
        current = existing.get(entry.student_id)
        if current is None or (overwrite and not current.get("is_admin")):
            short += len(entry.password) < PASSWORD_MIN_LENGTH
            record = {
                "student_id": entry.student_id,
                "name": entry.name or (current or {}).get("name") or "",
                "password_hash": generate_password_hash(entry.password),
                "must_change_password": True,
            }
            if current is None:
                inserts.append({**record, "is_admin": False, "is_active": True})
            else:
                updates.append(record)
        progress.progress(i / len(entries), text=f"비밀번호를 암호화하는 중… ({i}/{len(entries)})")

    progress.progress(1.0, text="저장하는 중…")
    for i in range(0, len(inserts), 200):
        db.insert_users(inserts[i : i + 200])
    for i in range(0, len(updates), 200):
        db.upsert_users(updates[i : i + 200])
    progress.empty()
    return len(inserts), len(updates), short


def issue_temp_password(member: dict) -> None:
    temp = secrets.token_urlsafe(6)
    db.update_user(member["student_id"], {
        "password_hash": generate_password_hash(temp),
        "must_change_password": True,
        "failed_attempts": 0,
        "locked_until": None,
    })
    db.delete_user_sessions(member["student_id"])
    st.session_state.temp_password_notice = (member["student_id"], temp)


def member_label(member: dict, now) -> str:
    parts = [f"**{member['student_id']}**", member.get("name") or "-"]
    if member.get("is_admin"):
        parts.append(":blue[관리자]")
    if not member.get("is_active"):
        parts.append(":gray[비활성]")
    suspended = parse_iso(member.get("suspend_until"))
    if suspended and suspended > now:
        parts.append(f":red[정지 ~{suspended:%m-%d %H:%M}]")
    locked = parse_iso(member.get("locked_until"))
    if locked and locked > now:
        parts.append(f":orange[로그인 잠김 ~{locked:%H:%M}]")
    if member.get("must_change_password"):
        parts.append(":gray[비밀번호 변경 대기]")
    return " · ".join(parts)


def member_actions(member: dict, now) -> None:
    sid = member["student_id"]
    if member["is_active"]:
        if st.button("비활성화 (로그인 차단)", key=f"deact_{sid}", width="stretch"):
            db.update_user(sid, {"is_active": False})
            flash("admin_users", f"{sid} 계정을 비활성화했습니다.")
            st.rerun()
    elif st.button("활성화 (승인)", key=f"act_{sid}", width="stretch", type="primary"):
        db.update_user(sid, {"is_active": True})
        flash("admin_users", f"{sid} 계정을 활성화했습니다.")
        st.rerun()

    suspended = parse_iso(member.get("suspend_until"))
    if suspended and suspended > now:
        if st.button("정지 해제", key=f"unsuspend_{sid}", width="stretch"):
            db.update_user(sid, {"suspend_until": None})
            flash("admin_users", f"{sid} 계정의 이용 정지를 해제했습니다.")
            st.rerun()
    else:
        days = st.selectbox(
            "정지 기간", SUSPEND_DAY_OPTIONS, index=1, key=f"days_{sid}", format_func=lambda d: f"{d}일"
        )
        if st.button("이용 정지", key=f"suspend_{sid}", width="stretch"):
            until = now + timedelta(days=days)
            db.update_user(sid, {"suspend_until": until.isoformat()})
            flash("admin_users", f"{sid} 계정을 {until:%m-%d %H:%M}까지 정지했습니다.")
            st.rerun()

    locked = parse_iso(member.get("locked_until"))
    if locked and locked > now:
        if st.button("로그인 잠금 해제", key=f"unlock_{sid}", width="stretch"):
            db.update_user(sid, {"locked_until": None, "failed_attempts": 0})
            flash("admin_users", f"{sid} 계정의 로그인 잠금을 해제했습니다.")
            st.rerun()

    if st.button("임시 비밀번호 발급", key=f"reset_{sid}", width="stretch"):
        issue_temp_password(member)
        st.rerun()

    st.divider()
    sure = st.checkbox("삭제하면 예약 기록도 함께 삭제됨을 확인했습니다", key=f"del_ok_{sid}")
    if st.button("계정 삭제", key=f"del_{sid}", width="stretch", disabled=not sure):
        db.delete_user(sid)
        flash("admin_users", f"{sid} 계정을 삭제했습니다.")
        st.rerun()


def admin_users(actor: dict) -> None:
    show_flash("admin_users")
    notice = st.session_state.pop("temp_password_notice", None)
    if notice:
        sid, temp = notice
        st.success(f"{sid}의 임시 비밀번호입니다. 이 화면을 벗어나면 다시 볼 수 없으니 본인에게 전달하세요.")
        st.code(temp, language=None)

    with st.expander("사용자 직접 추가"):
        with st.form("user_add", clear_on_submit=True):
            sid = st.text_input("학번").strip()
            name = st.text_input("이름").strip()
            password = st.text_input("초기 비밀번호", type="password", help="첫 로그인 때 변경하도록 안내됩니다.")
            if st.form_submit_button("사용자 추가"):
                if not sid:
                    st.error("학번을 입력하세요.")
                elif len(password) < PASSWORD_MIN_LENGTH:
                    st.error(f"초기 비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다.")
                elif db.get_user(sid):
                    st.error("이미 존재하는 학번입니다.")
                else:
                    db.insert_users([{
                        "student_id": sid,
                        "name": name,
                        "password_hash": generate_password_hash(password),
                        "is_admin": False,
                        "is_active": True,
                        "must_change_password": True,
                    }])
                    flash("admin_users", f"{sid} 사용자를 추가했습니다.")
                    st.rerun()

    with st.expander("엑셀 명단 업로드"):
        st.caption(
            "'학번', '비밀번호' 열이 필요하며 '이름' 열은 선택입니다. "
            "업로드된 사용자는 첫 로그인 때 비밀번호를 바꿔야 합니다."
        )
        uploaded = st.file_uploader("roster.xlsx", type=["xlsx"])
        overwrite = st.checkbox("기존 사용자의 비밀번호와 이름도 덮어쓰기 (관리자 제외)")
        if st.button("명단 반영", disabled=uploaded is None):
            try:
                added, updated, short = import_roster(uploaded, overwrite)
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                logger.exception("roster import failed")
                st.error("명단을 반영하지 못했습니다. 파일 형식을 확인하세요.")
            else:
                message = f"신규 {added}명, 갱신 {updated}명을 반영했습니다."
                if short:
                    message += (
                        f" ({short}명은 초기 비밀번호가 {PASSWORD_MIN_LENGTH}자 미만이라 "
                        "첫 로그인 때 반드시 변경해야 합니다.)"
                    )
                st.success(message)

    st.markdown("#### 사용자 목록")
    term = sanitize_search(st.text_input("학번 또는 이름 검색", key="user_search"))
    page = st.session_state.get("user_page", 0)
    if st.session_state.get("user_search_last") != term:
        page = 0
    st.session_state.user_search_last = term
    members, total = db.search_users(term, page, USERS_PAGE_SIZE)
    pages = max((total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE, 1)
    page = min(page, pages - 1)
    st.caption(f"총 {total}명 · {page + 1}/{pages} 페이지")

    now = now_kst()
    for member in members:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.markdown(member_label(member, now))
            if member.get("is_admin") or member["student_id"] == actor["student_id"]:
                continue
            with c2.popover("관리", width="stretch"):
                member_actions(member, now)

    if pages > 1:
        p1, p2, p3 = st.columns([1, 2, 1])
        if p1.button("◀ 이전", disabled=page == 0, width="stretch"):
            st.session_state.user_page = page - 1
            st.rerun()
        if p3.button("다음 ▶", disabled=page >= pages - 1, width="stretch"):
            st.session_state.user_page = page + 1
            st.rerun()
    st.session_state.user_page = page


# ---------------------------------------------------------------- 예약

def admin_reservations(actor: dict) -> None:
    show_flash("admin_reservations")
    today = now_kst().date()
    rules = Rules.from_settings(db.get_settings())
    rooms = db.all_rooms()
    room_by_name = {room["name"]: room["id"] for room in rooms}

    c1, c2 = st.columns(2)
    period = c1.date_input(
        "기간", value=(today, today + timedelta(days=rules.booking_window_days - 1)), key="admin_res_period"
    )
    selected_rooms = c2.multiselect("스터디룸", list(room_by_name), placeholder="전체")
    c3, c4 = st.columns(2)
    term = sanitize_search(c3.text_input("학번 검색", key="admin_res_term"))
    include_cancelled = c4.checkbox("취소된 예약 포함", key="admin_res_cancelled")

    if not isinstance(period, (tuple, list)) or len(period) != 2:
        st.info("기간의 시작일과 종료일을 모두 선택하세요.")
        return
    start, end = period
    rows = db.admin_reservations(
        start, end, [room_by_name[n] for n in selected_rooms], term, include_cancelled
    )
    if not rows:
        st.info("조건에 맞는 예약이 없습니다.")
        return

    df = pd.DataFrame([
        {
            "날짜": row["res_date"],
            "시간": range_to_str(row["start_slot"], row["end_slot"]),
            "스터디룸": (row.get("rooms") or {}).get("name", ""),
            "학번": row["student_id"],
            "이름": (row.get("users") or {}).get("name", ""),
            "상태": "예약" if row["status"] == "active" else "취소",
            "예약 시각": _fmt_ts(row.get("created_at")),
            "취소 시각": _fmt_ts(row.get("cancelled_at")),
            "취소자": row.get("cancelled_by") or "",
        }
        for row in rows
    ])
    st.caption(f"{len(rows)}건" + (" (최대 2000건까지 표시)" if len(rows) >= 2000 else ""))
    st.dataframe(df, hide_index=True, width="stretch")
    st.download_button(
        "CSV 내려받기",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"reservations_{start:%Y%m%d}_{end:%Y%m%d}.csv",
        mime="text/csv",
    )

    cancellable = [row for row in rows if row["status"] == "active" and _not_ended(row)]
    if cancellable:
        st.markdown("#### 예약 취소")
        by_label = {
            f"{row['res_date']} {range_to_str(row['start_slot'], row['end_slot'])} · "
            f"{(row.get('rooms') or {}).get('name', '')} · {row['student_id']}": row
            for row in cancellable
        }
        choice = st.selectbox("취소할 예약", list(by_label), index=None, placeholder="예약 선택")
        if st.button("선택한 예약 취소", disabled=choice is None):
            confirm_cancel(by_label[choice], actor, "admin_reservations")


def _not_ended(row: dict) -> bool:
    return reservation_phase(row, now_kst()) != "past"


def _fmt_ts(value: str | None) -> str:
    parsed = parse_iso(value)
    return f"{parsed:%Y-%m-%d %H:%M}" if parsed else ""

