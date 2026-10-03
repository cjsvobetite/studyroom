"""CBT mode — 문항별 1개씩 표시 + 상단 번호 네비게이션 + 주관식 직접 입력.

v2.14 신규:
- 제출 완료 시 history.save_attempt() 호출 → cbt_history.json에 유저별 기록 저장.
- _show_score()에 '틀린 문항만 다시 풀기' 버튼 추가 → 오답만 모아 render_cbt() 재실행.

v2.13 신규:
- CBT 제출 완료 후 점수 패널 하단에 📄 PDF 다운로드 버튼 추가.
  build_pdf()를 cbt.py에서 직접 호출. session_state.last_full_text 기반.

v2.12 신규:
- 🔖 플래그 기능: 문항마다 '나중에 확인' 토글. 플래그된 문항은 상단에 목록 표시.
- 번호 버튼: 미답=회색(secondary), 답변=✓N(secondary), 플래그=🔖N(secondary), 현재=primary.
- 미완료 제출 허용: 미답 문항은 오답으로 처리되고 그냥 제출 가능.

v2.11 성능 최적화:
- render_cbt()에 @st.fragment 적용 → 버튼 클릭 시 앱 전체가 아닌 CBT 영역만 재렌더.
  네비게이션 속도 대폭 향상 + 화면 흐려짐(greying-out) 완전 제거.
- st.rerun() 제거 → fragment 내부는 state 변경 후 자동 재렌더되므로 불필요.
"""
import re
import streamlit as st
from pdf_export import build_pdf
from history import save_attempt, get_wrong_questions

_CIRCLES = "①②③④⑤⑥⑦⑧⑨⑩"
_CIRCLE_IDX = {c: i for i, c in enumerate(_CIRCLES)}
# v2.15 — 조합형 답 선지 ⓐⓑⓒⓓⓔ 지원
_COMBO_LETTERS = "ⓐⓑⓒⓓⓔⓕⓖⓗ"
_COMBO_LETTER_IDX = {c: i for i, c in enumerate(_COMBO_LETTERS)}
_COMBO_LINE_RE = re.compile(r'[ⓐⓑⓒⓓⓔⓕⓖⓗ]')
_SUMMARY_RE = re.compile(r'<summary>(.*?)</summary>', re.DOTALL)
_QID_RE = re.compile(r'\*\*((?:문제|Q|C)\s*\d+\.?)\*\*\s*(.*)', re.DOTALL)
_ANS_RE = re.compile(r'✅\s*\*\*정답\s*[:：]?\s*(.*?)\*\*')


def _choice_idx(tok):
    tok = tok.strip()
    if tok and tok[0] in _CIRCLE_IDX:
        return _CIRCLE_IDX[tok[0]]
    # v2.15: 조합형 ⓐⓑⓒⓓⓔ → 인덱스
    if tok and tok[0] in _COMBO_LETTER_IDX:
        return _COMBO_LETTER_IDX[tok[0]]
    m = re.match(r'^\((\d+)\)', tok)
    if m:
        return int(m.group(1)) - 1
    return None


_GANA_LABELS = ['가', '나', '다', '라', '마', '바', '사', '아', '자', '차']
_GANA_PREFIX_RE = re.compile(r'^\([가나다라마바사아자차]\)')
# v2.16: 조합형 답 선지 ①②→가, 나 변환
_CIRCLE_TO_GANA = {c: _GANA_LABELS[i] for i, c in enumerate(_CIRCLES[:10])}


def _fmt_combo_opt(text: str) -> str:
    """①②③ → 가, 나, 다 형태로 변환. 예: '①③④' → '가, 다, 라'"""
    parts = [_CIRCLE_TO_GANA[ch] for ch in text if ch in _CIRCLE_TO_GANA]
    return ', '.join(parts) if parts else text

