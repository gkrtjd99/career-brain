# Rust 검토: Python으로 계속 간다 (O12)

**결론: 지금은 Python(FastAPI·SQLAlchemy)을 유지하고 Rust·PyO3 구성요소를 넣지 않는다.**
이 프로젝트에서 시간과 메모리를 쓰는 곳은 Python 코드가 아니라 PostgreSQL·외부 모델 API·네이티브 라이브러리(ONNX Runtime, Chromium)다.
아래 "다시 볼 조건"을 하나라도 측정으로 넘으면, 그 경로 하나만 PyO3 모듈로 바꾸는 것을 검토한다.

## 측정 (2026-11-16, Apple Silicon, Docker Desktop arm64, 합성 자료)

방법과 전체 수치: [install.md](../operations/install.md) "자원" 절.

| 항목 | 값 | 어디서 시간이 가나 |
|---|---|---|
| 앱 유휴 RSS (`serve`: API + worker) | 약 85~100MB | 인터프리터 + FastAPI/pydantic/SQLAlchemy/psycopg 적재. SDK(openai·anthropic)·pypdf·fastembed는 쓸 때 올린다 |
| 키워드 검색 (블록 3,613 + 주장 510) | p50 150ms, p95 225ms (블록 7,200개: p50 275ms) | **PostgreSQL CPU 98%, 앱 2%** |
| `/api/core/health` | p50 0.5ms | 앱 오버헤드 자체는 1ms 미만 |
| 텍스트 자료 300개 파싱 | 약 11초 (그동안 health 최대 1.5ms) | DB 쓰기 위주 |
| HTML 공고 추출 (`extract_page`) | 59KB 4ms, 266KB 33ms | 순수 Python(`html.parser`) |
| PDF 텍스트 (`pypdf`, 단순 글꼴) | 페이지당 약 1.1ms (50쪽 57ms) | 순수 Python. 복잡한 글꼴·표가 많은 PDF는 더 느리다 |
| 로컬 임베딩 | 모델 단독 RAM 0.7~1GB | ONNX Runtime(C++). Python은 호출만 한다 |

## 일의 성격: I/O 대기가 대부분

- **LLM 호출**(추출·인터뷰 질문·API 임베딩): 수백 ms~수십 초의 네트워크 대기. 언어를 바꿔도 같다. 비용도 토큰 값이 지배한다.
- **DB 질의**: 검색 점수 계산(pg_trgm·pgvector)은 PostgreSQL 안의 C 코드다. 위 측정에서 검색 시간의 대부분이 DB CPU였다. 원인은 질의 모양이다: `word_similarity()`를 행마다 계산해서 GIN trigram 색인을 쓰지 못한다. 고칠 곳은 SQL(`<%` 연산자로 색인 사용, 후보를 먼저 줄이고 점수 계산)이지 앱 언어가 아니다 (plan §18 백로그).
- **로컬 임베딩**: fastembed → ONNX Runtime. 이미 네이티브이고 메모리는 모델 가중치다. Rust로 감싸도 줄지 않는다.
- **동적 페이지 렌더링**: Chromium 프로세스. 선택 설치이고 앱 언어와 무관하다.
- **CPU를 실제로 쓰는 Python 코드**: HTML 추출, PDF 텍스트, 본문 해시·정규화. 모두 worker 작업이고, `serve`에서는 worker가 별도 스레드·이벤트 루프에서 돌아 API 응답을 막지 않는다 (측정: 파싱 중 health 최대 1.5ms). 공고 1건 수십 ms는 사람이 공고를 저장하는 빈도에서 문제가 되지 않는다.

## 메모리: Rust로 아낄 수 있는 양

- 같은 API를 Rust(axum + sqlx)로 쓰면 유휴 RSS는 대략 10~30MB일 것이다 (측정 아님, 일반적인 범위). 프로세스당 약 70MB 차이.
- 개인 서버 전체로 보면: 앱 ~100MB + PostgreSQL ~40MB(+ shared_buffers 128MB) ≈ 300MB. 1GB 서버에서 Python 때문에 더 큰 서버가 필요해지지 않는다.
- 더 큰 몫은 따로 있다: 로컬 임베딩 ~1GB(mmap 로드 전 1.6GB, → OpenAI 임베딩이면 0), Chromium 수백 MB(→ 기본 꺼짐), API·worker를 두 프로세스로 나누기(+83MB → `serve`가 한 프로세스로 합침), alembic 상주(+8MB → migrate 때만 import). 이번 작업은 이 쪽을 줄였다.

