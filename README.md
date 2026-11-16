# career-brain

개인 서버에서 경력·프로젝트·학습·문서·관심 공고·지원 이력을 관리하는 **개인용 커리어 브레인** 오픈소스.

이 레포는 두 역할을 가진다.

1. **바로 설치해서 쓰는 개인용 제품** — `apps/personal-web`(React) + `apps/personal-api`(FastAPI) + PostgreSQL + worker
2. **공통 엔진** — `packages/core`(Python `career-core`), `packages/ui`, `packages/client`, `skills/`

> 상태: 초기 스캐폴딩. 설계는 [docs/architecture/plan.md](docs/architecture/plan.md).

## 구조

```text
apps/personal-web     React + TS + Vite (개인 대시보드)
apps/personal-api     FastAPI composition root
packages/core         career_core: domain / usecases / ports / db / migrations / adapters / jobs / interfaces
packages/ui           문서 편집·근거 카드 등 공통 UI
packages/client       TS API 클라이언트
contracts/            OpenAPI·JSON Schema·호환성
skills/               업무 스킬 (SKILL.md)
deploy/compose/       개인용 compose
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
uv run uvicorn personal_api.main:app --reload           # http://127.0.0.1:8000
uv run personal-api worker                              # 파싱 등 백그라운드 작업
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

DB 구조·역할·RLS 규칙: [docs/architecture/database.md](docs/architecture/database.md) · 모델 게이트웨이: [docs/architecture/models.md](docs/architecture/models.md)

## 원칙

- `packages/core`에 결제·플랜·서비스 운영 코드를 넣지 않는다. `IS_CLOUD` 분기를 만들지 않는다.
- 비공개 레포의 어떤 모듈도 import하지 않는다.
- 실제 개인정보·키·쿠키·DB 덤프를 커밋하지 않는다. 데모·테스트는 합성 데이터만.

## License

Apache-2.0
