"""공통 화면 요소: 스타일, 헤더, 알림, 시간표 그리드, 푸터."""

from __future__ import annotations

from datetime import date, datetime
from html import escape

import streamlit as st

from .rules import Rules
from .timeutil import slot_start, slot_to_str

STYLES = """
<style>
    .block-container { max-width: 1080px; padding-top: 3rem; padding-bottom: 5rem; }
    div.stButton > button,
    div.stFormSubmitButton > button,
    div.stDownloadButton > button { min-height: 44px; border-radius: 8px; font-weight: 650; }
    [data-baseweb="tab-list"] { gap: 8px; }
    [data-baseweb="tab"] { height: 44px; }

    .hero {
        padding: 24px 28px;
        border: 1px solid rgba(128, 128, 128, 0.25);
        border-radius: 12px;
        background: linear-gradient(135deg, rgba(14, 74, 132, 0.04) 0%, rgba(14, 74, 132, 0.16) 100%);
        margin-bottom: 24px;
    }
    .hero h1 { margin: 0 0 6px; padding: 0; font-size: 2rem; letter-spacing: -0.03em; }
    .hero p { margin: 0; opacity: 0.75; }

    .sched-wrap { overflow-x: auto; margin: 4px 0 8px; }
    .sched { border-collapse: separate; border-spacing: 2px; font-size: 0.72rem; }
    .sched th { font-weight: 500; opacity: 0.7; text-align: left; padding: 0 2px; white-space: nowrap; }
    .sched th.room { padding-right: 8px; font-size: 0.85rem; font-weight: 650; opacity: 1; }
    .sched td { width: 18px; min-width: 18px; height: 26px; border-radius: 3px; padding: 0; }
    .sched td.free { background: rgba(33, 150, 83, 0.30); }
    .sched td.taken { background: rgba(128, 128, 128, 0.45); }
    .sched td.mine { background: rgba(14, 116, 230, 0.85); }
    .sched td.past { background: rgba(128, 128, 128, 0.12); }
    .sched td.hour { border-left: 2px solid rgba(128, 128, 128, 0.35); }
    .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 0.8rem; opacity: 0.85; margin-bottom: 8px; }
    .legend span::before {
        content: ""; display: inline-block; width: 12px; height: 12px;
        border-radius: 3px; margin-right: 5px; vertical-align: -1px;
    }
    .legend .free::before { background: rgba(33, 150, 83, 0.30); }
    .legend .taken::before { background: rgba(128, 128, 128, 0.45); }
    .legend .mine::before { background: rgba(14, 116, 230, 0.85); }
    .legend .past::before { background: rgba(128, 128, 128, 0.12); }

    .footer { text-align: center; opacity: 0.6; font-size: 0.78rem; margin-top: 0.5rem; }

    @media (max-width: 640px) {
        .block-container { padding-top: 2rem; }
        .hero { padding: 20px; }
        .hero h1 { font-size: 1.55rem; }
    }
</style>
"""


def apply_styles() -> None:
    st.markdown(STYLES, unsafe_allow_html=True)


def hero(subtitle: str) -> None:
    body = "<br>".join(escape(line) for line in subtitle.strip().splitlines()) or "&nbsp;"
    st.markdown(f"<div class='hero'><h1>📚 스터디룸 예약</h1><p>{body}</p></div>", unsafe_allow_html=True)


def footer() -> None:
    st.divider()
    st.markdown(
        "<p class='footer'>Webpage designed by 한양대학교 의과대학 스터디룸 제작 TF"
        "<br>(변서현, 박예영, 최준서, 한승주)</p>",
        unsafe_allow_html=True,
    )


def flash(scope: str, message: str, kind: str = "success") -> None:
    """st.rerun() 직후에도 메시지가 보이도록 세션에 저장해 둔다."""
    st.session_state[f"flash_{scope}"] = (kind, message)


def show_flash(scope: str) -> None:
    """flash()로 저장된 메시지를 한 번만 표시한다."""
    item = st.session_state.pop(f"flash_{scope}", None)
    if not item:
        return
    kind, message = item
    show = {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)
    show(message)


def schedule_grid(
    rooms: list[dict],
    reservations: list[dict],
    rules: Rules,
    day: date,
    now: datetime,
    me: str,
) -> None:
    """방 × 시간 예약 현황표. 다른 사람의 학번은 화면에 내보내지 않는다."""
    status: dict[tuple[int, int], str] = {}
    for row in reservations:
        kind = "mine" if row["student_id"] == me else "taken"
        for slot in range(row["start_slot"], row["end_slot"]):
            status[(row["room_id"], slot)] = kind

    header = "".join(
        f"<th>{slot_to_str(slot)[:2] if slot % 2 == 0 else ''}</th>" for slot in rules.slots
    )
    body = []
    for room in rooms:
        cells = []
        for slot in rules.slots:
            kind = status.get((room["id"], slot))
            if kind is None:
                kind = "past" if slot_start(day, slot + 1) <= now else "free"
            hour = " hour" if slot % 2 == 0 else ""
            label = {"free": "예약 가능", "taken": "예약됨", "mine": "내 예약", "past": "지난 시간"}[kind]
            cells.append(f"<td class='{kind}{hour}' title='{slot_to_str(slot)} {label}'></td>")
        body.append(f"<tr><th class='room'>{escape(room['name'])}</th>{''.join(cells)}</tr>")

    st.markdown(
        "<div class='legend'><span class='free'>예약 가능</span><span class='taken'>예약됨</span>"
        "<span class='mine'>내 예약</span><span class='past'>지난 시간</span></div>"
        f"<div class='sched-wrap'><table class='sched'><tr><th class='room'></th>{header}</tr>"
        f"{''.join(body)}</table></div>",
        unsafe_allow_html=True,
    )
