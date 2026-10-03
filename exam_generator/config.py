"""경로·비밀키·폰트 설정.

Colab에서는 getpass로 받던 값을, 로컬/Streamlit Cloud에서는
환경변수 또는 .streamlit/secrets.toml에서 읽는다.
"""
import os
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("EXAM_DATA_DIR", BASE_DIR / "data"))
FONTS_DIR = BASE_DIR / "fonts"
OUTPUTS_DIR = str(DATA_DIR / "outputs")
HISTORY_PATH = str(DATA_DIR / "cbt_history.json")
USERS_PATH = str(DATA_DIR / "users.json")

DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_secret(name: str, default: str | None = None) -> str | None:
    """환경변수 → st.secrets 순서로 값을 찾는다."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


ADMIN_ID = get_secret("ADMIN_ID", "jsdec22")

_FONT_URLS = {
    "NanumGothic.ttf": "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf",
    "NanumGothic-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf",
}


def ensure_fonts() -> tuple[str, str]:
    """PDF용 한글 폰트가 없으면 내려받고 (regular, bold) 경로를 돌려준다."""
    FONTS_DIR.mkdir(exist_ok=True)
    for name, url in _FONT_URLS.items():
        path = FONTS_DIR / name
        if not path.exists() or path.stat().st_size == 0:
            urllib.request.urlretrieve(url, path)
    return str(FONTS_DIR / "NanumGothic.ttf"), str(FONTS_DIR / "NanumGothic-Bold.ttf")
