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
- 인터넷에 열 때는 HTTPS 프록시 뒤에 둔다 (`docs/operations/install.md`). 기본은 공간 범위 API 토큰이다. 외부 인가 서버의 OAuth 토큰을 받으려면 아래 "원격 연결 OAuth".

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
| `search_brain` (`include_study`로 학습 기록 포함, 아래) | `search.read` | `GET /core/search` |
| `list_experiences` · `get_experience` · `list_pending_claims` (쿼리 하나, 최대 200개·`truncated`) · `get_evidence` | `brain.read` | `GET /core/experiences…`, `/source-spans/{id}` |
| `start_interview` | `brain.write` | `POST /core/interviews` |
| `get_interview` (`session_id` 또는 경험의 최근 인터뷰) | `brain.read` | `GET /core/interviews…` |
| `add_interview_answer` | `brain.write` | `POST /core/interview-questions/{id}/answers` |
| `save_job_urls` (최대 10개, URL 500자 이하) | `jobs.write` | `POST /core/job-postings` |
| `get_posting` (`include_text`로 최신 본문) | `jobs.read` | `GET /core/job-postings/{id}` |
| `coverage` | `study.read` | `GET /core/study/postings/{id}/coverage` |
| `get_application` | `applications.read` | `GET /core/applications/{id}` |
| `check_application` (글자·바이트 수와 검사 결과는 이것만 쓴다) | `applications.read` | `GET /core/applications/{id}/checks` |
| `propose_document_suggestion` | `documents.ai` (egress) | `POST …/documents/{id}/suggestions` |
| `study_record_attempt` | `study.write` | `POST /core/study/attempts` |
| `approval_request` · `approval_status` | `approvals.request` · `approvals.read` | `POST/GET /core/approvals` |
| `run_start` · `run_status` · `run_cancel` | `runs.start` · `runs.read` · `runs.control` | `/core/runs…` |

`search_brain`은 기본으로 경험 근거(주장·블록·인터뷰 답)만 찾는다. `include_study: true`를 주면 학습 메모(`study_note`)·오답(`study_mistake`)을 더하고, 학습 결과마다 `record: "study"`, `note: "study, not experience"`를 붙인다 (응답에도 `include_study: true`). `kinds`로는 학습 종류를 고를 수 없다 (경험 종류만, 섞이지 않게). 학습 기록은 경험 근거가 아니므로 문서 근거로 쓰지 않는다 (c0021, `search.md`).

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

## 도구 호출 감사 기록

모든 `tools/call`은 `AuditSink` 포트로 한 줄을 남긴다 (기본 구현 `adapters/audit.py::DbAuditSink` → `core.audit_events`, c0014).

- 칸: 시각, actor(ID·`agent`), 경로(`mcp-http` / `mcp-stdio`), 도구 이름, 결과(`ok` / `error`), 오류 코드(`forbidden`·`invalid_arguments`·`unknown_tool`·`not_found` 등), 걸린 시간(ms), **인자 해시**(정규 JSON의 sha256).
- 원문 인자·결과·토큰은 저장하지 않는다. 같은 인자로 다시 불렀는지만 비교할 수 있다. 싱크는 정해진 칸 말고는 버린다.
- 표는 추가·조회만 된다 (앱 계정에 UPDATE·DELETE 권한 없음, FORCE RLS). 공간이 정해지지 않은 호출(인증 실패)은 공간 기록에 남지 않는다.
- 기록이 실패해도 도구 결과는 바뀌지 않는다 (경고 로그만).
- 보기: `GET /core/audit?limit=&before=&tool=&status=` (`audit.read`, 소유자) → 최근 기록 + 지난 24시간 도구별 호출·오류 수 + `retention_days`. 웹 `/system?tab=audit`.
- **보존 (c0019)**: `CAREER_AUDIT_RETENTION_DAYS`(기본 180일, `0`이면 계속 보관)보다 오래된 행을 주기 유지보수(`jobs/maintenance.py`의 `prune_audit_and_tokens` 단계)가 공간마다 지운다.
  - 앱 계정에는 여전히 DELETE 권한이 없다. 지우기는 소유자 권한 함수 `core.purge_audit_events(keep_days)`(SECURITY DEFINER, `search_path = pg_catalog`, PUBLIC 실행 권한 없음)로만 한다.
  - 함수는 **호출한 트랜잭션의 공간 문맥** 안의 행만, `keep_days`보다 오래된 것만 지운다 (FORCE RLS와 WHERE 두 겹). 공간 문맥이 없으면 0행. `keep_days < 7`이면 오류다 — 앱 계정이 뚫려도 최근 7일 기록은 지울 수 없다. 설정값 1–6은 7로 올린다.

