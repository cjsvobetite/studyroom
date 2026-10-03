import os as _os, re
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

from config import ensure_fonts

try:
    _REGULAR, _BOLD = ensure_fonts()
    pdfmetrics.registerFont(TTFont("NanumGothic", _REGULAR))
    if _os.path.exists(_BOLD):
        pdfmetrics.registerFont(TTFont("NanumGothic-Bold", _BOLD))
    else:
        pdfmetrics.registerFont(TTFont("NanumGothic-Bold", _REGULAR))
    registerFontFamily("NanumGothic",
                       normal="NanumGothic", bold="NanumGothic-Bold",
                       italic="NanumGothic", boldItalic="NanumGothic-Bold")
    _FONT = "NanumGothic"
except Exception as _e:  # 폰트 다운로드 실패 시 ReportLab 내장 한글 CID 폰트 사용
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    print(f"⚠️ NanumGothic 로드 실패 ({_e}) → HYGothic-Medium 대체")
    _FONT = "HYGothic-Medium"
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
    registerFontFamily(_FONT, normal=_FONT, bold=_FONT, italic=_FONT, boldItalic=_FONT)

# v2.9 — 원문자(①-⑳)를 (1)(2)(3)… 텍스트로 치환 (폰트 없이 PDF 렌더)
# DejaVu TTF 취득 방법(wget/shutil/apt) 모두 ZIP 반환 문제로 불채택 → 텍스트 치환으로 확실하게 해결
_CIRCLE_MAP = {chr(0x2460 + i): f'({i + 1})' for i in range(20)}

def _wrap_circles(text: str) -> str:
    """원문자 ①-⑳ → (1)(2)(3)… 텍스트 치환. NanumGothic에 해당 글리프 없어 빈칸 렌더되던 문제 해결."""
    for c, repl in _CIRCLE_MAP.items():
        text = text.replace(c, repl)
    return text


# ── v2.10: 그리스 문자 + 특수기호 → ASCII 치환 (NanumGothic 미지원) ──
_GREEK_MAP = {
    'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'ε': 'epsilon',
    'ζ': 'zeta', 'η': 'eta', 'θ': 'theta', 'κ': 'kappa', 'λ': 'lambda',
    'μ': 'mu', 'ν': 'nu', 'π': 'pi', 'ρ': 'rho', 'σ': 'sigma',
    'τ': 'tau', 'φ': 'phi', 'χ': 'chi', 'ψ': 'psi', 'ω': 'omega',
    'Δ': 'Delta', 'Σ': 'Sigma', 'Π': 'Pi', 'Λ': 'Lambda', 'Ω': 'Omega',
    '°': 'deg', '±': '+/-', '×': 'x',
}


def _fix_special(text: str) -> str:
    """그리스 문자·특수기호를 PDF 렌더 가능한 ASCII로 치환."""
    for ch, name in _GREEK_MAP.items():
        text = text.replace(ch, name)
    return text


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"],   fontName=_FONT, fontSize=18, leading=24, spaceAfter=12),
        "h2":    ParagraphStyle("h2",    parent=base["Heading2"], fontName=_FONT, fontSize=14, leading=20, spaceAfter=8),
        "h3":    ParagraphStyle("h3",    parent=base["Heading3"], fontName=_FONT, fontSize=12, leading=18, spaceAfter=6),
        "body":  ParagraphStyle("body",  parent=base["Normal"],   fontName=_FONT, fontSize=10.5, leading=16),
        "small": ParagraphStyle("small", parent=base["Normal"],   fontName=_FONT, fontSize=9, leading=12, textColor="#666666"),
    }


def _md_inline(line: str) -> str:
    # 상이한 < > & 는 먼저 이스케이프 (ReportLab Paragraph은 XML을 파싱)
    line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out, parts, bold = "", line.split("**"), False
    for p in parts:
        out += ("<b>" + p + "</b>") if bold else p
        bold = not bold
    # v2.10 — 원문자 치환 + 그리스 문자 ASCII 변환
    return _fix_special(_wrap_circles(out))


_QID_PAT = re.compile(r"\*\*((?:문제|Q|S|C)\s*\d+\.?)\*\*")
_SUMMARY_PAT = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)

_CIRCLES = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# v2.7 — 어떤 형식의 선지 prefix이든 잡아서 떼어내는 패턴 (이미 있어도 무조건 재번호)
_STRIP_PREFIX_RE = re.compile(
    r"^(\s*)"
    r"(?:"
    r"[①-⑳]"               # 원문자
    r"|\([가-하]\)"          # (가)(나)(다)
    r"|[가-하][\.\)]"        # 가. 나)
    r"|\(\d+\)"              # (1)(2)
    r"|\d+[\.\)]"            # 1. 1)
    r"|[A-Ea-e][\.\)]"       # A. a)
    r"|[-•*]"                # - • *
    r")\s*"
)

_Q_HEADER_RE = re.compile(r"문제\s*\d+")


def _is_question_header(line: str) -> bool:
    return bool(_Q_HEADER_RE.search(line.strip().strip("*").strip()))


