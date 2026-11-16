# 설치·업데이트·운영 (O12)

career-brain은 GitHub 레포를 받아 직접 실행한다. PyPI·npm·Docker Hub에 올린 패키지는 없다.

```text
브라우저 ─(TLS 리버스 프록시: Caddy 등)─→ personal-api serve ──→ PostgreSQL 17 + pgvector
                                         ├ API (/api/...)
                                         ├ 내장 worker 스레드 (파싱·추출·색인·공고 가져오기)
                                         └ 웹 앱 정적 파일 (/, SPA)
```

상시 프로세스는 **앱 1개 + PostgreSQL 1개**다. worker를 따로 띄우고 싶으면 `serve --no-worker` + `worker`.

## 자원 (2026-11-16, Apple Silicon, Docker Desktop arm64, 합성 자료)

| 항목 | 값 |
|---|---|
| 이미지 (기본) | 308MB 풀린 크기 / 80MB 내려받기 (python:3.13-slim 자체가 147MB / 46MB) |
| 이미지 (`EXTRAS=local-embed`) | 500MB / 146MB. 모델 파일(~0.5GB)은 처음 쓸 때 `/data/models`로 받는다 |
| 이미지 (`EXTRAS=browser`) | 1080MB / 392MB (Chromium headless shell) |
| 앱 유휴 RSS (`serve`, API+worker) | 약 85~100MB. 유휴 CPU 약 0.4% (1초 간격 작업 확인 때 측정) |
| 유휴 worker의 DB 쿼리 (2026-11-17) | `LISTEN` + 30초 확인: 120초에 `claim_job` 5회 (시작·연결 직후 포함). 이전 1초 간격 확인: 60초에 60회 |
| 앱 2개로 나눴을 때 | API 약 93MB + worker 약 83MB |
| PostgreSQL 유휴 | 약 30~45MB (컨테이너 메모리, shared_buffers 128MB 설정) |
| 로컬 임베딩 사용 중 | 모델 단독 약 0.9~1.6GB 추가 ([search.md](../architecture/search.md)). API와 worker가 모델 하나를 같이 쓴다 |
| `/api/core/health` | p50 0.5ms, p95 1.2ms |
| 키워드 검색 (블록 3,613 + 주장 510) | p50 150ms, p95 225ms. 이때 CPU는 PostgreSQL 98%, 앱 2%. 블록 7,200개에서 p50 275ms (자료 수에 비례: 색인을 못 쓰는 질의, plan §18) |
| 파싱 처리량 (텍스트 자료 300개) | 약 11초. 그동안 health p50 0.8ms / 최대 1.5ms (worker가 API를 막지 않음) |

1GB RAM 서버에서 기본 구성(키워드 검색 또는 OpenAI 임베딩)이 넉넉히 돈다. 로컬 임베딩은 2~3GB 이상을 권한다.

## 1. 로컬 (uv)

