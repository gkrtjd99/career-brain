# syntax=docker/dockerfile:1
#
# career-brain 개인용 이미지: API + 내장 worker + 웹 정적 파일을 한 프로세스로 (`personal-api serve`).
#
#   docker build -t career-brain:local .                               # 기본 (키워드 검색, API 임베딩)
#   docker build -t career-brain:local --build-arg EXTRAS=local-embed . # + 로컬 임베딩 (fastembed/ONNX)
#   docker build -t career-brain:local --build-arg EXTRAS=browser .     # + 동적 공고 렌더링 (Chromium)
#   docker build -t career-brain:local --build-arg EXTRAS=pdf-render .  # + 스캔 PDF 공고를 이미지로 (pypdfium2, ~8MB)
#   docker build -t career-brain:local --build-arg EXTRAS=korean .      # + 한국어 형태소 분석 (kiwipiepy, ~100MB, CAREER_KOREAN_ANALYZER=kiwi)
#   docker build -t career-brain:local --build-arg WEB=0 .              # 웹 빌드 생략 (API만)
#
# 이미지에는 비밀값·설정·사용자 자료가 없다. 설정은 환경변수와 /config/models.toml,
# 자료는 /data 볼륨. 실행 예시는 deploy/docker/compose.yaml.

ARG PYTHON_IMAGE=python:3.13-slim
ARG NODE_IMAGE=node:24-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6
ARG WEB=1

# --- 웹 앱 빌드 (정적 파일만 결과로 남는다) ---------------------------------------------
FROM ${NODE_IMAGE} AS web-build
WORKDIR /src
COPY package.json package-lock.json tsconfig.base.json ./
COPY packages/ui packages/ui
COPY packages/client packages/client
COPY apps/personal-web apps/personal-web
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund && npm run build && cp -r apps/personal-web/dist /out

FROM web-build AS web-1

FROM ${PYTHON_IMAGE} AS web-0
RUN mkdir /out

FROM web-${WEB} AS web

# --- Python 의존성 + 패키지 (.venv 하나로 만든다) ---------------------------------------
FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
ARG EXTRAS=""
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/
COPY apps/personal-api/pyproject.toml apps/personal-api/
# 의존성 층: 소스가 바뀌어도 다시 받지 않는다. EXTRAS는 쉼표 구분 (local-embed,browser,pdf-render).
RUN --mount=type=cache,target=/root/.cache/uv \
    set --; for e in $(echo "$EXTRAS" | tr ',' ' '); do set -- "$@" --extra "$e"; done; \
    uv sync --frozen --no-dev --package personal-api --no-install-workspace "$@"
COPY packages/core/src packages/core/src
COPY apps/personal-api/src apps/personal-api/src
RUN --mount=type=cache,target=/root/.cache/uv \
    set --; for e in $(echo "$EXTRAS" | tr ',' ' '); do set -- "$@" --extra "$e"; done; \
    uv sync --frozen --no-dev --package personal-api --no-editable "$@"

# --- 실행 이미지 -----------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ARG EXTRAS=""
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAREER_HOST=0.0.0.0 \
    CAREER_PORT=8000 \
    CAREER_BLOB_DIR=/data/blobs \
    CAREER_RESTORE_DIR=/data/restore \
    CAREER_MODEL_DIR=/data/models \
    CAREER_MODELS_CONFIG=/config/models.toml \
    CAREER_WEB_DIR=/app/web \
    HF_HOME=/data/models/.hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN groupadd --system --gid 10001 career \
 && useradd --system --uid 10001 --gid career --home-dir /app --no-create-home \
            --shell /usr/sbin/nologin career \
 && mkdir -p /data /config \
 && chown career:career /data

# 코드와 의존성은 root 소유(읽기 전용). 실행 사용자는 /data 에만 쓴다.
COPY --from=builder /app/.venv /app/.venv
COPY --from=web /out /app/web
COPY LICENSE NOTICE /app/

# EXTRAS에 browser가 있으면 Chromium과 시스템 라이브러리를 넣는다 (수백 MB 증가).
RUN case ",${EXTRAS}," in \
      *,browser,*) apt-get update \
        && apt-get install -y --no-install-recommends libatomic1 \
        && playwright install --with-deps --only-shell chromium \
        && rm -rf /var/lib/apt/lists/* ;; \
    esac

USER career:career
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('CAREER_PORT','8000')}/api/core/health\", timeout=4)"]
ENTRYPOINT ["personal-api"]
CMD ["serve"]
