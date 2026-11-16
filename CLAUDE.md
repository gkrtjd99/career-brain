# career-brain — 작업 규칙

- 설계 원본: `docs/architecture/plan.md`. 구현 순서는 §16 (O01 → O02 → …).
- 이 레포는 **공개**다. 실제 개인정보·키·쿠키·운영 로그를 커밋하지 않는다. fixture는 합성 데이터.
- 의존 방향은 career-service → career-brain 하나뿐. 여기서 서비스 코드를 import하거나 서비스 전용 API를 요구하지 않는다.
- 결제·플랜·고객지원·운영자 화면은 이 레포의 범위가 아니다 (sibling `../career-service`).
- 바깥 기능은 `career_core.ports`의 작은 인터페이스로만 받는다. 주입이 없으면 거부가 기본값.
- 사용자 데이터 테이블에는 처음부터 `workspace_id`.
- 코어 테이블 migration은 이 레포가 소유한다 (`packages/core/src/career_core/migrations`).

## 명령

- Python: `uv sync`, `uv run pytest`, `uv run ruff check .`
- Web: `npm install`, `npm run typecheck`, `npm run build`, `npm run dev`
