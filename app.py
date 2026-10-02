"""제2의학관 스터디룸 예약 — Streamlit 진입점.

화면별 코드는 studyroom/views/, DB 접근은 studyroom/db.py,
예약 규칙 등 순수 로직은 studyroom/rules.py 에 있다.
"""

from __future__ import annotations

import logging

import streamlit as st

from studyroom import auth, db
from studyroom.timeutil import now_kst
from studyroom.ui import apply_styles, footer, hero
from studyroom.views import account, admin, booking, mine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("studyroom")

st.set_page_config(
    page_title="제2의학관 스터디룸 예약",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)


def login_screen(setup_warning: str | None) -> None:
    left, center, right = st.columns([1, 1.25, 1])
    with center:
        hero("제2의학관 스터디룸을 간편하게 예약하세요.")
        if setup_warning:
            st.warning(setup_warning)
        notice = st.session_state.pop("login_notice", None)
        if notice:
            st.error(notice)
        with st.form("login_form"):
            sid = st.text_input("학번 또는 관리자 아이디")
            password = st.text_input("비밀번호", type="password")
            remember = st.checkbox("로그인 상태 유지", help="개인 기기에서만 사용하세요. 로그아웃하면 해제됩니다.")
            submitted = st.form_submit_button("로그인", type="primary", width="stretch")
        if submitted:
            user, error = auth.authenticate(sid.strip(), password)
            if error:
                st.error(error)
            else:
                auth.sign_in(user, remember)
                st.rerun()
        st.caption("계정이 없거나 비밀번호를 잊었다면 학생회에 문의하세요.")


def sidebar(user: dict) -> None:
    with st.sidebar:
        logo_url = db.setting("logo_url")
        if logo_url:
            st.image(logo_url, width=160)
        st.subheader("스터디룸 예약")
        st.write(f"**{user.get('name') or user['student_id']}**")
        st.caption("관리자" if user.get("is_admin") else f"학번 {user['student_id']}")
        st.divider()
        st.caption(f"현재 시각 · {now_kst():%Y-%m-%d %H:%M}")
        if st.button("로그아웃", width="stretch"):
            auth.sign_out()
            st.rerun()


def main() -> None:
    apply_styles()
    try:
        setup_warning = auth.bootstrap()
        auth.restore_session()
        user = auth.current_user()
    except Exception:
        logger.exception("Supabase connection or bootstrap failed")
        st.error("Supabase 연결 또는 초기화에 실패했습니다. 잠시 후 다시 시도하세요.")
        st.info("관리자라면 README와 supabase_schema.sql을 따라 Supabase와 Secrets 설정을 확인하세요.")
        st.stop()

    auth.apply_cookie_action()

    if not user:
        login_screen(setup_warning)
        footer()
        return

    sidebar(user)
    if user.get("must_change_password"):
        account.forced_change(user)
        footer()
        return

    hero(db.setting("announcement") or "스터디룸 예약 시스템에 오신 것을 환영합니다.")
    tab_names = ["예약", "내 예약", "계정"] + (["관리자"] if user.get("is_admin") else [])
    tabs = st.tabs(tab_names)
    with tabs[0]:
        booking.render(user)
    with tabs[1]:
        mine.render(user)
    with tabs[2]:
        account.render(user)
    if user.get("is_admin"):
        with tabs[3]:
            admin.render(user)
    footer()


if __name__ == "__main__":
    main()