## 호스팅 서비스로 갈 때의 확장 경로

같은 코어를 서비스(career-service)가 쓸 때도 병목 순서는 Python보다 DB가 먼저다.

1. **API 수평 확장**: API는 상태가 없다 (세션 문맥은 트랜잭션 로컬 `set_config`). 여러 복제본 + 로드밸런서. 복제본마다 연결 풀이 생기므로 PgBouncer(transaction 모드)를 앞에 둔다. 트랜잭션 로컬 설정이라 transaction 풀링과 맞는다.
2. **worker 분리·확장**: `serve --no-worker` + `worker` N개. `core.claim_job`은 `FOR UPDATE SKIP LOCKED`라 worker를 늘려도 중복 처리하지 않는다. 처리량이 모자라면 먼저 worker 하나가 여러 작업을 동시에 기다리게 한다 (LLM 대기가 대부분이라 프로세스를 늘리는 것보다 싸다).
3. **작업 큐**: 유휴 worker는 `LISTEN career_jobs`로 깨어나고 30초마다 한 번만 확인한다 (worker당 LISTEN 연결 1개, 분당 확인 쿼리 약 2회). PgBouncer transaction 모드 뒤의 worker는 `CAREER_WORKER_LISTEN=0`(짧은 간격 확인)이거나 DB에 직접 붙는다. 처리량이 더 필요하면 Procrastinate(plan §5)를 검토한다.
4. **PostgreSQL**: 검색 질의 색인 사용, 필요하면 읽기 복제본, 벡터가 많아지면 HNSW(재현율 확인 후). 공간(workspace)별 필터가 있어 개인 규모 정확 검색은 오래 버틴다.
5. **비용**: 사용자당 비용은 모델 API 토큰이 지배한다. 계산 자원 최적화보다 게이트웨이의 예산·라우팅(저가 모델로 추출)이 효과가 크다.

## Rust(PyO3)를 다시 볼 조건

측정으로 아래 중 하나를 넘으면, 그 경로 하나만 기존 port 뒤에 PyO3 모듈로 바꾸고 순수 Python 구현을 대체 경로로 남긴다.

| 경로 | 다시 볼 기준 | 그 전에 할 것 |
|---|---|---|
| API 요청 처리 | SQL을 고친 뒤에도 앱 CPU가 DB보다 먼저 포화되거나, 앱 쪽 처리 시간(DB 제외)이 p95 50ms를 넘음 | 프로파일링, 응답 모델 직렬화 줄이기 |
| PDF 텍스트 | 실제 자료에서 파싱이 worker 시간의 절반 이상이거나 10쪽당 1초 이상 | `pypdfium2` 등 이미 있는 네이티브 라이브러리로 교체 (`LocalParser` port) |
| HTML 공고 추출 | 서비스 규모에서 공고 가져오기가 CPU 병목 (페이지당 100ms 이상이 흔함) | `selectolax`/`lxml` 같은 네이티브 파서 |
| 임베딩 전처리·청크·해시 | 대량 색인에서 Python 전처리가 임베딩 호출 시간보다 큼 | 배치 크기 조정 |
| 프로세스 메모리 | 서비스 worker 수십 개 이상에서 프로세스당 ~80MB가 비용의 의미 있는 몫 | worker 안 동시 처리로 프로세스 수 줄이기 |

## Rust를 지금 넣지 않는 다른 이유

- **배포 방식**: 이 레포는 GitHub에서 받아 직접 빌드한다 (PyPI·Docker Hub 배포 없음). Rust 확장을 넣으면 Docker 빌드에 Rust 툴체인 단계(수백 MB~1GB, 수 분)가 붙고, uv로 직접 설치하는 사람은 cargo가 필요하다. "가볍게 설치"라는 목표와 반대다.
- **기여·유지보수**: 코어 도메인 규칙(근거·확정·RLS)이 Python에 있다. 두 언어로 나누면 같은 규칙을 두 번 시험해야 한다.
- **이득이 작다**: 측정된 시간의 대부분은 DB와 네트워크 대기다. 앱 CPU 2%를 0.2%로 줄여도 사용자가 느끼는 지연은 그대로다.
