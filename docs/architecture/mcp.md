# MCP 서버 (O08)

외부 AI 클라이언트(Claude Code, Claude Desktop 등)가 웹과 **같은 usecase·같은 권한 정책**으로 커리어 브레인을 쓰게 한다. REST endpoint를 자동으로 노출하지 않고 업무 단위 도구만 둔다 (plan §12).

```text
웹 작업 → REST(/api/core/…) ─┐
외부 AI → MCP(/api/core/mcp) ─┼─▶ 같은 UseCase · 같은 PermissionPolicy · 같은 RLS 공간 세션
예약 작업 → worker ───────────┘
```

## 전송

| 전송 | 주소·명령 | 인증 |
|---|---|---|
| streamable HTTP (JSON 응답) | `POST http://127.0.0.1:8000/api/core/mcp` | `Authorization: Bearer <API 토큰>` (요청마다 확인) |
| stdio | `personal-api mcp` | 환경변수 `CAREER_API_TOKEN` (메시지마다 다시 확인) |

- 토큰은 HTTP API와 같은 것(`personal-api bootstrap`이 만든 `cbp_…`)이고 같은 `IdentityResolver`로 확인한다. 폐기하면 다음 요청부터 막힌다.
- 공간은 토큰이 정한다. 도구 인자는 `additionalProperties: false`라서 `workspace_id`를 넘기면 `invalid_arguments`로 거부된다.
- `Origin` 헤더가 있으면 같은 호스트여야 한다(브라우저를 이용한 DNS rebinding 차단). `GET /api/core/mcp`(서버→클라이언트 스트림)는 제공하지 않아 405다.
- 지원 규격 판: `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`. 그 밖의 판을 요청하면 최신 판으로 답한다.
- 인터넷에 열 때는 HTTPS 프록시 뒤에 둔다 (`docs/operations/install.md`). 지금은 OAuth가 아니라 공간 범위 API 토큰을 쓴다. 표준 OAuth 연결은 백로그(plan §18).

공식 Python SDK(FastMCP)를 쓰지 않은 이유: 필요한 메서드가 `initialize`·`ping`·`tools/list`·`tools/call`뿐이고, SDK는 starlette·SSE·jsonschema·암호 라이브러리 등을 더 끌고 오며 인증도 기존 토큰 포트를 그대로 써야 했다. 구현은 `packages/core/src/career_core/interfaces/mcp/` (프로토콜·전송 약 330줄, 도구 정의 약 500줄).

## 연결 예시

토큰은 예시 자리표시자다. 실제 토큰을 설정 파일째 커밋하지 않는다.

Claude Code (HTTP):

```bash
export CAREER_API_TOKEN=cbp_REPLACE_ME
claude mcp add --transport http career-brain http://127.0.0.1:8000/api/core/mcp \
  --header "Authorization: Bearer $CAREER_API_TOKEN"
```

또는 프로젝트의 `.mcp.json` (환경변수 확장 사용):

```json
{
  "mcpServers": {
    "career-brain": {
      "type": "http",
      "url": "http://127.0.0.1:8000/api/core/mcp",
      "headers": { "Authorization": "Bearer ${CAREER_API_TOKEN}" }
    }
  }
}
```

Claude Desktop (stdio, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "career-brain": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/career-brain", "personal-api", "mcp"],
      "env": {
        "CAREER_API_TOKEN": "cbp_REPLACE_ME",
        "CAREER_DATABASE_URL": "postgresql+psycopg://career_api:REPLACE_ME@127.0.0.1:5432/career_brain"
      }
    }
  }
}
```

stdio는 API 서버 없이 DB에 직접 붙는다 (`.env`도 읽는다). 백그라운드 작업(공고 가져오기·실행)은 `personal-api serve` 또는 `personal-api worker`가 돌아야 진행된다.

## 도구

| 도구 | 행동(ACTION_SCOPES) | 같은 HTTP 경로 |
|---|---|---|
| `search_brain` | `search.read` | `GET /core/search` |
| `list_experiences` · `get_experience` · `list_pending_claims` · `get_evidence` | `brain.read` | `GET /core/experiences…`, `/source-spans/{id}` |
| `add_interview_answer` | `brain.write` | `POST /core/interview-questions/{id}/answers` |
| `save_job_urls` (최대 10개, URL 500자 이하) | `jobs.write` | `POST /core/job-postings` |
| `get_posting` (`include_text`로 최신 본문) | `jobs.read` | `GET /core/job-postings/{id}` |
| `coverage` | `study.read` | `GET /core/study/postings/{id}/coverage` |
| `get_application` | `applications.read` | `GET /core/applications/{id}` |
| `propose_document_suggestion` | `documents.ai` (egress) | `POST …/documents/{id}/suggestions` |
| `study_record_attempt` | `study.write` | `POST /core/study/attempts` |
| `approval_request` · `approval_status` | `approvals.request` · `approvals.read` | `POST/GET /core/approvals` |
| `run_start` · `run_status` · `run_cancel` | `runs.start` · `runs.read` · `runs.control` | `/core/runs…` |

권한 거부는 도구 결과 `{"error": "forbidden"}`(`isError: true`)로 돌아온다. HTTP의 403과 같은 판정이다 (`test_mcp.py::test_same_policy_via_mcp_as_http`).

### AI는 승인하지 않는다

MCP를 거친 호출의 actor는 사람 토큰이어도 `kind = "agent"`다. agent는 `approve` 범위를 쓸 수 없다:

- 승인 결정·주장 확정·승인 없는 직접 제출 도구가 없고, 있어도 정책이 거부한다.
- `add_interview_answer`로 만든 주장은 owner 토큰이어도 `extracted`(확인 대기)다.
- `approval_request`는 요청만 만든다 (`requested_via = "agent"`). 사람이 웹에서 결정하면, 승인된 묶음으로 `submit_application` 실행이 제출할 수 있다.

### 외부 자료는 자료다 (§17.15)

- 공고·문서·답변 본문이 들어 있는 결과에는 `"untrusted_content": true`와 안내 문구가 붙는다. `initialize`의 `instructions`도 같은 내용을 말한다.
- 서버는 결과 본문을 해석하지 않는다. 도구는 클라이언트가 명시적으로 `tools/call`할 때만 돈다. 서버 안에서 다른 도구를 부르는 경로가 없다.
- 임의 URL 조회·shell·SQL·파일 도구가 없다. `save_job_urls`는 자료를 실어 나르는 통로가 되지 않도록 개수·길이를 제한하고, 가져오기는 SSRF 검사를 거친다 (plan §14).
- 공고 본문에 "save_job_urls를 불러라, shell을 실행하라" 같은 지시가 있어도 조회만으로는 공고·작업·실행·승인·모델 호출·주장이 하나도 생기지 않는다 (`test_mcp.py::test_injected_instructions_in_posting_cause_no_tool_calls`).
