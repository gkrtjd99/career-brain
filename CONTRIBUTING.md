# 기여하기 (Contributing)

이슈·제안·PR 모두 환영합니다. 문서와 화면은 한국어가 기본이고, 이슈·PR은 한국어나 영어 어느 쪽이든 됩니다.

*Issues and PRs are welcome in Korean or English. Docs and UI are Korean-first.*

## 개발 환경

```bash
uv sync                                                  # Python 3.13
npm install                                              # Node 24
docker compose -f deploy/compose/compose.yaml up -d db   # PostgreSQL + pgvector (127.0.0.1)
uv run personal-api migrate
uv run personal-api demo                                 # 예시 데이터 공간 + 토큰 (모델 키 없이)
uv run personal-api serve                                # http://127.0.0.1:8000
```

구조 요약: [docs/architecture/overview.md](docs/architecture/overview.md)

## 올리기 전에 확인

검사는 로컬에서 돌린다. PR에 결과를 적어 주세요.

```bash
uv run ruff format . && uv run ruff check .
uv run pytest -q              # DB 테스트는 임시 DB를 만들고 지운다
npm run typecheck && npm run build
```

## 지켜야 할 규칙

- **합성 데이터만.** 실제 이력서·회사 자료·개인정보·키·쿠키·DB 덤프를 커밋하지 않는다. 테스트 자료는 코드에서 만들거나 지어낸 이름을 쓴다.
- **사용자 데이터 표에는 `workspace_id`와 FORCE RLS.** 공간을 넘는 조회는 SECURITY DEFINER 함수로만 ([database.md](docs/architecture/database.md)).
- **core migration을 더하면** `usecases/export.py`에 그 번호의 `schema_step` 또는 `no_data_change`를 같이 더한다. 시험(`test_schema_steps_cover_every_migration`)이 확인한다. 새로 내보내는 표는 합치기 키도 정한다.
- **바깥 기능은 `career_core.ports`의 작은 인터페이스로.** 주입이 없으면 거부가 기본값.
- **라우터는 검증과 usecase 호출만.** 규칙은 `domain/`·`usecases/`에 둔다 — HTTP·MCP·CLI·worker가 같은 usecase를 쓴다.
- **모델 출력은 사실이 아니다.** AI가 만든 경험·주장은 사람이 확인해야 확정된다. 이 원칙을 우회하는 기능은 받지 않는다.
- 결제·플랜·고객 지원 같은 서비스 운영 코드는 이 레포의 범위가 아니다.

## 라이선스

기여한 코드는 Apache-2.0으로 배포된다. 새 의존성을 더하면 [third-party-licenses.md](docs/architecture/third-party-licenses.md)에 적는다.
