# 제2의학관 스터디룸 예약 — Streamlit

GitHub + Streamlit Community Cloud + Supabase 기반 예약 웹앱입니다.

## 파일

- `app.py`: Streamlit 웹앱
- `supabase_schema.sql`: Supabase 테이블·예약 함수 생성 SQL
- `requirements.txt`: 설치 패키지
- `.streamlit/secrets.toml.example`: 비밀키 형식 예시

## 1. Supabase 준비

1. Supabase에서 프로젝트를 만듭니다.
2. SQL Editor에 `supabase_schema.sql` 전체를 붙여 넣고 실행합니다.
3. Project Settings → API에서 Project URL과 새 `service_role` key를 복사합니다.

## 2. 로컬 비밀키

`.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사하고 실제 값을 넣습니다.

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_NEW_SERVICE_ROLE_KEY"
```

`service_role` key는 절대 GitHub에 올리지 마세요.

## 3. 로컬 실행

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

브라우저에서 `http://localhost:8501`을 엽니다.

- 최초 관리자: `1` / `1`
- 시험 사용자: `0` / `0`
- 첫 로그인 직후 관리자 비밀번호를 변경하세요.

## 4. Streamlit Community Cloud

1. GitHub 저장소에 이 폴더의 파일을 업로드합니다.
2. https://share.streamlit.io 에서 새 앱을 만듭니다.
3. Main file path를 `app.py`로 지정합니다.
4. Advanced settings → Secrets에 로컬 `secrets.toml` 내용을 붙여 넣습니다.
5. Deploy를 선택합니다.

## 보안

비밀번호는 Werkzeug 해시로 저장되며 예약 중복은 Supabase DB 제약과 RPC로 차단합니다. `service_role` key는 Streamlit Secrets에만 저장해야 합니다. 실제 운영 전에는 개인정보 처리방침, 계정 삭제 절차, 관리자 접근 통제를 추가로 점검하세요.