필요한 것: Python 3.13, [uv](https://docs.astral.sh/uv/), Docker(개발용 DB) 또는 pgvector·pg_trgm이 있는 PostgreSQL 17. 웹 화면까지 쓰려면 Node 24.

```bash
git clone https://github.com/gkrtjd99/career-brain.git && cd career-brain
uv sync --package personal-api --no-dev            # 실행에 필요한 것만 (로컬 임베딩: --extra local-embed)
docker compose -f deploy/compose/compose.yaml up -d db   # 127.0.0.1:5432, 개발용 비밀번호
uv run --no-sync personal-api migrate
uv run --no-sync personal-api bootstrap            # API 토큰을 한 번만 출력한다. 안전한 곳에 보관
npm ci && npm run build                            # 선택: apps/personal-web/dist
uv run --no-sync personal-api serve                # http://127.0.0.1:8000 (API + worker + 웹)
```

- 레포 루트에서 실행하면 `apps/personal-web/dist`가 있을 때 웹 앱을 같이 내준다. 다른 폴더는 `CAREER_WEB_DIR`.
- 개발(`uv sync`, `uv run pytest`, `npm run dev`)은 [README](../../README.md#개발).
- 개발용 DB(`deploy/compose`)의 비밀번호는 공개된 값이다. 이 기계 밖으로 포트를 열지 않는다.

## 2. Docker (서버)

필요한 것: Docker Engine + Compose v2. 레포만 받으면 이미지는 서버에서 빌드한다.

```bash
git clone https://github.com/gkrtjd99/career-brain.git && cd career-brain/deploy/docker
cp .env.example .env              # CAREER_DB_OWNER_PASSWORD, CAREER_API_DB_PASSWORD (openssl rand -hex 24)
cp app.env.example app.env        # 모델 키 등 (선택)
mkdir -p config && cp ../../models.example.toml config/models.toml   # 선택: 모델 설정
docker compose up -d --build      # db → migrate(한 번) → app
docker compose run --rm app bootstrap   # 첫 설치 때 한 번: API 토큰 출력
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/core/me
```

- `.env`는 compose 값 채우기용(DB 비밀번호·포트·빌드 옵션), `app.env`는 앱 환경변수(모델 키)다. DB 소유자 비밀번호는 app 컨테이너에 들어가지 않는다.
- 빌드 옵션: `.env`의 `CAREER_EXTRAS=local-embed` (로컬 임베딩), `browser` (동적 공고 렌더링, `app.env`에 `CAREER_BROWSER_FETCH=1`도), `pdf-render` (스캔 PDF 공고를 쪽 이미지로 바꿔 비전 모델로: pypdfium2 ~8MB, `models.toml`에 `job_extract_image` 라우트도). 쉼표로 여럿. 로컬 `uv sync`에는 `pdf-render`가 기본으로 들어 있다.
- 자료: 볼륨 `db-data`(PostgreSQL), `app-data`(`/data`: 업로드 원본, 로컬 모델 파일). 설정: `./config` → `/config` (읽기 전용).
- 운영 명령: `docker compose run --rm app models`, `... app usage --by model` (`app.env`에 `CAREER_API_TOKEN`), `... app reindex`.
- 로그: `docker compose logs -f app`. 접근 로그를 끄려면 `CAREER_ACCESS_LOG=0`.

### 컨테이너 보안 기본값

- 이미지: 비root 사용자(uid 10001), 코드·의존성은 root 소유(읽기 전용), 쓰기는 `/data`만. `.dockerignore`가 허용 목록이라 `.env`·`models.toml`·`data/`·`.git`은 build context에 들어가지 않는다.
- compose: `read_only` 루트 파일시스템, `cap_drop: [ALL]`, `no-new-privileges`, `init`.
- 앱 포트는 기본 `127.0.0.1:8000`(서버 안에서만), DB 포트는 호스트에 열지 않는다.
- 앱은 RLS가 걸리는 `career_api` 계정으로만 접속하고, migration만 소유자 계정을 쓴다.

### 인터넷에 열기: TLS 리버스 프록시

앱은 HTTPS를 직접 하지 않는다. 같은 서버에 Caddy·nginx 등을 두고 `127.0.0.1:8000`으로 넘긴다. Caddy 예시 (`/etc/caddy/Caddyfile`):

```caddy
career.example.com {
    encode zstd gzip
    # 웹 복원(백업 묶음 올리기) 경로만 크게: CAREER_MAX_RESTORE_MB(기본 2048). 두 상한이 겹치지 않게 나눈다
    @restore path /api/core/maintenance/restore
    @other not path /api/core/maintenance/restore
    request_body @other {
        max_size 25MB          # CAREER_MAX_UPLOAD_MB(기본 20)보다 조금 크게
    }
    request_body @restore {
        max_size 2GB
    }
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options nosniff
        Referrer-Policy same-origin
        X-Frame-Options DENY
    }
    reverse_proxy 127.0.0.1:8000
}
```

- 프록시를 컨테이너로 같은 compose 네트워크에 두면 `CAREER_BIND`는 그대로 두고 프록시에서 `app:8000`으로 보낸다.
- 공개 주소에서 쓰는 API 토큰은 비밀번호처럼 다룬다. 새 토큰 발급·폐기는 `personal-api token` (아래 "API 토큰"). 세션 로그인·CSRF는 웹 로그인 작업에서 다룬다 (plan §14).
- 방화벽에서 80/443만 연다.

## 3. 모델 설정

`models.toml`(로컬: 레포 루트, Docker: `deploy/docker/config/models.toml`)에서 엔드포인트·라우트·월 예산을 정한다. 키는 파일에 넣지 않고 환경변수(`.env` / `app.env`)에 둔다. 예시: [models.example.toml](../../models.example.toml), 자세히: [models.md](../architecture/models.md).

검색 임베딩(`routes.embed`)은 셋 중 하나:

| 선택 | 설정 | 자원 | 언제 |
|---|---|---|---|
| OpenAI API (**예시 설정 기본**) | `embed = "openai/text-embedding-3-large"` + `[embedding] dimensions = 1024` + `OPENAI_API_KEY` | 로컬 메모리 거의 없음, $0.13/1M 토큰 (개인 자료 전체 색인도 몇 센트) | 키가 없으면 호출하지 않고 키워드 검색만 |
| OpenAI API (더 싸게) | `embed = "openai/text-embedding-3-small"` + `dimensions = 512` | $0.02/1M 토큰 | 비용이 더 중요할 때. 한국어 바꿔 말하기 검색이 약하다 |
| 없음 | `embed` 줄을 지움 | 추가 없음 | 가장 가볍다. 키워드 검색만 |
| 로컬 (fastembed) | `[endpoints.embed-local] preset = "local"` + `embed = "embed-local/minishlab/potion-multilingual-128M"` | 이미지 +190MB, 사용 중 RAM ~1GB (mmap 로드) | 자료를 밖으로 보내지 않을 때 |

- 바꾼 뒤에는 `personal-api reindex` (Docker: `docker compose run --rm app reindex`)로 다시 색인한다.
- 로컬 모델은 `CAREER_EMBED_IDLE_SECONDS`(기본 300초) 동안 안 쓰면 메모리에서 내린다. 스레드 수는 `CAREER_EMBED_THREADS`(기본 2).
- `personal-api models`로 설정 상태를 보고(키 값은 출력하지 않음), `personal-api ping`으로 연결을 확인한다(과금됨).

## 4. 업데이트

```bash
git pull                                  # 태그를 쓰면: git fetch --tags && git checkout vX.Y.Z
# 로컬
uv sync --package personal-api --no-dev && uv run --no-sync personal-api migrate && npm ci && npm run build
# Docker (migrate 서비스가 app보다 먼저 한 번 돈다)
cd deploy/docker && docker compose up -d --build
docker image prune -f                     # 옛 이미지 정리
```

- migration은 배포 명령(`personal-api migrate`)으로만 실행한다. API는 시작할 때 schema를 바꾸지 않는다. 여러 번 실행해도 잠금 아래 순서대로 한 번만 적용된다.
- 업데이트 전에 백업한다 (아래).

## 5. 백업·복원

자세히: [backup.md](backup.md). 요약:

```bash
personal-api export --out career-$(date +%F).tar.gz     # 공간 하나 (소유자 토큰), 행 수·해시 manifest 포함
personal-api restore career-YYYY-MM-DD.tar.gz --dry-run # 빈 설치에서 검사만 → 빼고 실행하면 복원 + 새 토큰
cd deploy/docker && docker compose exec -T db pg_dump -U career -d career_brain -Fc > career-db-$(date +%F).dump
```

- 서버는 매일 `pg_dump` + 원본 볼륨, 매주 공간 내보내기를 서버 밖에 암호화해서 둔다. 실행 중인 DB 데이터 폴더를 그대로 복사하지 않는다 (plan §14).
- 묶음과 덤프에는 개인 자료가 들어 있다. 묶음에는 API 토큰·모델 키·검색 벡터가 없다 (복원 후 새 토큰, 재색인).

## 6. API 토큰

`CAREER_API_TOKEN`에 **소유자** 토큰을 두고 그 공간의 토큰을 관리한다. 토큰 원문은 DB에 없고(SHA-256만), 목록은 ID·이름·상태·만료·마지막 사용만 보인다. 새 토큰은 발급할 때 한 번만 출력한다. 웹의 **설정 → 토큰 관리**(소유자만)에서도 같은 목록·발급·폐기를 한다 (`GET/POST /api/core/tokens`, `POST /api/core/tokens/<id>/revoke`: 응답에 토큰 원문·해시는 없고, 발급 응답의 `secret`에만 한 번).

```bash
personal-api token list                    # * = 지금 CAREER_API_TOKEN 토큰
personal-api token issue --label laptop    # 새 토큰 (한 번만 출력)
personal-api token issue --label ci-like --expires-days 90   # 90일 뒤 만료 (1~3650)
personal-api token revoke <id>             # 폐기: 다음 요청부터 API·MCP 모두 거부
# Docker: docker compose run --rm app token list
```

- 토큰이 샜으면: `issue`로 새 토큰 → `CAREER_API_TOKEN`·MCP 설정을 새 토큰으로 → 옛 토큰 `revoke`.
- 지금 쓰는 토큰은 폐기하지 않는다 (잠겨 나가지 않게). 다른 공간 토큰은 보이지도, 폐기되지도 않는다 (RLS).
- 만료: `--expires-days`를 주면 그 시각부터 API·MCP 모두 401이다 (목록에 `expired`). 기본은 만료 없음 (`bootstrap` 토큰 포함).
- 마지막 사용 시각은 인증할 때 **5분에 한 번만** 기록한다 (요청마다 DB에 쓰지 않게). 그래서 최대 5분 늦다.

## 7. 작은 서버 조정

- PostgreSQL: `deploy/docker/compose.yaml`의 `command`. 기본은 1GB 서버용(`shared_buffers=128MB`, `max_connections=40`, 병렬 질의·JIT 끔). RAM이 넉넉하면 `.env`의 `CAREER_PG_SHARED_BUFFERS`(RAM 25%)와 `CAREER_PG_EFFECTIVE_CACHE`(RAM 50%)를 올린다.
- 작업 깨우기: worker는 `LISTEN career_jobs`로 새 작업을 바로 알아채고, 알림을 놓쳤을 때를 위해 `CAREER_WORKER_POLL_SECONDS`(기본 30초)마다 한 번 확인한다. 알림이 오지 않는 작업(죽은 worker의 lease 만료, worker가 뜨기 전에 예약된 미래 작업)도 늦게 집지 않도록, 쉬기 전에 `core.next_job_due`로 다음 예정 시각을 보고 그때 깬다 (확인 간격보다 길게 자지 않는다. 조회가 실패하면 간격대로). DB 연결이 끊겨도 다시 연결하며, 그동안은 이 간격 확인으로 일한다. PgBouncer transaction 모드처럼 LISTEN이 안 되는 경로면 `CAREER_WORKER_LISTEN=0` (간격 기본 1초).
- 주기 유지보수: `serve`(내장 worker)와 `worker`가 `CAREER_MAINTENANCE_INTERVAL_SECONDS`(기본 21600 = 6시간, `0`이면 끔)마다 모든 공간의 미확정 공고 초안(7일 지난 것)을 정리한다. 시작 60초 뒤 첫 시도. 마지막 실행 시각을 DB(`core.maintenance_schedule`)에 두므로 worker가 여럿이거나 재시작해도 **간격마다 한 번만** 돈다 (먼저 돈 worker가 있으면 그 다음 시각까지 기다림). 동시 실행은 advisory lock으로 막고, 정리는 멱등이다. 마지막·다음 실행과 이 공간의 처리 수는 `GET /api/core/maintenance`의 `auto`. 지금 바로: `personal-api maintenance` (간격과 상관없이 돈다). `serve --no-worker`는 하지 않는다 (따로 띄운 `worker`가 한다). `pg_dump`는 앱이 하지 않는다: [backup.md](backup.md#4-일정-제안)의 cron/systemd timer.
- 업로드 크기: `CAREER_MAX_UPLOAD_MB`(기본 20). 웹 복원 업로드(`POST /api/core/maintenance/restore`) 하나만 `CAREER_MAX_RESTORE_MB`(기본 2048)을 쓴다 — 받는 파일은 임시 디렉터리(`TMPDIR`)에 두므로 그만큼 여유가 있어야 한다 ([backup.md](backup.md#웹-복원-system--백업유지보수--복원)).
- 앱 프로세스는 하나로 둔다 (`--workers` 없음). 개인 자료 규모에서는 DB가 먼저 일하고 앱 CPU는 거의 쓰지 않는다. 판단 근거: [rust.md](../architecture/rust.md).

## 환경변수 요약

| 변수 | 기본 | 뜻 |
|---|---|---|
| `CAREER_DATABASE_URL` | 개발 DB (`career_api`) | 앱 접속 (RLS 적용 계정) |
| `CAREER_MIGRATION_DATABASE_URL` | 개발 DB (`career`) | migration 전용 소유자 계정 |
| `CAREER_HOST` / `CAREER_PORT` | `127.0.0.1` / `8000` (이미지: `0.0.0.0`) | `serve` 바인딩 |
| `CAREER_EMBEDDED_WORKER` | `1` | `0`이면 `serve`는 API만 |
| `CAREER_WORKER_LISTEN` | `1` | 새 작업을 `LISTEN/NOTIFY`로 깨우기. `0`이면 간격 확인만 |
| `CAREER_WORKER_POLL_SECONDS` | `30` (LISTEN 끄면 `1`) | 대기 작업 확인 간격 (LISTEN일 때는 놓친 알림 대비) |
| `CAREER_MAINTENANCE_INTERVAL_SECONDS` | `21600` | 주기 유지보수(초안 정리) 간격, `0`이면 끔. worker가 여럿이어도 설치 전체에서 간격마다 한 번 |
| `CAREER_AUDIT_RETENTION_DAYS` | `180` | MCP 감사 기록 보존 일수 (주기 유지보수가 지움). `0`이면 계속 보관, 최소 7 |
| `CAREER_WEB_DIR` | `apps/personal-web/dist` (있을 때) | 웹 정적 파일. 빈 값이면 끔 |
| `CAREER_BLOB_DIR` | `data/blobs` (이미지: `/data/blobs`) | 업로드 원본 |
| `CAREER_MODELS_CONFIG` | `models.toml` (이미지: `/config/models.toml`) | 모델 설정 |
| `CAREER_MODEL_DIR` | – (이미지: `/data/models`) | 로컬 임베딩 모델 파일 |
| `CAREER_MAX_UPLOAD_MB` | `20` | 업로드 한도 |
| `CAREER_MAX_RESTORE_MB` | `2048` | 웹 복원(백업 묶음) 업로드 한도, 이 경로만 |
| `CAREER_BROWSER_FETCH` | `0` | 동적 공고 렌더링 (browser extra 필요) |
| `CAREER_LOG_LEVEL` / `CAREER_ACCESS_LOG` | `info` / `1` | 로그 |
| `CAREER_ENV_FILE` | `.env` | 로컬 실행 때 읽는 키 파일 (셸 변수가 우선) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | `X-Forwarded-*`를 믿을 프록시 주소 (uvicorn) |