def parse_cbt_questions(md):
    """마크다운에서 CBT용 문항 파싱 (선택형 + 주관식 모두).
    반환: [{id, stem, choices, answers, explanation, is_subjective}]
    v2.16: 조합형 본선지 텍스트 → stem에 포함, choices = combo 선지만.
    """
    questions = []
    lines = md.split('\n')
    n = len(lines)
    i = 0
    while i < n:
        if '<details>' not in lines[i]:
            i += 1
            continue
        summary_text = ''
        for j in range(i, min(n, i + 6)):
            sm = _SUMMARY_RE.search(lines[j])
            if sm:
                summary_text = sm.group(1)
                break
        qm = _QID_RE.search(summary_text)
        if not qm:
            i += 1
            continue
        qid = qm.group(1).rstrip('.')
        stem = re.sub(r'\*+', '', qm.group(2)).strip()
        choices, answers, expl_lines = [], [], []
        pre_combo_buf = []   # v2.16: 조합형 본선지 임시 버퍼
        depth, in_answer = 1, False
        k = i + 2
        while k < n and depth > 0:
            l = lines[k]
            ls = l.strip()
            if '<details>' in ls:
                depth += 1
                sm2 = _SUMMARY_RE.search(ls)
                if sm2 and '정답' in sm2.group(1):
                    in_answer = True
                k += 1; continue
            if '</details>' in ls:
                depth -= 1
                if depth == 1: in_answer = False
                if depth == 0: break
                k += 1; continue
            sm2 = _SUMMARY_RE.search(l)
            if sm2:
                if '정답' in sm2.group(1): in_answer = True
                k += 1; continue
            if not in_answer:
                # v2.16 fix: 빈 줄은 pre_combo_buf를 flush하지 않고 무시
                if not ls:
                    k += 1
                    continue
                is_choice_line = bool(
                    ls and (ls[0] in _CIRCLE_IDX
                            or re.match(r'^\(\d+\)', ls)
                            or _GANA_PREFIX_RE.match(ls))
                )
                if is_choice_line:
                    # 일단 버퍼에 저장 — combo 라인이 뒤에 오면 stem으로, 아니면 choices로
                    pre_combo_buf.append(ls)
                elif _COMBO_LINE_RE.search(ls):
                    # v2.16: 조합형 답 선지 라인 ⓐ ... ⓑ ... → combo choices
                    combo_opts = re.findall(
                        r'[ⓐⓑⓒⓓⓔⓕⓖⓗ]\s*(.+?)(?=\s*[ⓐⓑⓒⓓⓔⓕⓖⓗ]|$)', ls
                    )
                    combo_opts = [_fmt_combo_opt(o.strip()) for o in combo_opts if o.strip()]
                    if combo_opts and pre_combo_buf:
                        # 본선지 텍스트 → stem에 가나다라 형태로 추가
                        stem_items = []
                        for _gi, _pc in enumerate(pre_combo_buf):
                            _body = re.sub(
                                r'^\([가나다라마바사아자차]\)\s*|^[①-⑳]\s*|^\(\d+\)\s*', '', _pc
                            ).strip()
                            _lbl = _GANA_LABELS[_gi] if _gi < len(_GANA_LABELS) else str(_gi+1)
                            stem_items.append(f"({_lbl}) {_body}")
                        stem += '\n' + '\n'.join(stem_items)
                        pre_combo_buf = []
                        choices = combo_opts
                    elif combo_opts:
                        choices = combo_opts  # pre_combo_buf 없는 경우 (이미 변환됨)
                else:
                    # 일반 비선지 라인 — pre_combo_buf 있으면 일반 choices로 flush
                    if pre_combo_buf:
                        for _pc in pre_combo_buf:
                            _body = re.sub(
                                r'^[①-⑳]\s*|^\(\d+\)\s*|^\([가나다라마바사아자차]\)\s*', '', _pc
                            ).strip()
                            choices.append(_body)
                        pre_combo_buf = []
                    # v2.16: 선지 수집 전 텍스트 (케이스 증례 설명·발문 확장) → stem에 추가
                    if not choices and ls and not ls.startswith('<') and not ls.startswith('✅'):
                        _stem_add = re.sub(r'^>\s*', '', ls).strip()  # blockquote '>' prefix 제거
                        _stem_add = re.sub(r'\*\*(.*?)\*\*', r'\1', _stem_add)  # **bold** → plain
                        if _stem_add:
                            stem += '\n' + _stem_add
            else:
                am = _ANS_RE.search(l)
                if am:
                    for tok in re.split(r'[,，\s]+', am.group(1)):
                        idx = _choice_idx(tok.strip())
                        if idx is not None: answers.append(idx)
                elif ls and not ls.startswith('<') and not ls.startswith('✅'):
                    expl_lines.append(re.sub(r'\*+', '', ls))
            k += 1
        # 루프 종료 후 남은 pre_combo_buf → choices
        if pre_combo_buf:
            for _pc in pre_combo_buf:
                _body = re.sub(
                    r'^[①-⑳]\s*|^\(\d+\)\s*|^\([가나다라마바사아자차]\)\s*', '', _pc
                ).strip()
                choices.append(_body)
        if stem:
            questions.append({
                'id': qid, 'stem': stem, 'choices': choices,
                'answers': sorted(set(answers)),
                'explanation': '\n'.join(e for e in expl_lines if e).strip(),
                'is_subjective': len(choices) == 0,
            })
        i = k + 1
    return questions