## 원격 연결 OAuth (자원 서버만)

MCP Authorization 규격의 **자원 서버** 부분만 구현한다 (`interfaces/mcp/oauth.py`). 인가 서버는 이미 운영하는 것(Keycloak·Authentik·Auth0·Zitadel 등)을 쓴다. 이 저장소는 인가 서버를 만들지 않는다.

| 하는 것 | 내용 |
|---|---|
| 보호 자원 메타데이터 (RFC 9728) | `GET /.well-known/oauth-protected-resource/api/core/mcp` (와 경로 없는 형태): `resource`, `authorization_servers`, `scopes_supported`, `bearer_methods_supported: ["header"]` |
| 401 안내 | `WWW-Authenticate: Bearer resource_metadata="…", scope="…"` (토큰이 틀렸으면 `error="invalid_token"`) |
| 토큰 검증 | JWKS 서명(허용: RS256·PS256·ES256·EdDSA, `none`·HS* 거부), `iss` = 설정한 issuer, `aud` = 이 MCP 주소(RFC 8707, 또는 `CAREER_OAUTH_AUDIENCE`), `exp`·`nbf`(60초 여유), `sub` 필수 |
| 불투명 토큰 (c0019, 선택) | RFC 7662 introspection: JWT가 아닌 토큰(JWKS를 안 정했으면 모든 토큰)을 `CAREER_OAUTH_INTROSPECTION_URL`에 묻는다 (client_secret_basic). `active = true`, `iss`(있으면) = issuer, `aud`에 이 MCP 주소(필수), `exp`·`nbf`, `sub` 필수. 활성 결과만 최대 60초(만료 전까지) 메모리 캐시. API 토큰(`cbp_`)은 보내지 않는다 |
| 토큰 기록·폐기 (c0019) | 받은 토큰마다 `core.oauth_tokens`에 `jti`(없으면 토큰 sha256, 원문 없음)·처음/마지막 사용·횟수·만료를 남긴다. 소유자가 토큰 하나를 **폐기**하면 만료 전이라도 다음 요청부터 401. 연결을 끊으면 그 사용자의 모든 토큰이 401 (다시 연결해도 끊기 전 토큰은 계속 거부). 만료 하루 뒤 기록은 주기 유지보수가 지운다 |
| `jti` | 같은 `jti`가 **다른 연결**(다른 subject)로 오면 거부 (`jti_conflict`). `CAREER_OAUTH_REQUIRE_JTI=1`이면 `jti` 없는 토큰 거부 |
| 공간 | 토큰의 어떤 값도 공간 ID로 쓰지 않는다. `(issuer, sub)` → `core.oauth_identities` 연결 → (공간, actor). 연결은 공간 소유자(사람)만 만든다. 한 `(issuer, sub)`는 살아 있는 연결 하나 |
| 범위 | 토큰 `scope`(또는 `scp`) → 코어 범위: `career:read` → read, `career:write` → read+draft-write, `career:ai` → egress. **현재 역할 범위와 교집합**. `approve`·`workspace-admin`은 어떤 토큰 범위로도 줄 수 없다 (설정 단계에서 거부) |
| actor | 항상 `agent` (API 토큰 MCP와 같다) |

설정 (personal-api). `CAREER_OAUTH_ISSUER`가 있으면 켜지고 그때 `CAREER_PUBLIC_URL`과 `CAREER_OAUTH_JWKS_URL`(또는 `CAREER_OAUTH_INTROSPECTION_URL` + 클라이언트 ID·비밀)도 있어야 한다 (없으면 시작할 때 오류). 없으면 API 토큰만:

