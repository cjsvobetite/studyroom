"""Supabase 접근 계층. 화면 코드는 여기 함수만 호출한다."""

from __future__ import annotations

import logging
from datetime import date, datetime

import streamlit as st
from supabase import Client, create_client

from .config import DEFAULT_SETTINGS

logger = logging.getLogger("studyroom")

USER_COLUMNS = (
    "student_id,password_hash,name,is_admin,is_active,suspend_until,"
    "must_change_password,failed_attempts,locked_until"
)


@st.cache_resource(show_spinner=False)
def get_supabase() -> Client:
    url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = str(st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip()

    # Supabase Python 클라이언트에는 프로젝트 루트 URL만 전달합니다.
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]

    if not url or not key:
        raise RuntimeError("SUPABASE_URL과 SUPABASE_SERVICE_ROLE_KEY를 설정하세요.")

    return create_client(url, key)


def table(name: str):
    return get_supabase().table(name)


def first(rows: list[dict] | None) -> dict | None:
    return rows[0] if rows else None


def rpc(name: str, params: dict) -> dict:
    data = get_supabase().rpc(name, params).execute().data
    if isinstance(data, list):
        data = data[0] if data else {}
    return data or {}


# ---------------------------------------------------------------- settings

@st.cache_data(ttl=30, show_spinner=False)
def get_settings() -> dict[str, str]:
    rows = table("settings").select("key,value").execute().data or []
    merged = dict(DEFAULT_SETTINGS)
    merged.update({row["key"]: str(row["value"] or "") for row in rows})
    return merged


def setting(key: str) -> str:
    return get_settings().get(key, DEFAULT_SETTINGS.get(key, ""))


def save_settings(values: dict[str, object]) -> None:
    payload = [{"key": k, "value": str(v)} for k, v in values.items()]
    table("settings").upsert(payload, on_conflict="key").execute()
    get_settings.clear()


# ---------------------------------------------------------------- users

def get_user(student_id: str) -> dict | None:
    if not student_id:
        return None
    return first(table("users").select(USER_COLUMNS).eq("student_id", student_id).limit(1).execute().data)


def update_user(student_id: str, values: dict) -> None:
    table("users").update(values).eq("student_id", student_id).execute()


def insert_users(rows: list[dict]) -> None:
    if rows:
        table("users").insert(rows).execute()


def upsert_users(rows: list[dict]) -> None:
    if rows:
        table("users").upsert(rows, on_conflict="student_id").execute()


def delete_user(student_id: str) -> None:
    table("users").delete().eq("student_id", student_id).execute()