def _set_cur(prefix, idx):
    st.session_state[f"{prefix}_cur"] = idx


def _show_result(q, ua):
    """선택형 정오 표시."""
    if not q['answers']:
        return
    ok = sorted(q['answers']) == sorted(ua)
    if ok:
        st.success("✅ 정답!")
    else:
        ans_str = ', '.join(f"({i+1})" for i in q['answers'])
        st.error(f"❌ 오답 — 정답: **{ans_str}**")
    if q['explanation']:
        with st.expander("📖 해설"):
            st.markdown(q['explanation'])


def _show_score(questions, user_ans, prefix, user=""):
    obj_qs = [q for q in questions if not q['is_subjective']]
    correct = sum(
        1 for q in obj_qs
        if q['answers'] and sorted(q['answers']) == sorted(
            user_ans.get(q['id']) if isinstance(user_ans.get(q['id']), list) else []
        )
    )
    denom = len(obj_qs) if obj_qs else 1
    pct = int(correct / denom * 100)
    st.metric("🎯 점수 (객관식)", f"{correct} / {denom}  ({pct}%)",
              delta="Pass ✅" if pct >= 60 else "Fail ❌")
    if pct >= 80:
        st.balloons()

    # ── v2.14: 풀이 기록 저장 ──
    if user and not st.session_state.get(f"{prefix}_saved"):
        try:
            record = save_attempt(user, questions, user_ans,
                                       full_text=st.session_state.get("last_full_text", ""))
            st.session_state[f"{prefix}_saved"] = True
            st.session_state[f"{prefix}_wrong_ids"] = record["wrong_ids"]
            st.caption(f"📝 기록 저장됨 — {record['ts']}")
        except Exception as _e:
            st.caption(f"기록 저장 실패: {_e}")

    # ── 틀린 문항만 다시 풀기 (v2.14) ──
    wrong_ids = st.session_state.get(f"{prefix}_wrong_ids", [])
    if wrong_ids:
        st.warning(f"❌ 틀린 문항: {', '.join(wrong_ids)} ({len(wrong_ids)}개)")
        if st.button("🔁 틀린 문항만 다시 풀기", key=f"{prefix}_retry_wrong", use_container_width=True):
            wrong_qs = get_wrong_questions(questions, wrong_ids)
            retry_prefix = f"{prefix}_retry"
            for k in list(st.session_state.keys()):
                if k.startswith(retry_prefix):
                    del st.session_state[k]
            st.session_state[f"{prefix}_retry_qs"] = wrong_qs
            st.rerun()

    # ── CBT 완료 후 PDF 다운로드 (v2.13) ──
    raw_md = st.session_state.get("last_full_text", "")
    if raw_md:
        try:
            pdf_bytes = build_pdf(raw_md, title="SaluTerra CBT 결과")
            st.download_button(
                "📄 PDF 다운로드 (문제지 + 정답·해설)",
                data=pdf_bytes,
                file_name=f"cbt_result_{prefix}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                key=f"{prefix}_pdf_dl",
            )
        except Exception as _e:
            st.warning(f"PDF 생성 실패: {_e}")

    # 다시 풀기 — fragment 밖의 전역 state를 초기화하므로 st.rerun() 필요
    if st.button("🔄 처음부터 다시 풀기", key=f"{prefix}_reset"):
        for k in list(st.session_state.keys()):
            if k.startswith(prefix):
                del st.session_state[k]
        st.rerun()