```sh
export CAREER_PUBLIC_URL=https://career.example.com          # 클라이언트가 보는 주소
export CAREER_OAUTH_ISSUER=https://auth.example.com/realms/career
export CAREER_OAUTH_JWKS_URL=https://auth.example.com/realms/career/protocol/openid-connect/certs
export CAREER_OAUTH_AUDIENCE=https://career.example.com/api/core/mcp   # 선택, 기본 = MCP 주소
# 선택: 불투명 토큰 introspection (RFC 7662). JWKS 대신 또는 함께. 비밀은 .env·비밀 저장소에만
export CAREER_OAUTH_INTROSPECTION_URL=https://auth.example.com/realms/career/protocol/openid-connect/token/introspect
export CAREER_OAUTH_CLIENT_ID=career-brain
export CAREER_OAUTH_CLIENT_SECRET=REPLACE_ME
export CAREER_OAUTH_REQUIRE_JTI=1   # 선택: jti 없는 토큰 거부
uv sync --extra oauth   # PyJWT (루트 개발 환경은 이미 포함)

CAREER_API_TOKEN=cbp_… personal-api oauth-link --subject <인가 서버의 sub>   # 연결
CAREER_API_TOKEN=cbp_… personal-api oauth-link                               # 목록
CAREER_API_TOKEN=cbp_… personal-api oauth-link --revoke <연결 ID>            # 끊기 (다음 요청부터 401)
```

인가 서버 쪽: 클라이언트(예: MCP 클라이언트)를 등록하고, 위 범위를 만들고, 액세스 토큰의 `aud`에 MCP 주소가 들어가게 한다 (Keycloak은 audience mapper, Auth0은 API identifier). introspection을 쓰면 이 서버용 기밀 클라이언트(introspection 권한)를 하나 더 만든다. API 토큰(`cbp_…`)은 계속 같이 쓸 수 있다.

웹 화면 (c0019): `/system?tab=oauth` "원격 연결" — 설정 요약, 연결 추가(sub·이름), 연결 목록·끊기, 최근 토큰(`jti`·사용 횟수·마지막 사용·만료)·폐기. 소유자(사람)만. API:

| 경로 | 내용 |
|---|---|
| `GET /core/oauth?limit=` | `{config: {enabled, issuer, resource, jwks, introspection, require_jti}, links[], tokens[]}` |
| `POST /core/oauth/links` `{subject, issuer?, label?}` → 201 | 연결 (issuer 생략 = 설정한 issuer). 다른 공간에 이미 있으면 409 |
| `POST /core/oauth/links/{id}/revoke` | 연결 끊기 `{revoked}` |
| `POST /core/oauth/tokens/{id}/revoke` | 토큰 하나 폐기 `{revoked}` |

### `jti` 재사용 감지: 한 것과 안 한 것

액세스 토큰(RFC 9068 JWT든 불투명이든)은 **bearer 토큰**이다. MCP 클라이언트는 만료될 때까지 요청마다 같은 토큰을 다시 보낸다. 그래서 "같은 `jti`가 두 번 오면 거부"(일회용 토큰, client assertion·DPoP proof의 `jti` 규칙)를 액세스 토큰에 적용하면 정상 클라이언트가 두 번째 요청부터 막힌다. 안전하게 할 수 있는 것만 했다:

- 같은 `jti`가 다른 연결로 오면 거부 (발급자 버그나 위조 시도의 신호).
- 사용 횟수·처음/마지막 사용 시각을 화면에 보여 준다. 모르는 사용이 보이면 그 토큰을 폐기하거나 연결을 끊는다.
- 폐기는 만료까지 유지된다 (폐기 기록은 만료 하루 뒤 지운다. 그 뒤 토큰은 서명 검증의 `exp`에서 막힌다).

탈취한 토큰의 재전송 자체를 막으려면 토큰을 클라이언트 키에 묶는 소유 증명(DPoP, RFC 9449)이 필요하다. 인가 서버와 MCP 클라이언트 양쪽이 지원해야 해서 지금은 하지 않는다 (plan §18 후속). 대신 짧은 토큰 수명(인가 서버 설정)과 폐기 목록으로 피해 시간을 줄인다.

하지 않은 것 (plan §18):

- 인가 서버·로그인 화면·동의 화면·동적 클라이언트 등록(RFC 7591). 운영 중인 인가 서버의 기능을 쓴다.
- 일회용 `jti`(위 이유), DPoP 소유 증명.
- 인가 서버 쪽 폐기의 즉시 반영: JWT는 서명만 보므로 인가 서버에서 폐기해도 여기서는 이 서버의 폐기 목록에 넣거나 만료될 때까지 통한다. introspection은 캐시 시간(최대 60초) 안에 반영된다.
- 토큰 교환·다른 서비스로의 토큰 전달(token passthrough 금지).
