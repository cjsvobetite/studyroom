# AI 시험 문항 생성기 — Streamlit

Colab 노트북(`시험 문제 제작.ipynb`)을 GitHub에서 그대로 실행할 수 있는 Streamlit 앱으로 정리한 버전입니다.
강의자료(PDF/PPTX 등)와 기출문제를 올리면 OpenAI로 문항 세트를 만들고, 미리보기·CBT 풀이·PDF 다운로드·복습 기록을 제공합니다.

## 파일 구조

```
app.py                  Streamlit 진입점 (로그인/회원가입, 관리자 대시보드, 문항 생성, CBT)
config.py               경로·비밀키(OPENAI_API_KEY, ADMIN_ID)·폰트 설정
question_generator.py   OpenAI 호출 (이어쓰기·재시도)
prompt_loader.py        시스템/유저 프롬프트 조립
prompts/exam_guide.md   시험 유형별 문항 제작 지침
extractors.py           PDF / PPTX / 텍스트 추출
cbt.py                  CBT 모드 파싱·렌더링
history.py              유저별 풀이 기록 저장
pdf_export.py           PDF 생성 (한글 폰트)
```

실행 중 만들어지는 `data/`(users.json, cbt_history.json, outputs/)와 `fonts/*.ttf`는 Git에 올라가지 않습니다.

## 로컬 실행

```bash
cd exam_generator
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 값 채우기
streamlit run app.py
```

`OPENAI_API_KEY`, `ADMIN_ID`는 환경변수로 넣어도 됩니다. 한글 폰트(NanumGothic)는 첫 실행 때 `fonts/`에 자동으로 내려받습니다. 내려받지 못하면 ReportLab 내장 한글 폰트로 PDF를 만듭니다.

## Streamlit Community Cloud 배포

1. New app → 이 저장소 선택, **Main file path**를 `exam_generator/app.py`로 지정합니다.
2. Advanced settings → Secrets에 아래 내용을 넣습니다.

```toml
OPENAI_API_KEY = "sk-..."
ADMIN_ID = "관리자로 쓸 아이디"
```

- API 키는 절대 코드나 GitHub에 올리지 마세요.
- `ADMIN_ID`와 같은 아이디로 가입·로그인하면 관리자 대시보드가 열립니다. 지정하지 않으면 기존 노트북 값(`jsdec22`)을 씁니다. 먼저 그 아이디로 가입해 두세요.
- Streamlit Cloud는 재시작 시 파일이 초기화되므로 회원·풀이 기록(`data/`)도 사라집니다. 오래 보관하려면 DB가 필요합니다.

## Colab에서 실행 (기존 방식)

```python
!git clone https://github.com/cjsvobetite/studyroom.git
%cd studyroom/exam_generator
!pip install -q -r requirements.txt pyngrok

import os, getpass, subprocess, time
os.environ["OPENAI_API_KEY"] = getpass.getpass("OpenAI API 키: ")
subprocess.Popen(["streamlit", "run", "app.py", "--server.port=8501", "--server.headless=true"])
time.sleep(8)

from pyngrok import ngrok
ngrok.set_auth_token(getpass.getpass("ngrok 토큰: "))
print(ngrok.connect(8501, "http").public_url)
```