@st.fragment
def render_cbt(questions, mode, session_prefix, user=""):
    """CBT UI — 문항 1개씩 표시, 상단 번호 버튼 네비게이션.

    @st.fragment: 이 함수 내부의 위젯 변경은 전체 앱을 재실행하지 않고
    이 fragment만 재실행 → 네비게이션이 즉각적이고 화면이 흐려지지 않음.

    mode: 'per_q' | 'submit_all'
    """
    if not questions:
        st.info("ℹ️ 파싱된 문항이 없습니다.")
        return

    total = len(questions)
    cur_key   = f"{session_prefix}_cur"
    ans_key   = f"{session_prefix}_ans"
    shown_key = f"{session_prefix}_shown"

    flags_key = f"{session_prefix}_flags"

    if cur_key   not in st.session_state: st.session_state[cur_key]   = 0
    if ans_key   not in st.session_state: st.session_state[ans_key]   = {}
    if shown_key not in st.session_state: st.session_state[shown_key] = set()
    if flags_key not in st.session_state: st.session_state[flags_key] = set()

    cur      = st.session_state[cur_key]
    user_ans = st.session_state[ans_key]
    shown    = st.session_state[shown_key]
    flags    = st.session_state[flags_key]

    answered_count = sum(1 for v in user_ans.values() if v not in (None, [], ""))
    st.markdown(f"**총 {total}문항** · 답변: {answered_count}/{total}")
    st.progress(answered_count / total if total else 0)

    # ── 플래그 목록 (상단) ──
    if flags:
        flagged_nums = ', '.join(f"{i+1}번" for i in sorted(flags))
        st.warning(f"🔖 **나중에 확인**: {flagged_nums}")

    # ── 상단 번호 버튼 네비게이션 (10개씩 한 줄) ──
    # 미답=회색(secondary), 답변=✓N(secondary), 플래그=🔖N(secondary), 현재=primary
    COLS_PER_ROW = 10
    for row_start in range(0, total, COLS_PER_ROW):
        row_qs = list(range(row_start, min(row_start + COLS_PER_ROW, total)))
        cols = st.columns(len(row_qs))
        for ci, idx in enumerate(row_qs):
            q_nav = questions[idx]
            ua_nav = user_ans.get(q_nav['id'])
            answered = ua_nav not in (None, [], "")
            is_flagged = idx in flags
            is_current = idx == cur
            if is_current:
                label = f"**{idx+1}**"
            elif is_flagged:
                label = f"🔖{idx+1}"
            elif answered:
                label = f"✓{idx+1}"
            else:
                label = str(idx+1)
            cols[ci].button(
                label,
                key=f"{session_prefix}_nav_{idx}",
                on_click=_set_cur, args=(session_prefix, idx),
                type="primary" if is_current else "secondary",
                use_container_width=True,
            )

    st.divider()

    # ── 현재 문항 ──
    q   = questions[cur]
    qid = q['id']
    ua  = user_ans.get(qid)
    answer_revealed = cur in shown

    # 🔖 플래그 토글 버튼 (문항 제목 옆)
    flag_label = "🔖 확인 취소" if cur in flags else "🔖 나중에 확인"
    def _toggle_flag():
        if cur in flags:
            flags.discard(cur)
        else:
            flags.add(cur)
        st.session_state[flags_key] = flags
    col_stem, col_flag = st.columns([5, 1])
    with col_stem:
        # v2.16: stem에 '\n' 포함 시 (케이스형 증례 설명 등) 제목과 본문 분리 렌더링
        _stem_parts = q['stem'].split('\n', 1)
        st.markdown(f"### {cur+1}. {_stem_parts[0]}")
        if len(_stem_parts) > 1 and _stem_parts[1].strip():
            st.markdown(_stem_parts[1].replace('\n', '  \n'), unsafe_allow_html=False)
    with col_flag:
        st.button(flag_label, key=f"{session_prefix}_flag_{cur}",
                  on_click=_toggle_flag, use_container_width=True)

    if q['is_subjective']:
        # 주관식 — 직접 타이핑
        # on_change 콜백으로 session_state에 저장 (rerun 없이)
        def _save_subj():
            user_ans[qid] = st.session_state[f"{session_prefix}_subj_{qid}"]
            st.session_state[ans_key] = user_ans

        prev_text = ua if isinstance(ua, str) else ""
        st.text_area(
            "답 입력", value=prev_text,
            key=f"{session_prefix}_subj_{qid}",
            height=120, placeholder="답을 직접 입력하세요...",
            on_change=_save_subj,
        )

        if q['explanation']:
            if not answer_revealed:
                def _reveal_subj():
                    shown.add(cur)
                    st.session_state[shown_key] = shown
                st.button("정답·해설 확인",
                          key=f"{session_prefix}_check_{cur}",
                          on_click=_reveal_subj)
            else:
                with st.expander("📖 모범 답안 / 해설", expanded=True):
                    st.markdown(q['explanation'])

    elif len(q['answers']) != 1:
        # 복수정답 — 체크박스
        sel = list(ua) if isinstance(ua, list) else []
        new_sel = []
        for ci, choice in enumerate(q['choices']):
            if st.checkbox(f"({ci+1}) {choice}", value=(ci in sel),
                           key=f"{session_prefix}_cb_{qid}_{ci}"):
                new_sel.append(ci)
        user_ans[qid] = new_sel
        st.session_state[ans_key] = user_ans

        if mode == 'per_q' and new_sel:
            _show_result(q, new_sel)
        elif mode == 'submit_all' and answer_revealed:
            _show_result(q, new_sel)

    else:
        # 단일정답 — 라디오
        labels   = [f"({ci+1}) {c}" for ci, c in enumerate(q['choices'])]
        prev_idx = ua[0] if isinstance(ua, list) and ua else None
        sel_r = st.radio(
            "선택", options=list(range(len(labels))),
            format_func=lambda x: labels[x],
            index=prev_idx,
            key=f"{session_prefix}_r_{qid}",
            label_visibility="collapsed",
        )
        new_ua = [sel_r] if sel_r is not None else []
        user_ans[qid] = new_ua
        st.session_state[ans_key] = user_ans

        if mode == 'per_q' and new_ua:
            _show_result(q, new_ua)
        elif mode == 'submit_all' and answer_revealed:
            _show_result(q, new_ua)

    st.divider()

    # ── 이전 / 다음 버튼 ──
    nav_l, _, nav_r = st.columns([1, 3, 1])
    with nav_l:
        if cur > 0:
            def _go_prev():
                st.session_state[cur_key] = cur - 1
            st.button("← 이전", key=f"{session_prefix}_prev",
                      on_click=_go_prev, use_container_width=True)
    with nav_r:
        if cur < total - 1:
            def _go_next():
                st.session_state[cur_key] = cur + 1
            st.button("다음 →", key=f"{session_prefix}_next",
                      on_click=_go_next, type="primary",
                      use_container_width=True)
        elif mode == 'submit_all':
            unanswered = total - answered_count
            def _submit_all():
                for idx2 in range(total):
                    shown.add(idx2)
                st.session_state[shown_key] = shown
            st.button("📝 최종 제출", type="primary",
                      key=f"{session_prefix}_submit_btn",
                      on_click=_submit_all,
                      use_container_width=True)
            if unanswered > 0:
                st.caption(f"⚠️ 미답 {unanswered}문항은 오답 처리됩니다.")

    # ── 오답 재풀이 모드 (v2.14) ──
    retry_qs = st.session_state.get(f"{session_prefix}_retry_qs")
    if retry_qs:
        st.markdown("---")
        st.subheader(f"🔁 오답 재풀이 ({len(retry_qs)}문항)")
        render_cbt(retry_qs, mode=mode,
                   session_prefix=f"{session_prefix}_retry", user=user)
        return

    # ── 점수 패널 ──
    if mode == 'submit_all' and len(shown) >= total:
        st.markdown("---")
        _show_score(questions, user_ans, session_prefix, user=user)
    elif mode == 'per_q' and answered_count > 0:
        with st.expander(f"📊 현재 점수 ({answered_count}/{total})", expanded=False):
            _show_score(questions, user_ans, session_prefix, user=user)
