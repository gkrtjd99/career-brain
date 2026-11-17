# 외부 의존성 라이선스

career-brain은 Apache-2.0이다. 직접 의존하는 패키지와 라이선스를 적는다 (설치된 패키지 메타데이터 기준, 2026-11 확인). 버전 범위는 `pyproject.toml`·`package.json`, 정확한 버전은 `uv.lock`·`package-lock.json`이 원본이다. 간접 의존성은 각 패키지의 고지를 따른다.

## Python (기본 설치)

| 패키지 | 라이선스 | 메모 |
|---|---|---|
| fastapi | MIT | |
| pydantic | MIT | |
| sqlalchemy | MIT | |
| alembic | MIT | |
| psycopg, psycopg-binary | LGPL-3.0-only | 수정 없이 동적으로 불러 쓴다 (소스 포함 안 함) |
| pypdf | BSD-3-Clause | |
| python-multipart | Apache-2.0 | |
| httpx | BSD-3-Clause | |
| uvicorn | BSD-3-Clause | |
| anthropic, openai | Apache-2.0 | 모델 제공자 SDK (`llm` extra, personal-api 기본) |

## Python (선택 extra)

| extra | 패키지 | 라이선스 |
|---|---|---|
| `local-embed` | fastembed / onnxruntime | Apache-2.0 / MIT |
| `oauth` | pyjwt | MIT |
| `pdf-render` | pypdfium2 (PDFium 포함) | BSD-3-Clause, Apache-2.0 |
| `korean` | kiwipiepy, kiwipiepy-model | Apache-2.0 |
| `age` | pyrage | MIT |
| `browser` | playwright | Apache-2.0 (Chromium은 별도 설치) |

로컬 임베딩 모델 파일(예: `minishlab/potion-multilingual-128M`)은 처음 쓸 때 받으며 이 레포에 들어 있지 않다. 각 모델 카드의 라이선스를 따른다.

## 웹 (번들에 들어감)

| 패키지 | 라이선스 |
|---|---|
| react, react-dom | MIT |
| @tanstack/react-query, @tanstack/react-router | MIT |
| @radix-ui/react-dialog | MIT |
| clsx, tailwind-merge | MIT |
| lucide-react | ISC |
| pretendard (글꼴) | SIL OFL 1.1 — 글꼴 CSS 머리말에 고지 |
| tesseract.js, tesseract.js-core (이 기기 글자 읽기, 따로 불러오는 조각·`dist/ocr/`) | Apache-2.0 |
| @tesseract.js-data/kor, @tesseract.js-data/eng (학습 데이터 `4.0.0_best_int`, `dist/ocr/lang/`) | MIT (데이터 원본 tesseract-ocr/tessdata_best: Apache-2.0) |
| defuddle 0.19.6 (보고 있는 페이지 보내기: 본문 고르기, `/capture`에서만 따로 불러오는 조각) | MIT — 핵심 묶음(`defuddle`)만 쓴다. 수식·`full` 묶음과 그 선택 의존성(temml·linkedom 등)은 번들에 넣지 않는다 |
| turndown 7.2.5 (본문 HTML → Markdown, `/capture`에서만) | MIT |

빌드 도구(Vite, TypeScript, Tailwind CSS 등)는 번들에 들어가지 않는다.
