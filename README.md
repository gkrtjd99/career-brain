# career-brain

개인 서버에서 경력·프로젝트·학습·문서·관심 공고·지원 이력을 관리하는 **개인용 커리어 브레인** 오픈소스.

이 레포는 두 역할을 가진다.

1. **바로 설치해서 쓰는 개인용 제품** — `apps/personal-web`(React) + `apps/personal-api`(FastAPI) + PostgreSQL + worker
2. **공통 엔진** — `packages/core`(Python `career-core`), `packages/ui`, `packages/client`, `skills/`

> 상태: 1차 범위(O01–O12) 구현 완료, 아직 정식 릴리스 전. 설계는 [docs/architecture/plan.md](docs/architecture/plan.md).

*A self-hosted career "second brain": sources with evidence, experiences and claims, interviews, search, job postings, applications with quality checks, study tracking, runs/approvals and an MCP server for AI clients. Runs as one Python process + PostgreSQL. Install from this GitHub repo (no registry packages). Docs are in Korean; commands below are the quick path.*

## 무엇을 하나

| 영역 | 하는 일 |
|---|---|
| 브레인 | 이력서·문서를 올리면 파싱해 원문 위치까지 남긴다. 경험·주장(claim)은 원문 인용 근거와 함께 저장되고, 사람이 확인해야 사실이 된다. 인터뷰형 질문으로 경험을 정리한다. |
| 검색 | 키워드(pg_trgm) 기본, 선택으로 의미 검색(OpenAI 또는 로컬 임베딩). 공간 밖 결과 0건. |
| 공고 | 붙여넣은 URL만 가져온다 (내부 주소 차단, 쿠키 없음). 구조화 데이터 → 채용 시스템 어댑터 → 인용 검증 LLM 순으로 추출. 무변경 재수집은 버전을 만들지 않고, 실패는 마감으로 처리하지 않는다. |
| 지원·문서 | 문항별 문서, 버전 충돌 409, 근거 연결, 코드가 센 글자·바이트 수, 범용·다른 회사 문장·근거 없는 서술·요건 가시성·조작 의심 검사, 사실을 바꾸지 못하는 AI 수정안, 불변 제출본. |
| 학습 | 공고 요건 → 학습 항목·시험 → 시도·오답 → 복습 일정. 학습은 경력으로 바뀌지 않는다. |
| 실행·승인·MCP | 재시작 후 재개되는 작업, 사람만 하는 승인(대상이 바뀌면 무효), AI 클라이언트용 MCP 도구 20개 (웹과 같은 권한 검사). [mcp.md](docs/architecture/mcp.md) · 스킬: [skills/](skills/) |
| 운영 | 한 프로세스 실행, 공간 내보내기·복원(개수·해시 검증), 모델 호출 장부·월 예산. |

## 설치 (Install)

자세한 설명·백업·업데이트·리버스 프록시: [docs/operations/install.md](docs/operations/install.md) · 내보내기·복원·백업 일정: [docs/operations/backup.md](docs/operations/backup.md)

**Docker (서버)** — 앱 1개(API + worker + 웹) + PostgreSQL/pgvector. 앱 유휴 메모리 약 100MB.

```bash
git clone https://github.com/gkrtjd99/career-brain.git && cd career-brain/deploy/docker
cp .env.example .env && cp app.env.example app.env      # DB 비밀번호(openssl rand -hex 24), 모델 키
mkdir -p config && cp ../../models.example.toml config/models.toml   # 선택
docker compose up -d --build                             # db → migrate → app (127.0.0.1:8000)
docker compose run --rm app bootstrap                    # 첫 설치 때 한 번: API 토큰
```

인터넷에 열 때는 앞에 TLS 리버스 프록시(Caddy 등)를 둔다. DB 포트는 열리지 않고 앱은 기본 localhost에만 열린다.

**로컬 (uv)**

```bash
uv sync --package personal-api --no-dev                  # 로컬 임베딩까지: --extra local-embed
docker compose -f deploy/compose/compose.yaml up -d db   # 개발용 DB (127.0.0.1)
uv run --no-sync personal-api migrate
uv run --no-sync personal-api bootstrap
npm ci && npm run build                                  # 선택: 웹 화면
uv run --no-sync personal-api serve                      # http://127.0.0.1:8000
```

**업데이트**: `git pull` → (Docker) `docker compose up -d --build` / (로컬) `uv sync ... && personal-api migrate`. 그 전에 `pg_dump` 백업 또는 `personal-api export` ([backup.md](docs/operations/backup.md)).

