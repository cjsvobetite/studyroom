"""'계정' 탭과 첫 로그인 시 비밀번호 강제 변경 화면."""

from __future__ import annotations

import streamlit as st
from werkzeug.security import check_password_hash

from .. import auth, db
from ..config import PASSWORD_MIN_LENGTH
from ..rules import validate_new_password
from ..ui import flash, show_flash


def password_form(user: dict, *, forced: bool = False) -> None:
    with st.form("password_form", clear_on_submit=True):
        current = st.text_input("현재 비밀번호", type="password")
        new = st.text_input("새 비밀번호", type="password", help=f"{PASSWORD_MIN_LENGTH}자 이상, 학번과 다르게")
        confirm = st.text_input("새 비밀번호 확인", type="password")
        submitted = st.form_submit_button("비밀번호 변경", type="primary", width="stretch" if forced else "content")
    if not submitted:
        return
    latest = db.get_user(user["student_id"])
    if not latest or not check_password_hash(latest["password_hash"], current):
        st.error("현재 비밀번호가 일치하지 않습니다.")
        return
    error = validate_new_password(new, confirm, user["student_id"])
    if error:
        st.error(error)
    elif new == current:
        st.error("현재 비밀번호와 다른 비밀번호를 입력하세요.")
    else:
        auth.change_password(user["student_id"], new)
        flash("account", "비밀번호가 변경되었습니다. 다른 기기의 로그인 유지는 해제되었습니다.")
        st.rerun()


def render(user: dict) -> None:
    st.subheader("계정 설정")
    show_flash("account")
    st.write(f"**{user.get('name') or '-'}** · {user['student_id']}" + (" · 관리자" if user.get("is_admin") else ""))
    password_form(user)


def forced_change(user: dict) -> None:
    left, center, right = st.columns([1, 1.5, 1])
    with center:
        st.subheader("비밀번호 변경이 필요합니다")
        st.info("처음 로그인했거나 관리자가 비밀번호를 초기화했습니다. 새 비밀번호를 설정해야 이용할 수 있습니다.")
        password_form(user, forced=True)
        if st.button("로그아웃", width="stretch"):
            auth.sign_out()
            st.rerun()
