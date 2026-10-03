"""앱 전역 상수와 기본 운영 규칙.

예약 규칙의 기본값은 settings 테이블에 값이 없을 때만 쓰이며,
관리자 화면에서 바꾼 값은 Python과 Supabase RPC가 같은 settings 행을 읽는다.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# supabase_schema.sql 의 book_room / cancel_reservation 함수와 같은 값을 써야 한다.
SLOT_MINUTES = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES

DEFAULT_SETTINGS: dict[str, str] = {
    "global_lock": "0",
    "announcement": "제2의학관 스터디룸 예약 시스템에 오신 것을 환영합니다.",
    "logo_url": "",
    "booking_window_days": "7",
    "daily_limit_slots": "4",
    "open_slot": "0",
    "close_slot": str(SLOTS_PER_DAY),
}

# 예약 가능 기간(오늘 포함 일수). 관리자 화면에서 고를 수 있는 값과 최댓값.
MAX_BOOKING_WINDOW_DAYS = 56
BOOKING_WINDOW_CHOICES = [1, 2, 3, 4, 5, 6, 7, 14, 21, 28, 35, 42, 49, 56]
# 이 일수까지는 날짜를 버튼으로, 넘으면 달력으로 고른다.
DATE_PILLS_MAX_DAYS = 14

PASSWORD_MIN_LENGTH = 8
MAX_FAILED_LOGINS = 5
LOGIN_LOCK_MINUTES = 10

SESSION_COOKIE = "studyroom_session"
SESSION_DAYS = 14

PAST_RESERVATION_DAYS = 14
USERS_PAGE_SIZE = 30
SUSPEND_DAY_OPTIONS = [1, 3, 7, 14, 30]