def _ends_question_body(line: str) -> bool:
    s = line.rstrip().rstrip("*").rstrip()
    if not s:
        return False
    return s.endswith("?") or s.endswith("？") \
           or "고르시오" in s[-15:] or "쓰시오" in s[-15:] \
           or "무엇인가" in s[-15:]


def _is_blocking(line: str) -> bool:
    """선지로 절대 간주하지 않을 라인."""
    s = line.strip()
    if not s:
        return True
    if s.startswith(("#", ">", "```", "---", "✅", "❌", "📌", "💡")):
        return True
    if re.match(r"^\**\s*(정답|해설|답|Answer|Explanation)\s*[:：]?", s):
        return True
    if _is_question_header(s):
        return True
    # v2.10: 조합형 답 선지 라인 제외 (예: (가),(나) 참조 포함)
    body = _STRIP_PREFIX_RE.sub("", s).strip()
    if body and len(s) < 70 and _GANA_REF_RE.search(body):
        if re.match(r'^[\(①-⑳]', body):
            return True
    # v2.15: 조합형 답 선지 라인 (ⓐⓑⓒⓓⓔ 포함) — 일반 선지로 오분류 방지
    if re.search(r'[ⓐⓑⓒⓓⓔⓕⓖⓗ]', s):
        return True
    return False


_GANA_REF_RE = re.compile(r'\(([가나다라마])\)')
_GANA_TO_CIRCLE = {'가': '①', '나': '②', '다': '③', '라': '④', '마': '⑤'}
# v2.16: 조합형 답 선지 감지용 (cbt.py와 동일하게 정의 — pdf_export에도 필수)
_COMBO_LINE_RE = re.compile(r'[ⓐⓑⓒⓓⓔⓕⓖⓗ]')


def _sync_combo_refs(lines: list, start: int, end: int) -> None:
    """(가)(나) → ①② : 본선지 재번호 후 조합형 답 선지 동기화."""
    for idx in range(start, min(end, start + 25)):
        line = lines[idx]
        if not line.strip():
            continue
        if _is_question_header(line.strip()) or '<details>' in line or '</details>' in line:
            break
        if _GANA_REF_RE.search(line) and len(line.strip()) < 80:
            lines[idx] = _GANA_REF_RE.sub(
                lambda m: _GANA_TO_CIRCLE.get(m.group(1), m.group(0)), line
            )


def autonumber_choices(md: str) -> str:
    """v2.7 — 선지 prefix를 무엇이든 ①②③…로 통일.
    (가)(나), 1)2), A./a., indent 0인 평문 등 모두 처리. 이미 ①이 있어도 재번호."""
    if not md:
        return md
    lines = md.split("\n")
    n = len(lines)
    out = lines[:]
    i = 0
    while i < n:
        # 트리거: '문제 N' 헤더 또는 '?'/'고르시오'/'쓰시오'로 끝맺는 발문
        if _is_question_header(out[i]) or _ends_question_body(out[i]):
            j = i + 1
            while j < n and not out[j].strip():
                j += 1
            picks = []
            k = j
            while k < n and len(picks) < 10:
                cur = out[k]
                if not cur.strip():
                    # 선지 사이 빈 줄 1개까지 허용
                    if k + 1 < n and not _is_blocking(out[k+1]) and out[k+1].strip():
                        k += 1
                        continue
                    break
                if _is_blocking(cur):
                    break
                if len(cur.strip()) > 250:
                    break
                picks.append(k)
                k += 1
            if 2 <= len(picks) <= 10:
                # v2.16: picks 이후 ⓐⓑⓒ 라인이 있으면 → (가)(나)(다)(라) 넘버링
                _has_combo_ans = False
                for _m in range(k, min(n, k + 8)):
                    _ms = out[_m].strip()
                    if not _ms:
                        continue
                    if _COMBO_LINE_RE.search(_ms):
                        _has_combo_ans = True
                        break
                    # 선지 이외의 내용이 나오면 탐색 중단
                    if _is_blocking(out[_m]):
                        break

                _GANA_LABELS_AU = ['가', '나', '다', '라', '마', '바', '사', '아', '자', '차']
                gana_used = any(_GANA_REF_RE.match(out[ln].strip()) for ln in picks)
                for idx, line_no in enumerate(picks):
                    raw = out[line_no]
                    indent_m = re.match(r"^(\s*)", raw)
                    indent = indent_m.group(1) if indent_m else ""
                    body = _STRIP_PREFIX_RE.sub("", raw).strip()
                    if len(indent) < 3:
                        indent = "   "
                    if _has_combo_ans and idx < len(_GANA_LABELS_AU):
                        # 조합형 본선지 → (가)(나)(다)(라) 넘버링
                        out[line_no] = f"{indent}({_GANA_LABELS_AU[idx]}) {body}"
                    else:
                        out[line_no] = f"{indent}{_CIRCLES[idx]} {body}"
                if gana_used and not _has_combo_ans:
                    _sync_combo_refs(out, k, min(n, k + 25))
                i = k
                continue
        i += 1
    return "\n".join(out)