def existing_users(student_ids: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for i in range(0, len(student_ids), 200):
        chunk = student_ids[i : i + 200]
        rows = table("users").select("student_id,name,is_admin").in_("student_id", chunk).execute().data or []
        found.update({row["student_id"]: row for row in rows})
    return found


def any_admin_exists() -> bool:
    return bool(table("users").select("student_id").eq("is_admin", True).limit(1).execute().data)


def search_users(term: str, page: int, page_size: int) -> tuple[list[dict], int]:
    query = table("users").select(
        "student_id,name,is_admin,is_active,suspend_until,must_change_password,locked_until", count="exact"
    )
    if term:
        query = query.or_(f"student_id.ilike.*{term}*,name.ilike.*{term}*")
    start = page * page_size
    result = (
        query.order("is_admin", desc=True)
        .order("student_id")
        .range(start, start + page_size - 1)
        .execute()
    )
    return result.data or [], result.count or 0


def count_users() -> int:
    return table("users").select("student_id", count="exact").limit(1).execute().count or 0


# ---------------------------------------------------------------- sessions

def create_session(token_hash: str, student_id: str, expires_at: datetime) -> None:
    table("user_sessions").insert(
        {"token_hash": token_hash, "student_id": student_id, "expires_at": expires_at.isoformat()}
    ).execute()


def get_session(token_hash: str) -> dict | None:
    return first(
        table("user_sessions").select("student_id,expires_at").eq("token_hash", token_hash).limit(1).execute().data
    )


def delete_session(token_hash: str) -> None:
    table("user_sessions").delete().eq("token_hash", token_hash).execute()


def delete_user_sessions(student_id: str, keep_hash: str | None = None) -> None:
    query = table("user_sessions").delete().eq("student_id", student_id)
    if keep_hash:
        query = query.neq("token_hash", keep_hash)
    query.execute()


def delete_expired_sessions(now: datetime) -> None:
    table("user_sessions").delete().lt("expires_at", now.isoformat()).execute()


# ---------------------------------------------------------------- rooms

@st.cache_data(ttl=30, show_spinner=False)
def active_rooms() -> list[dict]:
    return table("rooms").select("id,name").eq("is_active", True).order("id").execute().data or []


def all_rooms() -> list[dict]:
    return table("rooms").select("id,name,is_active").order("id").execute().data or []


def add_room(name: str) -> None:
    table("rooms").insert({"name": name, "is_active": True}).execute()
    active_rooms.clear()


def set_room_active(room_id: int, active: bool) -> None:
    table("rooms").update({"is_active": active}).eq("id", room_id).execute()
    active_rooms.clear()


# ---------------------------------------------------------------- reservations

def reservations_on(day: date) -> list[dict]:
    return (
        table("reservations")
        .select("id,student_id,room_id,start_slot,end_slot")
        .eq("res_date", day.isoformat())
        .eq("status", "active")
        .order("start_slot")
        .execute()
        .data
        or []
    )


def user_reservations(student_id: str, since: date) -> list[dict]:
    return (
        table("reservations")
        .select("id,student_id,res_date,start_slot,end_slot,rooms(name)")
        .eq("student_id", student_id)
        .eq("status", "active")
        .gte("res_date", since.isoformat())
        .order("res_date")
        .order("start_slot")
        .execute()
        .data
        or []
    )


def room_reservations_from(room_id: int, since: date) -> list[dict]:
    return (
        table("reservations")
        .select("id,res_date,start_slot,end_slot")
        .eq("room_id", room_id)
        .eq("status", "active")
        .gte("res_date", since.isoformat())
        .execute()
        .data
        or []
    )


def admin_reservations(
    start: date, end: date, room_ids: list[int], student_term: str, include_cancelled: bool
) -> list[dict]:
    query = (
        table("reservations")
        .select(
            "id,student_id,res_date,start_slot,end_slot,status,created_at,cancelled_at,cancelled_by,"
            "rooms(name),users(name)"
        )
        .gte("res_date", start.isoformat())
        .lte("res_date", end.isoformat())
    )
    if room_ids:
        query = query.in_("room_id", room_ids)
    if student_term:
        query = query.ilike("student_id", f"*{student_term}*")
    if not include_cancelled:
        query = query.eq("status", "active")
    return query.order("res_date").order("start_slot").limit(2000).execute().data or []


def count_upcoming_reservations(today: date) -> tuple[int, int]:
    """(오늘 예약 수, 오늘 이후 전체 예약 수)"""
    def count(op: str) -> int:
        query = table("reservations").select("id", count="exact").eq("status", "active")
        return getattr(query, op)("res_date", today.isoformat()).limit(1).execute().count or 0

    return count("eq"), count("gte")


def book_room(student_id: str, room_id: int, day: date, start_slot: int, units: int) -> dict:
    return rpc(
        "book_room",
        {
            "p_student_id": student_id,
            "p_room_id": room_id,
            "p_res_date": day.isoformat(),
            "p_start_slot": start_slot,
            "p_units": units,
        },
    )


def cancel_reservation(reservation_id: int, actor_id: str) -> tuple[bool, str]:
    try:
        result = rpc("cancel_reservation", {"p_reservation_id": reservation_id, "p_actor_id": actor_id})
    except Exception:
        logger.exception("cancel_reservation RPC failed")
        return False, "예약 취소 중 오류가 발생했습니다. 잠시 후 다시 시도하세요."
    return bool(result.get("ok")), result.get("message", "예약을 취소할 수 없습니다.")


def cancel_reservations(ids: list[int], actor_id: str, now: datetime) -> None:
    if ids:
        table("reservations").update(
            {"status": "cancelled", "cancelled_at": now.isoformat(), "cancelled_by": actor_id}
        ).in_("id", ids).eq("status", "active").execute()
