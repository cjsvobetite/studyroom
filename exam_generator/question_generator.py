import time, math
from openai import OpenAI, RateLimitError
from prompt_loader import build_system_prompt, build_user_prompt

from config import get_secret

client = OpenAI(api_key=get_secret("OPENAI_API_KEY"))

MAX_CONTINUATIONS = 4         # 잘렸을 때 최대 이어쓰기 횟수
MAX_TOKENS_PER_CALL = 16000   # gpt-4o output 한도 근처
MAX_RETRY = 3                 # 429 재시도 최대 횟수

# 모델별 입력 토큰 안전 한도 (출력 여유분 확보)
_MODEL_INPUT_LIMIT = {
    "gpt-4-turbo":   12000,   # TPM 30k → 입력 12k + 출력 16k = 28k (여유 2k)
    "gpt-4o":        100000,
    "gpt-4o-mini":   100000,
}
_DEFAULT_INPUT_LIMIT = 12000

CONTINUE_PROMPT = (
    "출력이 토큰 한도로 끊겼습니다. **끊긴 지점부터 같은 번호 체계와 형식**을 "
    "유지하며 이어서 작성하시오. 이미 출력한 문항은 반복하지 말고 **다음 문항부터** "
    "시작. 마지막에 반드시 **전체 정답표 토글**까지 포함해서 마무리할 것."
)


def _estimate_tokens(text: str) -> int:
    """간이 토큰 추정: 영문 4자≈1토큰, 한글 1자≈0.7토큰."""
    return int(len(text) * 0.7)


def _trim_to_limit(text: str, max_tokens: int) -> tuple[str, bool]:
    """텍스트를 max_tokens 이하로 잘라 반환. (결과, 잘렸으면 True)"""
    if _estimate_tokens(text) <= max_tokens:
        return text, False
    # 글자 수 기준으로 비율 추정
    limit_chars = int(max_tokens / 0.7)
    return text[:limit_chars], True


def _call_with_retry(model, messages, max_tokens, attempt_label=""):
    """429(rate limit) 발생 시 지수 백오프로 최대 MAX_RETRY회 재시도."""
    for retry in range(MAX_RETRY + 1):
        try:
            return client.chat.completions.create(
                model=model, messages=messages,
                temperature=0.4, max_tokens=max_tokens, stream=True,
            )
        except RateLimitError as e:
            err = str(e)
            # 「Request too large」: 재시도해도 해결 안 됨 → 즉시 raise
            if "Request too large" in err or "tokens per min" in err.lower():
                if retry == 0:
                    raise RuntimeError(
                        f"입력 토큰이 {model}의 분당 한도를 초과했습니다.\n"
                        "해결 방법:\n"
                        "① 전사본/강의 자료를 더 짧게 자른 뒤 재시도\n"
                        "② 모델을 gpt-4o 또는 gpt-4o-mini로 변경\n"
                        f"원본 오류: {err}"
                    ) from e
            if retry >= MAX_RETRY:
                raise
            wait = 2 ** retry  # 1s → 2s → 4s
            print(f"[429 재시도 {retry+1}/{MAX_RETRY}] {wait}초 대기 중...")
            time.sleep(wait)


def generate_questions(
    lecture_text, transcript_text,
    answer_formats=None, content_types=None,
    num_mcq=40, num_short=0, difficulty="표준",
    extra_instructions="", model="gpt-4o",
    term_lang_rule: str = "",
    past_exam_text: str = "",
    variation_mode: str = "",
):
    """
    Generator yielding (delta, full_text_so_far).
    answer_formats / content_types: list[str] (1개 이상). 비어 있으면 기본값 적용.
    term_lang_rule: 의학용어 표기 규칙 — system·user prompt 양쪽에 강제 주입.
    past_exam_text: 기출문제 원문 — 변형 모드 또는 범위 참고용. v2.17
    variation_mode: 변형 강도 ("유사 변형" / "적당한 변형" / "창의적 변형"). v2.17
    finish_reason='length'이면 자동으로 이어쓰기 호출 (최대 MAX_CONTINUATIONS회).
    gpt-4-turbo 등 TPM 한도가 낮은 모델은 입력을 자동으로 잘라 요청.
    """
    if not answer_formats:
        answer_formats = ["3) 오지선다 모두 고르시오 (복수정답)"]
    if not content_types:
        content_types = ["기본개념형 (정의·분류·단순 기전)"]

    # ── 입력 토큰 제한: gpt-4-turbo는 강의 자료를 자동 트리밍 ──
    input_limit = _MODEL_INPUT_LIMIT.get(model, _DEFAULT_INPUT_LIMIT)
    # system prompt 추정 (~3000 토큰) + user prompt 구조 오버헤드 (~1000) 제외
    content_budget = input_limit - 4000
    trimmed_notice = ""

    combined = transcript_text + "\n\n" + lecture_text
    combined_est = _estimate_tokens(combined)
    if combined_est > content_budget:
        # 전사본 우선 보존, 강의 자료를 먼저 자름
        transcript_budget = min(_estimate_tokens(transcript_text), int(content_budget * 0.7))
        lecture_budget    = content_budget - transcript_budget

        transcript_text, t_cut = _trim_to_limit(transcript_text, transcript_budget)
        lecture_text,    l_cut = _trim_to_limit(lecture_text,    lecture_budget)

        if t_cut or l_cut:
            trimmed_notice = (
                f"⚠️ [{model}] 입력 토큰 한도({input_limit:,})를 초과해 "
                "강의 자료를 자동으로 일부 잘랐습니다. "
                "전체 내용을 사용하려면 gpt-4o로 변경하세요."
            )
            yield f"\n<!-- TRIM_NOTICE: {trimmed_notice} -->\n", trimmed_notice

    # answer_formats + term_lang_rule을 system_prompt에도 주입 (v2.16)
    system_prompt = build_system_prompt(extra_instructions,
                                        answer_formats=answer_formats,
                                        term_lang_rule=term_lang_rule)
    user_prompt = build_user_prompt(
        lecture_text, transcript_text,
        answer_formats, content_types,
        num_mcq, num_short, difficulty,
        term_lang_rule=term_lang_rule,
        past_exam_text=past_exam_text,
        variation_mode=variation_mode,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]
    full = ""

    for attempt in range(MAX_CONTINUATIONS + 1):
        chunk_text = ""
        finish_reason = None

        resp = _call_with_retry(model, messages, MAX_TOKENS_PER_CALL,
                                attempt_label=f"이어쓰기 {attempt}")
        for chunk in resp:
            d = chunk.choices[0].delta.content or ""
            if d:
                chunk_text += d
                full += d
                yield d, full
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        if finish_reason != "length":
            break  # 정상 종료
        if attempt >= MAX_CONTINUATIONS:
            break  # 한도 초과 안전장치

        # 이어쓰기 트리거
        messages.append({"role": "assistant", "content": chunk_text})
        messages.append({"role": "user",      "content": CONTINUE_PROMPT})
