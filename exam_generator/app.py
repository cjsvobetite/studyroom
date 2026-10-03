import os
import json
import hashlib
import re as _re
import streamlit as st
from datetime import datetime
from pathlib import Path as _Path
from extractors import extract_text, estimate_tokens
from question_generator import generate_questions
from pdf_export import build_pdf, autonumber_choices
from cbt import parse_cbt_questions, render_cbt
from history import load_history, get_wrong_questions
import pandas as pd
from config import ADMIN_ID, HISTORY_PATH, USERS_PATH, OUTPUTS_DIR, get_secret

# ─── 관리자 설정 ───

def _load_all_history() -> dict:
    if not os.path.exists(HISTORY_PATH):
        return {}
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_all_users() -> dict:
    if not os.path.exists(USERS_PATH):
        return {}
    with open(USERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def render_admin_page():
    st.title("🛠️ 관리자 대시보드")
    st.caption(f"관리자: {ADMIN_ID}")
    all_users   = _load_all_users()
    all_history = _load_all_history()

    tab1, tab2, tab3 = st.tabs(["👥 유저 목록", "📊 사용 통계", "📋 전체 풀이 기록"])

    with tab1:
        st.subheader(f"전체 가입 유저 ({len(all_users)}명)")
        if not all_users:
            st.info("가입된 유저가 없습니다.")
        else:
            rows = []
            for uid in all_users:
                h = all_history.get(uid, [])
                rows.append({
                    "아이디": uid,
                    "총 풀이 횟수": len(h),
                    "평균 점수": f"{sum(a.get('pct',0) for a in h)/len(h):.1f}%" if h else "-",
                    "마지막 접속": max(a.get("ts","") for a in h)[:16] if h else "없음",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with tab2:
        total   = sum(len(v) for v in all_history.values())
        active  = len([u for u,h in all_history.items() if h])
        c1,c2,c3 = st.columns(3)
        c1.metric("전체 가입자",  f"{len(all_users)}명")
        c2.metric("CBT 이용자",   f"{active}명")
        c3.metric("총 풀이 횟수", f"{total}회")
        if all_history:
            chart_df = pd.DataFrame(
                [(u, len(h)) for u,h in all_history.items() if h],
                columns=["유저","풀이 횟수"]
            ).sort_values("풀이 횟수", ascending=False)
            st.bar_chart(chart_df.set_index("유저"))

    with tab3:
        user_list = list(all_users.keys())
        if not user_list:
            st.info("가입된 유저가 없습니다.")
        else:
            sel_user = st.selectbox("유저 선택", user_list)
            uh = all_history.get(sel_user, [])
            if not uh:
                st.info(f"{sel_user}의 풀이 기록이 없습니다.")
            else:
                rows = [{"회차":i, "날짜/시각":a.get("ts","")[:16],
                         "정답률":f"{a.get('pct',0)}%",
                         "총 문항":a.get("total","-"), "정답 수":a.get("correct","-")}
                        for i,a in enumerate(uh,1)]
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
                labels = [f"{r['회차']}회차 ({r['날짜/시각']})" for r in rows]
                sel = st.selectbox("회차 선택 → 틀린 문항", labels, key="admin_sel")
                idx = int(sel.split("회차")[0]) - 1
                wq  = uh[idx].get("wrong_ids", [])
                if wq:
                    st.markdown(f"**틀린 문항 ({len(wq)}개):** {', '.join(wq)}")
                else:
                    st.success("틀린 문항이 없습니다! 🎉")

BRAND = "SaluTerra"
TAGLINE = "강의에서 시험 문항까지 — 조용히, 정확하게"

st.set_page_config(page_title=BRAND, page_icon="🐈", layout="wide")

# ─── 페이지 상태 ───
if "app_page" not in st.session_state:
    st.session_state.app_page = "main"  # "main" | "history"

# ─── Streamlit 기본 chrome 숨김 + 브랜드 헤더 + 고양이 애니메이션 ───
st.markdown("""
<style>
#MainMenu, footer, header [data-testid="stStatusWidget"] { visibility: hidden; }
header { background: transparent; }
.brand-card {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 22px; margin: 4px 0 16px 0;
    background: linear-gradient(135deg, #FAF7F2 0%, #F2EBE0 100%);
    border: 1px solid #E5DCC9; border-radius: 14px;
}
.brand-title { font-size: 26px; font-weight: 700; color: #3B2F1E; letter-spacing: -0.3px; }
.brand-tag   { font-size: 13px; color: #6E5E45; margin-top: 4px; }

/* 고양이 (CSS only, 외부 자산 0) */
.cat-wrap { width: 70px; height: 50px; position: relative; }
.cat {
    position: absolute; bottom: 4px; left: 10px;
    width: 46px; height: 32px;
    background: #6E5E45; border-radius: 18px 22px 14px 14px;
    animation: cat-breathe 2.6s ease-in-out infinite;
}
.cat::before, .cat::after {
    content: ""; position: absolute; top: -7px;
    width: 0; height: 0;
    border-left: 7px solid transparent; border-right: 7px solid transparent;
    border-bottom: 10px solid #6E5E45;
}
.cat::before { left: 4px; } .cat::after { right: 4px; }
.cat-eye {
    position: absolute; top: 11px; width: 4px; height: 4px;
    background: #FAF7F2; border-radius: 50%;
    animation: cat-blink 4s infinite;
}
.cat-eye.l { left: 12px; } .cat-eye.r { right: 12px; }
.cat-tail {
    position: absolute; bottom: 8px; right: -6px;
    width: 18px; height: 6px; background: #6E5E45;
    border-radius: 3px; transform-origin: left center;
    animation: cat-tail 1.8s ease-in-out infinite;
}
@keyframes cat-breathe { 0%,100% {transform: scaleY(1);} 50% {transform: scaleY(1.05);} }
@keyframes cat-blink   { 0%,92%,100% {transform: scaleY(1);} 94%,98% {transform: scaleY(0.1);} }
@keyframes cat-tail    { 0%,100% {transform: rotate(-10deg);} 50% {transform: rotate(20deg);} }
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="brand-card">
  <div>
    <div class="brand-title">{BRAND}</div>
    <div class="brand-tag">{TAGLINE}</div>
  </div>
  <div class="cat-wrap">
    <div class="cat-tail"></div>
    <div class="cat">
      <div class="cat-eye l"></div>
      <div class="cat-eye r"></div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ─── 인증 게이트 (회원가입 + 로그인) ───
import json, hashlib, re as _re
from pathlib import Path as _Path

_USERS_FILE = _Path(USERS_PATH)

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _load_users() -> dict:
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_users(d: dict):
    _USERS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

if "authed" not in st.session_state:
    st.session_state.authed = False
    st.session_state.user = None

# ─── 관리자 분기 (로그인 게이트 이전에 배치 불가 → 인증 후 아래에서 처리) ───

if not st.session_state.authed:
    st.markdown("#### 🔐 로그인이 필요합니다")
    tab_login, tab_signup = st.tabs(["로그인", "회원가입"])

    with tab_login:
        with st.form("login_form"):
            uid = st.text_input("아이디")
            pw = st.text_input("비밀번호 (숫자 4자리)", type="password", max_chars=4)
            ok = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if ok:
            users = _load_users()
            if uid in users and users[uid] == _hash_pw(pw or ""):
                st.session_state.authed = True
                st.session_state.user = uid
                st.rerun()
            else:
                st.error("아이디 또는 비밀번호가 일치하지 않습니다.")

    with tab_signup:
        with st.form("signup_form"):
            new_uid = st.text_input("아이디 (영문/숫자/_ 3~20자)")
            new_pw = st.text_input("비밀번호 (숫자 4자리)", type="password", max_chars=4)
            new_pw2 = st.text_input("비밀번호 확인", type="password", max_chars=4)
            ok2 = st.form_submit_button("회원가입", use_container_width=True)
        if ok2:
            users = _load_users()
            if not _re.fullmatch(r"[A-Za-z0-9_]{3,20}", new_uid or ""):
                st.error("아이디는 영문/숫자/_ 3~20자로 입력하세요.")
            elif new_uid in users:
                st.error("이미 사용 중인 아이디입니다.")
            elif not _re.fullmatch(r"\d{4}", new_pw or ""):
                st.error("비밀번호는 숫자 4자리여야 합니다.")
            elif new_pw != new_pw2:
                st.error("비밀번호가 일치하지 않습니다.")
            else:
                users[new_uid] = _hash_pw(new_pw)
                _save_users(users)
                st.success("✅ 가입 완료. 로그인 탭에서 로그인하세요.")
    st.stop()

# ─── 관리자 로그인 시 대시보드로 분기 ───
if st.session_state.user == ADMIN_ID:
    render_admin_page()
    st.stop()

ANSWER_FORMATS = [
    "1) 오지선다 하나만 고르시오 (단일정답)",
    "2) 가나다라 조합형 (가/가나/가다 식 복합 선지)",
    "3) 오지선다 모두 고르시오 (복수정답)",
    "4) 칠지선다 모두 고르시오 (심화 7선지)",
    "5) 단답형 (1단어~1줄)",
    "6) 약술형 (2~4줄 기전 서술)",
    "7) 서술형 (한 문단 이상)",
]
CONTENT_TYPES = [
    "기본개념형 (정의·분류·단순 기전)",
    "응용개념형 (2단계 추론·교차 비교)",
    "케이스형 (임상 시나리오 통합)",
]

TERM_LANG_OPTIONS = {
    "영어 (myocardial infarction)":            "english",
    "한글 (심근경색)":                          "korean",
    "병기 (심근경색 (myocardial infarction))": "both",
}
TERM_LANG_RULE = {
    "english": "**의학용어는 영어로만 표기**. 한글 번역 병기 금지. 발문·선지·해설 모두 적용. 표준 약어(ACEi, MI, CHF 등) 허용.",
    "korean":  "**의학용어는 한글로만 표기** (KMA 의학용어집 기준). 괄호 안 영문 병기조차 금지. 단, 표준 약자(DNA, RNA, ATP 등 일반화된 것)는 허용.",
    "both":    "**의학용어는 한글(영문) 형태로 병기**. 첫 등장 시 한글 → 괄호 안 영문, 같은 문항 내 반복 시 한글만. 약자(ACEi, MI 등)는 영문 그대로 사용 가능.",
}

# 사이드바: 옵션
with st.sidebar:
    st.caption(f"👤 {st.session_state.user}")
    col_lo, col_hi = st.columns(2)
    with col_lo:
        if st.button("로그아웃", use_container_width=True):
            st.session_state.authed = False
            st.session_state.user = None
            st.rerun()
    with col_hi:
        hist_label = "🏠 메인" if st.session_state.app_page == "history" else "📋 복습 기록"
        if st.button(hist_label, use_container_width=True,
                     type="primary" if st.session_state.app_page == "history" else "secondary"):
            st.session_state.app_page = "history" if st.session_state.app_page == "main" else "main"
            st.rerun()
    st.divider()

    if st.session_state.app_page == "main":
        st.header("옵션")
    else:
        st.header("복습 기록")
        st.caption("위 🏠 메인 버튼으로 돌아가세요.")

if st.session_state.app_page == "main":
    with st.sidebar:
        model = st.selectbox("모델", ["gpt-4o", "gpt-4o-mini", "o4-mini", "gpt-4-turbo", "gpt-4.5-preview", "o3"], index=0)
        st.session_state["_model_sel"] = model

        term_lang_label = st.radio(
            "의학용어 표기",
            list(TERM_LANG_OPTIONS.keys()),
            index=2,
        )
        term_lang = TERM_LANG_OPTIONS[term_lang_label]
        st.markdown("**선지 형식** (복수 선택 가능)")
        _DEFAULT_AF = {0, 1, 2, 4}
        answer_formats = []
        for i, opt in enumerate(ANSWER_FORMATS):
            if st.checkbox(opt, value=(i in _DEFAULT_AF), key=f"af_{i}"):
                answer_formats.append(opt)
        st.markdown("**내용 형식** (복수 선택 가능)")
        content_types = []
        for i, opt in enumerate(CONTENT_TYPES):
            if st.checkbox(opt, value=(i == 0), key=f"ct_{i}"):
                content_types.append(opt)
        num_mcq = st.number_input("본 문항 수", 0, 80, 20)
        difficulty = st.selectbox("난이도", ["표준", "높음 (2단계 추론)", "황세진 교수 수준"])
        extra = st.text_area("추가 지시사항 (선택)", placeholder="예: 학습목표 중심으로 출제해줘")
        st.divider()
        st.markdown("**📁 기출문제 활용**")
        past_exam_files = st.file_uploader(
            "기출문제 파일 (PDF/TXT/MD, 여러 개 가능)",
            type=["pdf", "txt", "md"],
            accept_multiple_files=True,
            key="past_exam",
        )
        generation_mode = st.radio(
            "생성 모드",
            ["새 문제 생성", "기출 변형 생성"],
            horizontal=True,
            key="gen_mode_radio",
        )
        variation_degree = "적당한 변형"
        if generation_mode == "기출 변형 생성":
            variation_degree = st.select_slider(
                "변형 강도",
                options=["유사 변형", "적당한 변형", "창의적 변형"],
                value="적당한 변형",
                key="variation_slider",
            )
else:
    # 복습 페이지에서는 sidebar 옵션 없음 → dummy 값
    answer_formats, content_types = [], []
    num_mcq, difficulty, extra, term_lang = 20, "표준", "", "both"
    generation_mode, variation_degree, past_exam_files = "새 문제 생성", "적당한 변형", []

# ═══════════════════════════════════════════════════
# 📋 복습 기록 페이지
# ═══════════════════════════════════════════════════
if st.session_state.app_page == "history":
    _user = st.session_state.get("user", "")
    _hist = load_history(_user) if _user else []
    st.subheader(f"📋 {_user}님의 CBT 복습 기록")
    if not _hist:
        st.info("아직 제출 기록이 없습니다. CBT 모드에서 문항을 제출하면 자동 저장됩니다.")
        st.stop()
    rows = [{"#": i+1, "날짜": r["ts"], "총 문항": r["total"],
             "정답": r["correct"], "정답률": f"{r['pct']}%",
             "틀린 문항 수": len(r["wrong_ids"])} for i, r in enumerate(_hist)]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown("---")
    st.subheader("🔁 회차 선택 후 오답 재풀이")
    sel_idx = st.selectbox(
        "재풀이할 회차",
        options=list(range(len(_hist))),
        format_func=lambda x: f"#{x+1}  {_hist[x]['ts']}  정답률 {_hist[x]['pct']}%",
        key="history_page_sel",
    )
    sel_rec = _hist[sel_idx]
    if not sel_rec["wrong_ids"]:
        st.success("🎉 이 회차는 모든 문항을 맞혔습니다!")
        st.stop()
    st.markdown(f"**틀린 문항 ({len(sel_rec['wrong_ids'])}개):** {', '.join(sel_rec['wrong_ids'])}")
    # v2.16: 기록에 저장된 full_text 우선 → 없으면 session_state 폴백
    _ft = sel_rec.get("full_text", "") or st.session_state.get("last_full_text", "")
    if not _ft:
        st.warning("⚠️ 이 회차 기록에 문항 데이터가 없습니다 (v2.16 이전 기록). 새로 생성한 세트는 자동 저장됩니다.")
        st.stop()
    review_mode = st.radio(
        "복습 모드", ["CBT - 문항별 즉시 해설", "CBT - 전체 제출 후 해설"],
        horizontal=True, key="history_review_mode",
    )
    if st.button("🔁 오답 재풀이 시작", type="primary", use_container_width=True,
                 key=f"hist_start_{sel_idx}"):
        all_qs = parse_cbt_questions(_ft)
        wrong_qs = get_wrong_questions(all_qs, sel_rec["wrong_ids"])
        st.session_state["review_qs"] = wrong_qs
        st.session_state["review_rec_ts"] = sel_rec["ts"]
        st.rerun()
    if st.session_state.get("review_qs"):
        rqs = st.session_state["review_qs"]
        rts = st.session_state.get("review_rec_ts", "복습")
        st.markdown("---")
        st.subheader(f"🔁 오답 재풀이 — {rts} ({len(rqs)}문항)")
        render_cbt(
            rqs,
            mode='per_q' if '문항별' in review_mode else 'submit_all',
            session_prefix=f"review_{rts.replace(' ', '_').replace(':', '')}",
            user=_user,
        )
    st.stop()

# ═══════════════════════════════════════════════════
# 🏠 메인 페이지
# ═══════════════════════════════════════════════════
# 유효성 안내
if not answer_formats:
    st.warning("⚠️ 사이드바에서 **선지 형식**을 1개 이상 선택하세요.")
if not content_types:
    st.warning("⚠️ 사이드바에서 **내용 형식**을 1개 이상 선택하세요.")

# ─── 결과 보기 방식 (생성 전 미리 선택) ───
display_mode = st.radio(
    "결과 보기 방식",
    ["미리보기 (Notion 토글 형식)", "CBT - 문항별 즉시 해설", "CBT - 전체 제출 후 해설"],
    horizontal=True,
    key="display_mode_radio",
)

# 본문: 파일 업로드
col1, col2 = st.columns(2)
with col1:
    st.subheader("📚 강의 자료")
    lecture_files = st.file_uploader(
        "PDF / PPTX / TXT (여러 개 가능)",
        type=["pdf", "pptx", "txt", "md"],
        accept_multiple_files=True, key="lecture",
    )
with col2:
    st.subheader("🎙️ 강의 전사본")
    _tc1, _tc2 = st.tabs(["📁 파일 업로드", "✏️ 텍스트 직접 입력"])
    with _tc1:
        transcript_files = st.file_uploader(
            "TXT / MD (여러 개 가능)",
            type=["txt", "md"],
            accept_multiple_files=True, key="transcript",
        )
    with _tc2:
        st.text_area(
            "전사본 붙여넣기",
            height=200,
            placeholder="강의 전사본 텍스트를 여기에 복사해서 붙여넣으세요...",
            key="transcript_direct_input",
        )

lecture_text = "\n\n".join(extract_text(f) for f in lecture_files) if lecture_files else ""
_t_file = "\n\n".join(extract_text(f) for f in transcript_files) if transcript_files else ""
_t_direct = st.session_state.get("transcript_direct_input", "").strip()
transcript_text = "\n\n".join(filter(None, [_t_file, _t_direct]))

if lecture_text or transcript_text:
    tokens_in = estimate_tokens(lecture_text + transcript_text)
    cost = tokens_in / 1_000_000 * 2.5 + 12000 / 1_000_000 * 10
    st.info(f"📊 강의 자료 {len(lecture_text):,}자 · 전사본 {len(transcript_text):,}자 · 입력 토큰 ≈ {tokens_in:,} · gpt-4o 예상 ≈ ${cost:.3f}")

    with st.expander("📄 추출된 텍스트 미리보기"):
        if transcript_text:
            st.text_area("전사본 (앞 3000자)", transcript_text[:3000], height=200)
        if lecture_text:
            st.text_area("강의 자료 (앞 3000자)", lecture_text[:3000], height=200)

# ── 기출문제 텍스트 추출 + 변형 모드 결정 ──
past_exam_text_raw = "\n\n".join(extract_text(f) for f in past_exam_files) if past_exam_files else ""
variation_mode = variation_degree if (generation_mode == "기출 변형 생성" and past_exam_text_raw) else ""


# ── 기출문제 CBT 직접 풀기 변환 함수 ──
def _convert_past_exam_to_cbt(text: str, mdl: str = "gpt-4o") -> str:
    """기출문제 텍스트를 parse_cbt_questions() 파싱 가능한 마크다운으로 AI 변환."""
    from openai import OpenAI
    _client = OpenAI(api_key=get_secret("OPENAI_API_KEY"))
    _sys = (
        "당신은 시험 문제를 Notion 마크다운 CBT 형식으로 변환하는 전문가입니다.\n"
        "아래 규칙을 정확히 따르시오:\n\n"
        "각 문항을 다음 구조로 변환:\n"
        "<details>\n"
        "<summary>**문제 N.** [발문 전체]</summary>\n\n"
        "   ① 선지1\n   ② 선지2\n   ③ 선지3\n   ④ 선지4\n   ⑤ 선지5\n\n"
        "<details>\n"
        "<summary>✅ 정답 확인</summary>\n\n"
        "✅ **정답: ①** (정답을 알 수 없으면 '정답 미제공')\n"
        "💡 **해설:** [해설이 있으면 기재, 없으면 '해설 없음']\n\n"
        "</details>\n"
        "</details>\n\n"
        "규칙:\n"
        "- 문제 번호는 '문제 1', '문제 2' ... 순으로 통일\n"
        "- 선지는 반드시 ①②③④⑤ 원문자 prefix 붙이기\n"
        "- 선지 없는 주관식은 선지 부분 생략 (정답 토글만 유지)\n"
        "- 원본 문항 내용을 그대로 유지 (내용 수정 금지)\n"
        "- 전체 출력을 ```코드블록```으로 감싸지 말 것"
    )
    _resp = _client.chat.completions.create(
        model=mdl,
        messages=[
            {"role": "system", "content": _sys},
            {"role": "user", "content": f"다음 문제를 변환하시오:\n\n{text[:12000]}"},
        ],
        temperature=0.1,
        max_tokens=16000,
    )
    return _resp.choices[0].message.content


# ── 기출문제 업로드 시: 미리보기 + CBT 직접 풀기 섹션 ──
if past_exam_text_raw and st.session_state.app_page == "main":
    with st.expander("📋 기출문제 미리보기 & CBT 직접 풀기", expanded=False):
        st.text_area("추출된 기출문제 텍스트 (앞 2000자)", past_exam_text_raw[:2000], height=130)
        st.markdown(f"📊 총 **{len(past_exam_text_raw):,}자** 추출됨")
        _cbt_col1, _cbt_col2 = st.columns([2, 1])
        with _cbt_col1:
            cbt_mode_past = st.radio(
                "CBT 모드",
                ["CBT - 문항별 즉시 해설", "CBT - 전체 제출 후 해설"],
                horizontal=True,
                key="past_cbt_mode",
            )
        with _cbt_col2:
            if st.button("🔄 기출문제 CBT 변환 & 풀기", type="primary",
                         use_container_width=True, key="past_cbt_btn"):
                with st.spinner("AI가 기출문제를 CBT 형식으로 변환 중..."):
                    try:
                        _converted = _convert_past_exam_to_cbt(
                            past_exam_text_raw,
                            mdl=st.session_state.get("_model_sel", "gpt-4o"),
                        )
                        st.session_state["past_cbt_text"] = _converted
                    except Exception as _e:
                        st.error(f"변환 실패: {_e}")
        if st.session_state.get("past_cbt_text"):
            _past_qs = parse_cbt_questions(st.session_state["past_cbt_text"])
            if _past_qs:
                st.success(f"✅ {len(_past_qs)}문항 파싱 완료 — 바로 풀어보세요")
                render_cbt(
                    _past_qs,
                    mode='per_q' if '문항별' in cbt_mode_past else 'submit_all',
                    session_prefix="past_cbt",
                    user=st.session_state.get("user", ""),
                )
            else:
                st.warning("문항 파싱에 실패했습니다. 텍스트 형식을 확인하거나 다시 시도하세요.")
                with st.expander("변환 결과 원문 보기"):
                    st.text(st.session_state["past_cbt_text"][:3000])


# 생성 버튼
can_run = bool((lecture_text or transcript_text) and answer_formats and content_types)
if st.button("문항 세트 생성", type="primary", use_container_width=True, disabled=not can_run):
    full_text = ""
    progress = st.empty()

    with st.spinner(f"{model} 작업 중 (40문항 기준 1\\~3분)..."):
        try:
            for delta, full in generate_questions(
                lecture_text, transcript_text,
                answer_formats=answer_formats,
                content_types=content_types,
                num_mcq=num_mcq, num_short=0,
                difficulty=difficulty,
                extra_instructions=extra,
                term_lang_rule=TERM_LANG_RULE[term_lang],
                past_exam_text=past_exam_text_raw,
                variation_mode=variation_mode,
                model=model,
            ):
                full_text = full
                # 생성 과정은 글자 수만 표시 — 본문 실시간 노출 X
                progress.caption(f"⏳ 생성 중... {len(full_text):,}자")
        except Exception as e:
            st.error(f"생성 실패: {e}")
            st.stop()
    progress.empty()

    # 모델이 원문자 prefix(①②③…)를 누락한 경우 안전망 — 자동 번호 매김
    full_text = autonumber_choices(full_text)

    st.markdown("---")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{BRAND}_{ts}"
    af_label = " + ".join(x.split(")")[0] + ")" for x in answer_formats)
    ct_label = " + ".join(x.split(" (")[0] for x in content_types)

    # PDF를 먼저 렌더링해서 결과 본문 위에 다운로드 버튼 배치
    pdf_bytes = build_pdf(full_text, title=f"{BRAND} 문항 세트 — {af_label} / {ct_label}")

    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.success(f"✅ 생성 완료 — {len(full_text):,}자")
    with top_r:
        st.download_button(
            "📄 PDF 다운로드",
            data=pdf_bytes,
            file_name=f"{base}.pdf", mime="application/pdf",
            help="문제지 + 정답·해설 분리",
            type="primary",
            use_container_width=True,
        )

    # CBT 모드에서는 생성 결과 원문 숨김 (CBT UI가 대신 표시)
    if st.session_state.get("last_display_mode", display_mode) == "미리보기 (Notion 토글 형식)":
        st.subheader("📝 생성 결과")
        st.markdown(full_text, unsafe_allow_html=True)

    # session_state에 저장 (CBT 모드 리렌더 대응)
    st.session_state.last_full_text = full_text
    st.session_state.last_ts = ts
    st.session_state.last_labels = (af_label, ct_label)
    st.session_state.last_display_mode = st.session_state.get("display_mode_radio", "미리보기 (Notion 토글 형식)")
    for _k in list(st.session_state.keys()):
        if _k.startswith("cbt_"):
            del st.session_state[_k]
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    with open(os.path.join(OUTPUTS_DIR, f"{BRAND}_{ts}.md"), "w", encoding="utf-8") as f:
        f.write(full_text)
    with open(os.path.join(OUTPUTS_DIR, f"{base}.pdf"), "wb") as f:
        f.write(pdf_bytes)

# --- 결과 표시 (session_state 기반 — CBT 리렌더 후에도 유지) ---
if st.session_state.get("last_full_text"):
    _ft = st.session_state.last_full_text
    _ts = st.session_state.last_ts
    _al, _cl = st.session_state.last_labels
    # display_mode는 이미 위에서 라디오로 선택됨 (생성 전 선택값 사용)
    display_mode = st.session_state.get("last_display_mode",
                   st.session_state.get("display_mode_radio", "미리보기 (Notion 토글 형식)"))
    if display_mode == "미리보기 (Notion 토글 형식)":
        st.subheader("생성 결과")
        st.markdown(_ft, unsafe_allow_html=True)
    else:
        cbt_qs = parse_cbt_questions(_ft)
        cbt_mode_val = 'per_q' if '문항별' in display_mode else 'submit_all'
        st.subheader(f"CBT 모드 ({len(cbt_qs)}문항 파싱됨)")
        render_cbt(cbt_qs, mode=cbt_mode_val, session_prefix=f"cbt_{_ts}",
                   user=st.session_state.get("user", ""))