**검색 임베딩**: 기본은 키워드 검색만(가장 가볍다). 작은 서버는 OpenAI `text-embedding-3-small`, 자료를 밖으로 보내지 않으려면 로컬 모델(이미지 `CAREER_EXTRAS=local-embed`, 사용 중 RAM ~1.6GB). [search.md](docs/architecture/search.md)

## 구조

```text
apps/personal-web     React + TS + Vite (개인 대시보드)
apps/personal-api     FastAPI composition root
packages/core         career_core: domain / usecases / ports / db / migrations / adapters / jobs / interfaces
packages/ui           문서 편집·근거 카드 등 공통 UI
packages/client       TS API 클라이언트
contracts/            OpenAPI·JSON Schema·호환성
skills/               업무 스킬 (SKILL.md)
deploy/compose/       개발용 DB compose
deploy/docker/        서버 배포 compose (Dockerfile은 레포 루트)
```

## 개발

```bash
uv sync                    # Python (3.13)
uv run pytest
npm install                # Node 24
docker compose -f deploy/compose/compose.yaml up -d db   # PostgreSQL + 앱 역할(career_api)
uv run pytest                                           # DB 테스트는 임시 DB를 만들고 지운다
uv run personal-api migrate                             # core → personal migration
uv run personal-api bootstrap                           # 공간 + owner + API 토큰 (한 번만 출력)
uv run uvicorn personal_api.main:app --reload           # http://127.0.0.1:8000 (API만)
uv run personal-api worker                              # 파싱 등 백그라운드 작업
uv run personal-api serve                               # 또는 한 프로세스로: API + worker + 웹(dist)
curl -H "Authorization: Bearer <token>" localhost:8000/api/core/me
curl -H "Authorization: Bearer <token>" -F file=@resume.pdf localhost:8000/api/core/sources
npm run dev                                             # http://localhost:5173
```

모델(LLM) 연결과 비용:

```bash
cp models.example.toml models.toml       # 엔드포인트·라우트·월 예산 (키는 환경변수로)
cp .env.example .env                     # 키·토큰 (git 제외, 셸 환경변수가 우선)
uv run personal-api models               # 설정 상태 (키 값은 출력하지 않음)
uv run personal-api pricing              # 단가표 점검 (--fetch-xai: xAI 공식 단가 API와 대조)
uv run personal-api ping                 # 짧은 합성 요청으로 연결 확인 (종량제면 과금)
uv run personal-api usage --by model     # CAREER_API_TOKEN 의 공간
curl -H "Authorization: Bearer <token>" "localhost:8000/api/core/usage?group_by=day&days=30"
```

웹(개인 대시보드): `npm run dev` 후 설정 화면에 API 토큰을 넣는다. API 주소가 8000이 아니면 `CAREER_API_PROXY=http://127.0.0.1:8765 npm run dev`. 배포용 정적 파일은 `npm run build` → `apps/personal-web/dist`. 화면 구성: [docs/architecture/web.md](docs/architecture/web.md)

AI 클라이언트(Claude Code·Desktop 등) 연결: 같은 API 토큰으로 MCP `http://127.0.0.1:8000/api/core/mcp` 또는 `personal-api mcp`(stdio). 업무 도구만 있고 승인은 사람만 한다. [docs/architecture/mcp.md](docs/architecture/mcp.md) · 실행·승인: [docs/architecture/runs.md](docs/architecture/runs.md)

DB 구조·역할·RLS 규칙: [docs/architecture/database.md](docs/architecture/database.md) · 모델 게이트웨이: [docs/architecture/models.md](docs/architecture/models.md) · 설치·운영: [docs/operations/install.md](docs/operations/install.md) · Rust 검토: [docs/architecture/rust.md](docs/architecture/rust.md)
DB 구조·역할·RLS 규칙: [docs/architecture/database.md](docs/architecture/database.md) · 모델 게이트웨이: [docs/architecture/models.md](docs/architecture/models.md) · 설치·운영: [docs/operations/install.md](docs/operations/install.md) · 백업·복원: [docs/operations/backup.md](docs/operations/backup.md) · Rust 검토: [docs/architecture/rust.md](docs/architecture/rust.md)

## 원칙

- `packages/core`에 결제·플랜·서비스 운영 코드를 넣지 않는다. `IS_CLOUD` 분기를 만들지 않는다.
- 비공개 레포의 어떤 모듈도 import하지 않는다.
- 실제 개인정보·키·쿠키·DB 덤프를 커밋하지 않는다. 데모·테스트는 합성 데이터만.

## License

Apache-2.0
