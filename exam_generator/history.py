"""CBT 풀이 기록 저장·불러오기.

저장 위치: cbt_history.json  (config.HISTORY_PATH, 서버 재시작 전까지 누적)
구조:
  {
    "alice": [
      {
        "ts": "2026-05-10 15:30:00",
        "total": 20, "correct": 14, "pct": 70,
        "wrong_ids": ["문제 3", "문제 7", ...],
        "detail": {"문제 1": true, "문제 3": false, ...}
      },
      ...
    ],
    ...
  }
"""
import json
from pathlib import Path
from datetime import datetime

from config import HISTORY_PATH

_HISTORY_FILE = Path(HISTORY_PATH)


def _load_all() -> dict:
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_all(data: dict):
    _HISTORY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_attempt(user: str, questions: list, user_ans: dict, full_text: str = ""):
    """CBT 제출 결과를 기록에 추가.
    questions: parse_cbt_questions() 반환값
    user_ans:  {qid: [int,...] or str}  (session_state[ans_key])
    full_text: 원본 마크다운 전문 (복습 재풀이용, v2.16)
    """
    obj_qs = [q for q in questions if not q["is_subjective"]]
    detail = {}
    wrong_ids = []
    for q in obj_qs:
        qid = q["id"]
        ua = user_ans.get(qid)
        ua_list = ua if isinstance(ua, list) else []
        correct = bool(q["answers"]) and sorted(q["answers"]) == sorted(ua_list)
        detail[qid] = correct
        if not correct:
            wrong_ids.append(qid)
    total = len(obj_qs)
    correct_cnt = total - len(wrong_ids)
    pct = int(correct_cnt / total * 100) if total else 0

    record = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "correct": correct_cnt,
        "pct": pct,
        "wrong_ids": wrong_ids,
        "detail": detail,
        "full_text": full_text,   # v2.16: 재풀이용 원본 마크다운 저장
    }
    data = _load_all()
    data.setdefault(user, []).append(record)
    _save_all(data)
    return record


def load_history(user: str) -> list:
    """유저의 풀이 기록 리스트 반환 (최신순)."""
    data = _load_all()
    return list(reversed(data.get(user, [])))


def get_wrong_questions(questions: list, wrong_ids: list) -> list:
    """틀린 문항 ID 목록으로 해당 문항 객체만 필터링."""
    id_set = set(wrong_ids)
    return [q for q in questions if q["id"] in id_set]
