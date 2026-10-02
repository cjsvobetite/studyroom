"""로그인, 로그인 시도 제한, 세션 유지, 초기 관리자 준비."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

import streamlit as st
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .config import LOGIN_LOCK_MINUTES, MAX_FAILED_LOGINS, SESSION_COOKIE, SESSION_DAYS
from .rules import account_block_reason
from .timeutil import now_kst, parse_iso

logger = logging.getLogger("studyroom")

LEGACY_TEST_USER = "0"
SESSION_LOCK_SECONDS = 30


@st.cache_resource(show_spinner=False)
def bootstrap() -> str | None:
    """프로세스당 한 번 실행. 초기 관리자를 준비하고 예전 기본 계정을 정리한다.

    반환값이 있으면 로그인 화면에 표시할 운영 경고다.
    """
    admin_id = str(st.secrets.get("INITIAL_ADMIN_ID", "")).strip()
    admin_pw = str(st.secrets.get("INITIAL_ADMIN_PASSWORD", ""))

    if admin_id and admin_pw:
        existing = db.get_user(admin_id)
        if not existing:
            db.insert_users([{
                "student_id": admin_id,
                "password_hash": generate_password_hash(admin_pw),
                "name": "학생회",
                "is_admin": True,
                "is_active": True,
                "must_change_password": True,
            }])
            logger.info("initial admin %s created from secrets", admin_id)
        elif existing.get("is_admin") and check_password_hash(existing["password_hash"], admin_id):
            # 예전 버전의 '1 / 1' 처럼 아이디와 같은 비밀번호를 쓰는 관리자 → Secrets 비밀번호로 재설정
            db.update_user(admin_id, {
                "password_hash": generate_password_hash(admin_pw),
                "must_change_password": True,
            })
            logger.warning("admin %s had a default password; reset from secrets", admin_id)

    # 예전 버전이 자동 생성하던 시험 계정(0 / 0)은 비활성화한다.
    test_user = db.get_user(LEGACY_TEST_USER)
    if (
        test_user
        and not test_user.get("is_admin")
        and test_user.get("is_active")
        and check_password_hash(test_user["password_hash"], LEGACY_TEST_USER)
    ):
        db.update_user(LEGACY_TEST_USER, {"is_active": False})
        logger.warning("legacy test user deactivated")

    db.delete_expired_sessions(now_kst())

    if not db.any_admin_exists():
        return (
            "관리자 계정이 없습니다. Streamlit Secrets에 INITIAL_ADMIN_ID와 "
            "INITIAL_ADMIN_PASSWORD를 설정한 뒤 앱을 다시 시작하세요."
        )
    return None


def authenticate(student_id: str, password: str) -> tuple[dict | None, str | None]:
    """성공하면 (user, None), 실패하면 (None, 오류 메시지)."""
    now = now_kst()
    blocked_until = st.session_state.get("login_blocked_until")
    if blocked_until and now < blocked_until:
        wait = int((blocked_until - now).total_seconds()) + 1
        return None, f"로그인 시도가 너무 많습니다. {wait}초 후 다시 시도하세요."

    user = db.get_user(student_id)
    locked = parse_iso(user.get("locked_until")) if user else None
    if locked and now < locked:
        return None, f"로그인 시도가 너무 많아 {locked:%H:%M}까지 잠긴 계정입니다."

    if not user or not check_password_hash(user.get("password_hash", ""), password):
        _record_failure(user)
        return None, "아이디 또는 비밀번호가 올바르지 않습니다."

    if user.get("is_admin") and password == user["student_id"]:
        return None, (
            "보안을 위해 아이디와 같은 기본 비밀번호로는 관리자 로그인을 할 수 없습니다. "
            "Secrets에 INITIAL_ADMIN_ID/INITIAL_ADMIN_PASSWORD를 설정하고 앱을 재시작하세요."
        )

    reason = account_block_reason(user, now)
    if reason:
        return None, reason

    changes: dict = {}
    if user.get("failed_attempts") or user.get("locked_until"):
        changes.update({"failed_attempts": 0, "locked_until": None})
    if password == user["student_id"] and not user.get("must_change_password"):
        changes["must_change_password"] = True
    if changes:
        db.update_user(user["student_id"], changes)
        user.update(changes)
    st.session_state.pop("login_failures", None)
    st.session_state.pop("login_blocked_until", None)
    return user, None


def _record_failure(user: dict | None) -> None:
    failures = st.session_state.get("login_failures", 0) + 1
    st.session_state.login_failures = failures
    if failures >= MAX_FAILED_LOGINS:
        st.session_state.login_failures = 0
        st.session_state.login_blocked_until = now_kst() + timedelta(seconds=SESSION_LOCK_SECONDS)

    if not user:
        return
    attempts = (user.get("failed_attempts") or 0) + 1
    if attempts >= MAX_FAILED_LOGINS:
        db.update_user(user["student_id"], {
            "failed_attempts": 0,
            "locked_until": (now_kst() + timedelta(minutes=LOGIN_LOCK_MINUTES)).isoformat(),
        })
    else:
        db.update_user(user["student_id"], {"failed_attempts": attempts})


# ---------------------------------------------------------------- sessions

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign_in(user: dict, remember: bool) -> None:
    st.session_state.sid = user["student_id"]
    st.session_state.pop("session_token_hash", None)
    if remember:
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        db.create_session(token_hash, user["student_id"], now_kst() + timedelta(days=SESSION_DAYS))
        st.session_state.session_token_hash = token_hash
        st.session_state.cookie_action = ("set", token)


def sign_out() -> None:
    token_hash = st.session_state.pop("session_token_hash", None)
    if token_hash:
        try:
            db.delete_session(token_hash)
        except Exception:
            logger.exception("failed to delete session")
    st.session_state.pop("sid", None)
    st.session_state.cookie_action = ("clear", None)
    st.session_state.cookie_checked = True


def restore_session() -> None:
    """새로고침 등으로 Streamlit 세션이 새로 열렸을 때 쿠키로 로그인 상태를 복원한다."""
    if st.session_state.get("sid") or st.session_state.get("cookie_checked"):
        return
    st.session_state.cookie_checked = True
    token = st.context.cookies.get(SESSION_COOKIE)
    if not isinstance(token, str) or not token:
        return
    token_hash = _hash_token(token)
    row = db.get_session(token_hash)
    expires = parse_iso(row.get("expires_at")) if row else None
    if not row or not expires or expires <= now_kst():
        if row:
            db.delete_session(token_hash)
        st.session_state.cookie_action = ("clear", None)
        return
    st.session_state.sid = row["student_id"]
    st.session_state.session_token_hash = token_hash


def apply_cookie_action() -> None:
    """sign_in/sign_out 이 예약해 둔 쿠키 변경을 브라우저에 반영한다."""
    action = st.session_state.pop("cookie_action", None)
    if not action:
        return
    kind, token = action
    if kind == "set":
        value, max_age = token, SESSION_DAYS * 24 * 3600
    else:
        value, max_age = "", 0
    st.html(
        "<script>"
        f"document.cookie = '{SESSION_COOKIE}={value}; path=/; max-age={max_age}; SameSite=Lax'"
        " + (location.protocol === 'https:' ? '; Secure' : '');"
        "</script>",
        unsafe_allow_javascript=True,
    )


def change_password(student_id: str, new_password: str) -> None:
    db.update_user(student_id, {
        "password_hash": generate_password_hash(new_password),
        "must_change_password": False,
    })
    # 비밀번호가 바뀌면 다른 기기의 '로그인 상태 유지' 세션은 모두 끊는다.
    db.delete_user_sessions(student_id, keep_hash=st.session_state.get("session_token_hash"))


def current_user() -> dict | None:
    """로그인한 사용자를 DB에서 다시 읽고, 정지·비활성 상태면 로그아웃시킨다."""
    sid = st.session_state.get("sid")
    if not sid:
        return None
    user = db.get_user(sid)
    reason = account_block_reason(user, now_kst()) if user else "계정을 찾을 수 없습니다."
    if reason:
        sign_out()
        st.session_state.login_notice = reason
        return None
    return user
