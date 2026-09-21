from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape
from io import BytesIO

import pandas as pd
import pytz
import streamlit as st
from supabase import Client, create_client
from werkzeug.security import check_password_hash, generate_password_hash

KST = pytz.timezone("Asia/Seoul")
SLOT_MINUTES = 30
SLOTS_PER_DAY = 48
BOOKING_WINDOW_DAYS = 7

st.set_page_config(
    page_title="제2의학관 스터디룸 예약",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
:root { --primary:#2783DE; --ink:#2C2C2B; --muted:#7D7A75; --border:#E6E5E3; --soft:#F9F8F7; }
.stApp { background:#FFFFFF; color:var(--ink); }
.block-container { max-width:1080px; padding-top:2rem; padding-bottom:5rem; }
[data-testid="stSidebar"] { background:#F9F8F7; border-right:1px solid #E6E5E3; }
.hero { padding:24px 28px; border:1px solid #E6E5E3; border-radius:12px; background:linear-gradient(135deg,#FFFFFF 0%,#F4F9FE 100%); margin-bottom:24px; }
.hero h1 { margin:0 0 6px; font-size:2rem; letter-spacing:-0.03em; }
.hero p { margin:0; color:#6B7280; }
.metric-card { padding:18px; border:1px solid #E6E5E3; border-radius:10px; background:#FFFFFF; }
.slot-card { padding:14px 16px; border:1px solid #E6E5E3; border-radius:10px; background:#F9F8F7; margin:8px 0; }
.small-muted { color:#7D7A75; font-size:0.9rem; }
div.stButton > button, div.stFormSubmitButton > button { min-height:44px; border-radius:8px; font-weight:650; }
div[data-testid="stMetric"] { border:1px solid #E6E5E3; padding:16px; border-radius:10px; background:#FFFFFF; }
[data-baseweb="tab-list"] { gap:8px; }
[data-baseweb="tab"] { height:44px; }
@media (max-width: 640px) {
  .block-container { padding:1.1rem 1rem 4rem; }
  .hero { padding:20px; }
  .hero h1 { font-size:1.55rem; }
}
</style>
""",
    unsafe_allow_html=True,
)


def now_kst() -> datetime:
    return datetime.now(KST)


def slot_to_str(slot: int) -> str:
    total = slot * SLOT_MINUTES
    return f"{total // 60:02d}:{total % 60:02d}"


def range_to_str(start_slot: int, end_slot: int) -> str:
    return f"{slot_to_str(start_slot)} ~ {slot_to_str(end_slot)}"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = KST.localize(parsed)
    return parsed.astimezone(KST)


@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL", "").strip().rstrip("/")
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    # Supabase Python 클라이언트에는 프로젝트 루트 URL만 전달합니다.
    if url.endswith("/rest/v1"):
        url = url[:-len("/rest/v1")]

    if not url or not key:
        raise RuntimeError("SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY를 설정하세요.")

    return create_client(url, key)


def one(table: str, column: str, value: object):
    rows = get_supabase().table(table).select("*").eq(column, value).limit(1).execute().data or []
    return rows[0] if rows else None


def setting(key: str, default: str = "") -> str:
    row = one("settings", "key", key)
    return str(row.get("value", default)) if row else default


def save_setting(key: str, value: str) -> None:
    db = get_supabase()
    row = one("settings", "key", key)
    if row:
        db.table("settings").update({"value": str(value)}).eq("key", key).execute()
    else:
        db.table("settings").insert({"key": key, "value": str(value)}).execute()


def ensure_initial_users() -> None:
    db = get_supabase()
    admins = db.table("users").select("student_id").eq("is_admin", True).limit(1).execute().data or []
    if not admins:
        db.table("users").insert(
            {
                "student_id": "1",
                "password_hash": generate_password_hash("1"),
                "name": "학생회",
                "is_admin": True,
                "is_active": True,
            }
        ).execute()
    if not one("users", "student_id", "0"):
        db.table("users").insert(
            {
                "student_id": "0",
                "password_hash": generate_password_hash("0"),
                "name": "테스트 사용자",
                "is_admin": False,
                "is_active": True,
            }
        ).execute()


def current_user():
    sid = st.session_state.get("sid")
    return one("users", "student_id", sid) if sid else None


def login_screen() -> None:
    left, center, right = st.columns([1, 1.25, 1])
    with center:
        st.markdown(
            """<div class="hero"><h1>📚 스터디룸 예약</h1>
            <p>제2의학관 스터디룸을 간편하게 예약하세요.</p></div>""",
            unsafe_allow_html=True,
        )
        with st.form("login_form"):
            sid = st.text_input("학번 또는 관리자 아이디")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if submitted:
            user = one("users", "student_id", sid.strip())
            if not user or not check_password_hash(user.get("password_hash", ""), password):
                st.error("아이디 또는 비밀번호가 올바르지 않습니다.")
            elif not user.get("is_active", False):
                st.error("승인되지 않은 계정입니다. 학생회에 문의하세요.")
            else:
                suspended = parse_iso(user.get("suspend_until"))
                if suspended and now_kst() < suspended:
                    st.error(f"{suspended:%Y-%m-%d %H:%M}까지 이용이 정지된 계정입니다.")
                else:
                    st.session_state.sid = user["student_id"]
                    st.rerun()
        st.caption("최초 관리자 1 / 1 · 시험 사용자 0 / 0")


def sidebar(user: dict) -> None:
    with st.sidebar:
        logo_url = setting("logo_url", "")
        if logo_url:
            st.image(logo_url, width=160)
        st.subheader("스터디룸 예약")
        st.write(f"**{user.get('name') or user['student_id']}**")
        st.caption("관리자" if user.get("is_admin") else f"학번 {user['student_id']}")
        st.divider()
        st.caption(f"현재 시각 · {now_kst():%Y-%m-%d %H:%M}")
        if st.button("로그아웃", use_container_width=True):
            st.session_state.pop("sid", None)
            st.rerun()


def show_header() -> None:
    announcement = setting("announcement", "스터디룸 예약 시스템에 오신 것을 환영합니다.")
    st.markdown(
        f"""<div class="hero"><h1>제2의학관 스터디룸</h1>
        <p>{escape(announcement)}</p></div>""",
        unsafe_allow_html=True,
    )


def active_rooms() -> list[dict]:
    return (
        get_supabase()
        .table("rooms")
        .select("id,name,is_active")
        .eq("is_active", True)
        .order("id")
        .execute()
        .data
        or []
    )


def room_schedule(room_id: int, chosen_date: date) -> list[dict]:
    return (
        get_supabase()
        .table("reservations")
        .select("id,student_id,start_slot,end_slot")
        .eq("room_id", room_id)
        .eq("res_date", chosen_date.isoformat())
        .eq("status", "active")
        .order("start_slot")
        .execute()
        .data
        or []
    )


def reservation_tab(user: dict) -> None:
    st.subheader("새 예약")
    if setting("global_lock", "0") == "1":
        st.warning("현재 관리자가 전체 예약을 잠갔습니다.")
        return
    rooms = active_rooms()
    if not rooms:
        st.info("현재 예약 가능한 스터디룸이 없습니다.")
        return

    col1, col2 = st.columns(2)
    with col1:
        chosen_date = st.date_input(
            "날짜",
            value=now_kst().date(),
            min_value=now_kst().date(),
            max_value=now_kst().date() + timedelta(days=BOOKING_WINDOW_DAYS - 1),
        )
    with col2:
        room_names = {room["name"]: room for room in rooms}
        room_name = st.selectbox("스터디룸", list(room_names))
        room = room_names[room_name]

    schedule = room_schedule(room["id"], chosen_date)
    with st.expander("선택한 방의 예약 현황", expanded=True):
        if schedule:
            for row in schedule:
                st.markdown(
                    f"<div class='slot-card'><b>{range_to_str(row['start_slot'], row['end_slot'])}</b>"
                    f"<div class='small-muted'>예약 완료</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("아직 예약이 없습니다.")

    time_options = list(range(SLOTS_PER_DAY))
    with st.form("booking_form"):
        b1, b2 = st.columns(2)
        with b1:
            start_slot = st.selectbox("시작 시간", time_options, format_func=slot_to_str)
        with b2:
            units = st.selectbox("이용 시간", [1, 2, 3, 4], format_func=lambda x: f"{x * 30}분")
        submitted = st.form_submit_button("예약하기", type="primary", use_container_width=True)
    if submitted:
        try:
            result = get_supabase().rpc(
                "book_room",
                {
                    "p_student_id": user["student_id"],
                    "p_room_id": room["id"],
                    "p_res_date": chosen_date.isoformat(),
                    "p_start_slot": start_slot,
                    "p_units": units,
                },
            ).execute().data
            if isinstance(result, list):
                result = result[0] if result else {}
            if result and result.get("ok"):
                st.success(result.get("message", "예약이 완료되었습니다."))
                st.rerun()
            else:
                st.error((result or {}).get("message", "예약할 수 없습니다."))
        except Exception as exc:
            st.error(f"예약 처리 중 오류가 발생했습니다: {exc}")


def reservation_rows(student_id: str | None = None, limit: int = 100) -> list[dict]:
    query = (
        get_supabase()
        .table("reservations")
        .select("id,student_id,res_date,start_slot,end_slot,status,rooms(name)")
        .eq("status", "active")
    )
    if student_id:
        query = query.eq("student_id", student_id)
    return query.order("res_date").order("start_slot").limit(limit).execute().data or []


def cancel_reservation(reservation_id: int, actor: dict) -> tuple[bool, str]:
    row = one("reservations", "id", reservation_id)
    if not row or row.get("status") != "active":
        return False, "예약을 찾을 수 없습니다."
    if not actor.get("is_admin") and row.get("student_id") != actor["student_id"]:
        return False, "본인 예약만 취소할 수 있습니다."
    if not actor.get("is_admin"):
        d = date.fromisoformat(row["res_date"])
        start = KST.localize(datetime.combine(d, time.min) + timedelta(minutes=row["start_slot"] * 30))
        if start <= now_kst():
            return False, "예약 시작 이후에는 취소할 수 없습니다."
    get_supabase().table("reservations").update({"status": "cancelled"}).eq("id", reservation_id).execute()
    return True, "예약이 취소되었습니다."


def my_reservations_tab(user: dict) -> None:
    st.subheader("내 예약")
    rows = reservation_rows(user["student_id"])
    if not rows:
        st.info("활성 예약이 없습니다.")
        return
    for row in rows:
        room_name = (row.get("rooms") or {}).get("name", "스터디룸")
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(
                f"<div class='slot-card'><b>{escape(room_name)}</b> · {row['res_date']} · "
                f"{range_to_str(row['start_slot'], row['end_slot'])}</div>",
                unsafe_allow_html=True,
            )
        with c2:
            if st.button("취소", key=f"cancel_{row['id']}", use_container_width=True):
                ok, message = cancel_reservation(row["id"], user)
                (st.success if ok else st.error)(message)
                if ok:
                    st.rerun()


def account_tab(user: dict) -> None:
    st.subheader("계정 설정")
    with st.form("password_form"):
        current = st.text_input("현재 비밀번호", type="password")
        new = st.text_input("새 비밀번호", type="password", help="8자 이상")
        confirm = st.text_input("새 비밀번호 확인", type="password")
        submitted = st.form_submit_button("비밀번호 변경", type="primary")
    if submitted:
        latest = one("users", "student_id", user["student_id"])
        if not latest or not check_password_hash(latest["password_hash"], current):
            st.error("현재 비밀번호가 일치하지 않습니다.")
        elif len(new) < 8:
            st.error("새 비밀번호는 8자 이상이어야 합니다.")
        elif new != confirm:
            st.error("새 비밀번호 확인이 일치하지 않습니다.")
        else:
            get_supabase().table("users").update({"password_hash": generate_password_hash(new)}).eq(
                "student_id", user["student_id"]
            ).execute()
            st.success("비밀번호가 변경되었습니다.")


def admin_overview(user: dict) -> None:
    db = get_supabase()
    users = db.table("users").select("student_id", count="exact").execute()
    rooms = db.table("rooms").select("id", count="exact").eq("is_active", True).execute()
    reservations = (
        db.table("reservations")
        .select("id", count="exact")
        .eq("status", "active")
        .gte("res_date", now_kst().date().isoformat())
        .execute()
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("사용자", users.count or 0)
    m2.metric("활성 스터디룸", rooms.count or 0)
    m3.metric("예정 예약", reservations.count or 0)

    st.markdown("#### 운영 설정")
    locked = setting("global_lock", "0") == "1"
    if st.button("전체 예약 잠금 해제" if locked else "전체 예약 잠그기", type="primary" if not locked else "secondary"):
        save_setting("global_lock", "0" if locked else "1")
        st.rerun()
    with st.form("announcement_form"):
        announcement = st.text_area("공지", value=setting("announcement", ""), height=100)
        logo_url = st.text_input("로고 이미지 URL (선택)", value=setting("logo_url", ""))
        if st.form_submit_button("화면 설정 저장"):
            save_setting("announcement", announcement)
            save_setting("logo_url", logo_url)
            st.success("화면 설정을 저장했습니다.")
            st.rerun()


def admin_rooms() -> None:
    db = get_supabase()
    with st.form("room_add"):
        name = st.text_input("새 스터디룸 이름")
        if st.form_submit_button("스터디룸 추가"):
            if not name.strip():
                st.error("이름을 입력하세요.")
            else:
                db.table("rooms").insert({"name": name.strip(), "is_active": True}).execute()
                st.success("스터디룸을 추가했습니다.")
                st.rerun()
    rooms = db.table("rooms").select("*").order("id").execute().data or []
    for room in rooms:
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{room['name']}** · {'활성' if room['is_active'] else '비활성'}")
        if c2.button("상태 전환", key=f"room_{room['id']}", use_container_width=True):
            db.table("rooms").update({"is_active": not room["is_active"]}).eq("id", room["id"]).execute()
            st.rerun()


def upsert_roster(file, overwrite: bool) -> tuple[int, int]:
    df = pd.read_excel(BytesIO(file.getvalue()), dtype=str).fillna("")
    normalized = {str(c).strip(): c for c in df.columns}
    id_col = normalized.get("학번") or normalized.get("student_id") or normalized.get("id")
    pw_col = normalized.get("비밀번호") or normalized.get("비번") or normalized.get("password") or normalized.get("pw")
    name_col = normalized.get("이름") or normalized.get("name")
    if not id_col or not pw_col:
        raise ValueError("엑셀에 '학번'과 '비밀번호' 열이 필요합니다.")
    added = updated = 0
    db = get_supabase()
    for _, row in df.iterrows():
        sid = str(row[id_col]).strip()
        password = str(row[pw_col]).strip()
        name = str(row[name_col]).strip() if name_col else ""
        if not sid or not password:
            continue
        existing = one("users", "student_id", sid)
        payload = {"name": name, "is_active": True}
        if existing:
            if overwrite and not existing.get("is_admin"):
                payload["password_hash"] = generate_password_hash(password)
                db.table("users").update(payload).eq("student_id", sid).execute()
                updated += 1
        else:
            payload.update({"student_id": sid, "password_hash": generate_password_hash(password), "is_admin": False})
            db.table("users").insert(payload).execute()
            added += 1
    return added, updated


def admin_users() -> None:
    db = get_supabase()
    with st.expander("사용자 직접 추가", expanded=False):
        with st.form("user_add"):
            sid = st.text_input("학번")
            name = st.text_input("이름")
            password = st.text_input("초기 비밀번호", type="password")
            if st.form_submit_button("사용자 추가"):
                if len(password) < 8:
                    st.error("초기 비밀번호는 8자 이상이어야 합니다.")
                elif one("users", "student_id", sid.strip()):
                    st.error("이미 존재하는 학번입니다.")
                else:
                    db.table("users").insert(
                        {
                            "student_id": sid.strip(),
                            "name": name.strip(),
                            "password_hash": generate_password_hash(password),
                            "is_admin": False,
                            "is_active": True,
                        }
                    ).execute()
                    st.success("사용자를 추가했습니다.")
                    st.rerun()

    with st.expander("엑셀 명단 업로드", expanded=True):
        uploaded = st.file_uploader("roster.xlsx", type=["xlsx"])
        overwrite = st.checkbox("기존 사용자 비밀번호와 이름도 덮어쓰기")
        if st.button("명단 반영", disabled=uploaded is None):
            try:
                added, updated = upsert_roster(uploaded, overwrite)
                st.success(f"신규 {added}명, 갱신 {updated}명을 반영했습니다.")
            except Exception as exc:
                st.error(str(exc))

    query = st.text_input("학번 또는 이름 검색")
    users = db.table("users").select("*").order("is_admin", desc=True).order("student_id").execute().data or []
    if query:
        q = query.lower()
        users = [u for u in users if q in u["student_id"].lower() or q in (u.get("name") or "").lower()]
    for member in users[:100]:
        c1, c2, c3 = st.columns([4, 1.2, 1.2])
        label = f"{member['student_id']} · {member.get('name') or '-'}"
        if member.get("is_admin"):
            label += " · 관리자"
        if member.get("suspend_until"):
            label += f" · 정지 {parse_iso(member['suspend_until']):%m-%d %H:%M}까지"
        c1.write(label)
        if not member.get("is_admin"):
            if c2.button("승인 전환", key=f"active_{member['student_id']}"):
                db.table("users").update({"is_active": not member["is_active"]}).eq(
                    "student_id", member["student_id"]
                ).execute()
                st.rerun()
            if c3.button("3일 정지", key=f"suspend_{member['student_id']}"):
                until = (now_kst() + timedelta(days=3)).isoformat()
                db.table("users").update({"suspend_until": until}).eq("student_id", member["student_id"]).execute()
                st.rerun()


def admin_reservations(user: dict) -> None:
    rows = reservation_rows(limit=200)
    if not rows:
        st.info("활성 예약이 없습니다.")
        return
    for row in rows:
        room_name = (row.get("rooms") or {}).get("name", "스터디룸")
        c1, c2 = st.columns([5, 1])
        c1.write(
            f"**{row['student_id']}** · {room_name} · {row['res_date']} · "
            f"{range_to_str(row['start_slot'], row['end_slot'])}"
        )
        if c2.button("관리자 취소", key=f"admin_cancel_{row['id']}"):
            cancel_reservation(row["id"], user)
            st.rerun()


def admin_tab(user: dict) -> None:
    st.subheader("관리자")
    overview, rooms, users, reservations = st.tabs(["운영", "스터디룸", "사용자", "예약"])
    with overview:
        admin_overview(user)
    with rooms:
        admin_rooms()
    with users:
        admin_users()
    with reservations:
        admin_reservations(user)


def main() -> None:
    try:
        get_supabase()
        ensure_initial_users()
    except Exception as exc:
        st.error("Supabase 연결 또는 초기화에 실패했습니다.")
        st.code(str(exc))
        st.info("README와 supabase_schema.sql을 따라 Supabase를 먼저 설정하세요.")
        st.stop()

    user = current_user()
    if not user:
        login_screen()
        return
    if not user.get("is_active"):
        st.session_state.pop("sid", None)
        st.error("승인되지 않은 계정입니다.")
        st.stop()

    sidebar(user)
    show_header()
    tab_names = ["예약", "내 예약", "계정"] + (["관리자"] if user.get("is_admin") else [])
    tabs = st.tabs(tab_names)
    with tabs[0]:
        reservation_tab(user)
    with tabs[1]:
        my_reservations_tab(user)
    with tabs[2]:
        account_tab(user)
    if user.get("is_admin"):
        with tabs[3]:
            admin_tab(user)
    st.divider()
    st.markdown(
        """
        <p style="text-align: center; color: #808080; font-size: 0.78rem; margin-top: 0.5rem;">
        Webpage designed by 한양대학교 의과대학 스터디룸 제작 TF
        (변서현, 박예영, 최준서, 한승주)
        </p>
        """,
        unsafe_allow_html=True,
    )

if __name__ == "__main__":
    main()