def split_questions_answers(md: str):
    """본문(문제+선지) / 답안 리스트로 분리.

    답안 = `<details>` 중 <summary>가 '정답'을 포함하는 것.
    문제 토글의 <details>/</details>/<summary>는 본문에서 제거하되 내용은 유지.
    v2.9 fix: lookahead 10줄로 확대 + 같은 줄에 <details><summary> 붙어있는 경우 처리.
    """
    lines = md.split("\n")
    main, answers = [], []
    mode_stack = []  # "normal" | "answer"
    cur_qid = "?"
    answer_buf, answer_qid = None, None
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        s = line.strip()

        if "answer" not in mode_stack:
            m = _QID_PAT.search(line)
            if m:
                cur_qid = m.group(1).rstrip(".")

        if "<details>" in s:
            # <details><summary>...  한 줄 또는 이후 최대 10줄에서 summary 탐색
            summary = ""
            for k in range(i, min(n, i + 10)):
                sm = _SUMMARY_PAT.search(lines[k])
                if sm:
                    summary = sm.group(1)
                    break
                # <details> 이후 빈 줄 아닌 내용이 나왔는데 summary가 없으면 중단
                if k > i and lines[k].strip() and "<summary>" not in lines[k]:
                    break
            is_answer = bool(re.search(r'정답', summary))
            mode_stack.append("answer" if is_answer else "normal")
            if is_answer and mode_stack.count("answer") == 1:
                answer_buf, answer_qid = [], cur_qid
            i += 1
            continue

        if "</details>" in s:
            if mode_stack:
                closing = mode_stack.pop()
                if closing == "answer" and "answer" not in mode_stack:
                    answers.append((answer_qid, "\n".join(answer_buf).strip()))
                    answer_buf, answer_qid = None, None
            i += 1
            continue

        sm = _SUMMARY_PAT.search(line)
        in_answer = "answer" in mode_stack
        if sm:
            if not in_answer:
                main.append(sm.group(1))  # 문제 헤더는 본문에 유지
            # 답안 모드의 "정답 확인" summary는 제거 (섹션 제목으로 대체)
            i += 1
            continue

        if in_answer:
            if answer_buf is not None:
                answer_buf.append(line)
        else:
            main.append(line)
        i += 1

    # 닫히지 않은 answer 블록이 있으면 강제 저장
    if answer_buf:
        answers.append((answer_qid, "\n".join(answer_buf).strip()))

    return "\n".join(main), answers


def _render_md(md: str, s: dict) -> list:
    # 모델이 출력 전체를 ```markdown ... ```으로 감싸면 본문이 전부 code로 잘못 입력됨.
    # Courier는 한글 글리프 없어서 ■로 렌더됨 → 바깥쪽 펨스는 벳겨낸다.
    lines = md.split("\n")
    while lines and lines[0].strip().startswith("```"):
        lines.pop(0)
    while lines and lines[-1].strip().startswith("```"):
        lines.pop()

    story, in_code = [], False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            # 코드블록도 NotoSansKR로 렌더 (Courier → 한글 ■).
            # 코드 느낌은 회색 배경 + 작은 글꼴로 구분.
            esc = (line.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;")
                       .replace(" ", "&nbsp;"))
            story.append(Paragraph(
                f"<font size='9' backColor='#F2F2F2'>{esc}</font>",
                s["body"],
            ))
            continue
        if line.startswith("# "):
            story.append(Paragraph(_md_inline(line[2:]), s["title"]))
        elif line.startswith("## "):
            story.append(Paragraph(_md_inline(line[3:]), s["h2"]))
        elif line.startswith("### "):
            story.append(Paragraph(_md_inline(line[4:]), s["h3"]))
        elif line.strip().startswith(("- ", "* ")):
            story.append(Paragraph("&nbsp;&nbsp;• " + _md_inline(line.strip()[2:]), s["body"]))
        elif line.strip():
            story.append(Paragraph(_md_inline(line), s["body"]))
        else:
            story.append(Spacer(1, 6))
    return story


def build_pdf(markdown_text: str, title: str = "AI 문항 세트") -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=50, bottomMargin=50,
                            splitLongParagraphs=True)
    s = _styles()

    markdown_text = autonumber_choices(markdown_text)
    main_md, answers = split_questions_answers(markdown_text)

    story = [
        Paragraph(title, s["title"]),
        Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M") + "  ·  문제지", s["small"]),
        Spacer(1, 12),
    ]
    story += _render_md(main_md, s)

    # ─── 정답 및 해설: 별도 페이지 ───
    if answers:
        story.append(PageBreak())
        story.append(Paragraph("정답 및 해설", s["title"]))
        story.append(Paragraph("문제지를 다 푼 뒤 확인하세요.", s["small"]))
        story.append(Spacer(1, 12))
        for qid, body in answers:
            story.append(Paragraph(f"<b>{qid}</b>", s["h3"]))
            story += _render_md(body, s)
            story.append(Spacer(1, 8))

    story.append(Spacer(1, 18))
    story.append(Paragraph("<i>AI 생성 문항 — 검토 후 사용.</i>", s["small"]))
    doc.build(story)
    return buf.getvalue()
