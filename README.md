# 제2의학관 스터디룸 예약 — Streamlit

GitHub + Streamlit Community Cloud + Supabase 기반 예약 웹앱입니다.

## 파일 구조

```
app.py                      Streamlit 진입점 (로그인, 탭 구성)
studyroom/
  config.py                 상수·기본 운영 규칙
  timeutil.py               시간·슬롯 변환
  rules.py                  예약 가능 시간 계산, 비밀번호·명단 검증 등 순수 로직
  db.py                     Supabase 쿼리 (설정·방 목록은 30초 캐시)
  auth.py                   로그인, 시도 제한, 로그인 상태 유지, 초기 관리자 준비
  ui.py                     공통 스타일, 헤더, 시간표 그리드, 알림
  views/                    예약 / 내 예약 / 계정 / 관리자 화면
tests/                      pytest 단위 테스트
supabase_schema.sql         테이블·예약/취소 함수 (여러 번 실행해도 안전)
.streamlit/config.toml      테마 설정
.streamlit/secrets.toml.example  비밀키 형식 예시
```

## 1. Supabase 준비

1. Supabase에서 프로젝트를 만듭니다.
2. SQL Editor에 `supabase_schema.sql` 전체를 붙여 넣고 실행합니다.
   - **기존 프로젝트 업그레이드**도 같은 파일을 다시 실행하면 됩니다. 새 컬럼·함수·세션 테이블이 추가되고 기존 데이터는 유지됩니다.
3. Project Settings → API에서 Project URL과 `service_role` key를 복사합니다.

## 2. 비밀키(Secrets)

`.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사하고 실제 값을 넣습니다.

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVICE_ROLE_KEY"
INITIAL_ADMIN_ID = "admin"
INITIAL_ADMIN_PASSWORD = "강력한-초기-비밀번호"
```

- `service_role` key는 절대 GitHub에 올리지 마세요.
- 관리자 계정이 하나도 없으면 `INITIAL_ADMIN_ID` / `INITIAL_ADMIN_PASSWORD`로 관리자가 만들어집니다. 첫 로그인 때 비밀번호를 반드시 바꿔야 합니다.

### 예전 버전(관리자 1/1, 시험 계정 0/0)에서 업그레이드하는 경우

- 아이디와 같은 비밀번호(예: `1`/`1`)로는 **관리자 로그인이 차단**됩니다.
- Secrets에 `INITIAL_ADMIN_ID = "1"`과 새 `INITIAL_ADMIN_PASSWORD`를 넣고 앱을 재시작하세요. 그 계정의 비밀번호가 Secrets 값으로 재설정되며, 첫 로그인 때 다시 바꾸게 됩니다.
- 시험 계정 `0`/`0`은 앱 시작 시 자동으로 비활성화됩니다.

## 3. 로컬 실행

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

브라우저에서 `http://localhost:8501`을 엽니다.

### 테스트

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## 4. Streamlit Community Cloud

1. GitHub 저장소에 이 폴더의 파일을 업로드합니다.
2. https://share.streamlit.io 에서 새 앱을 만듭니다.
3. Main file path를 `app.py`로 지정합니다.
4. Advanced settings → Secrets에 로컬 `secrets.toml` 내용을 붙여 넣습니다.
5. Deploy를 선택합니다.

## 운영 기능

- **예약 규칙**(예약 가능 기간, 1인 하루 최대 시간, 운영 시간)은 관리자 → 운영 탭에서 바꿀 수 있습니다. 앱과 DB 함수가 같은 `settings` 값을 사용합니다.
  - 예약 가능 기간은 1~6일 또는 1~8주 중에서 고릅니다. 2주를 넘으면 예약 화면의 날짜 선택이 버튼 대신 달력으로 바뀝니다.
  - 30일(4주)을 넘는 기간을 쓰려면 최신 `supabase_schema.sql`을 한 번 더 실행해야 합니다.
- **사용자 관리**: 활성화/비활성화, 기간별 이용 정지·해제, 로그인 잠금 해제, 임시 비밀번호 발급, 계정 삭제(예약 기록 함께 삭제)
- **예약 관리**: 기간·방·학번 필터, 취소 내역(취소 시각·취소자) 조회, CSV 내려받기
- 스터디룸을 비활성화할 때 예정된 예약을 함께 취소할 수 있습니다.

## 보안

- 비밀번호는 Werkzeug 해시로 저장되며, 예약 중복은 DB 제약(exclusion constraint)과 RPC로 차단합니다.
- 같은 계정으로 5회 연속 로그인에 실패하면 10분간 잠깁니다.
- 관리자가 추가하거나 명단으로 올린 계정, 임시 비밀번호를 받은 계정은 첫 로그인 때 비밀번호를 바꿔야 합니다.
- '로그인 상태 유지'는 무작위 토큰을 브라우저 쿠키에 저장하고 DB에는 해시만 보관합니다(14일). 로그아웃하거나 비밀번호를 바꾸면 해제됩니다. 공용 PC에서는 사용하지 마세요.
- `service_role` key는 Streamlit Secrets에만 저장해야 합니다. 실제 운영 전에는 개인정보 처리방침, 계정 삭제 절차, 관리자 접근 통제를 추가로 점검하세요.
