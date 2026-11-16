# 실행·중단·승인 (O08)

오래 걸리는 AI 작업과 여러 단계 작업을 **실행(run)** 하나로 묶고, 사람만 할 수 있는 결정은 **승인(approval)** 으로 묶는다.

```text
웹 / MCP / CLI ─▶ start_run ─▶ core.runs (queued) + core.jobs(kind=run)
                                    │
worker ─▶ claim_job ─▶ process_run ─┼─ 단계 실행 → 위치 저장(step, position) → 다음 단계 …
                                    ├─ 외부 한도: queued + wait_reason, 같은 작업을 retry_after 뒤에
                                    ├─ 승인 필요: waiting_approval ── 사람이 결정 ──▶ queued
                                    └─ done / failed(error_code) / cancelled / interrupted
```

## 상태

| status | 뜻 | 다음 |
|---|---|---|
| `queued` | 대기. `wait_reason`이 있으면 외부 한도 때문에 `retry_at`까지 기다리는 중 | worker가 집음 |
| `running` | 단계 실행 중 | 단계 사이에서 취소·중단 요청을 본다 |
| `waiting_approval` | `approval_id` 결정을 기다림. worker를 붙잡지 않는다 | 결정되면 `queued` |
| `interrupted` | 사용자가 잠시 멈춤 | `POST …/resume` → `queued` |
| `cancelled` · `failed` · `done` | 끝 (`finished_at` 필수, failed면 `error_code` 필수 — DB CHECK) | — |

실행 중인 단계는 강제로 끊지 않는다. `cancel`/`interrupt`는 `stop_requested`에 적고 worker가 단계가 끝난 뒤 반영한다. 대기·승인 대기 상태면 바로 반영한다.

## 재시작 후 재개와 멱등

- 단계가 끝날 때마다 `step`(다음 단계 번호)과 `position`(재개에 필요한 ID·번호)을 같은 트랜잭션에 저장한다.
- worker가 단계 도중에 죽으면 작업 큐 lease(기본 300초)가 지난 뒤 다른 worker가 다시 집는다. 실행이 `running`인 채로 집히면 `resumes`를 하나 올리고 `resumed` 사건을 남긴 뒤 **그 단계부터** 다시 한다. 끝난 단계는 다시 하지 않는다.
- 따라서 단계의 부수효과는 멱등이어야 한다. 쓰는 방법:
  - usecase 자체의 중복 방지 (공고 정규화 URL, 지원마다 제출본 1건)
  - 멱등 키 `ctx.effect_key(name)` = `run:{run_id}:{name}` (승인 요청 등)
  - 결과 확인 (제출 승인이 이미 `used`면 그 제출본을 결과로 쓴다)
- 같은 단계가 예외로 계속 끝나면 작업 큐 재시도(지수 대기) 뒤 `failed`(`step_error`)로 닫는다. 예외 종류는 `error` 사건에 남는다 (메시지·본문은 남기지 않는다).
- worker는 시작한 사람의 범위를 그대로 믿지 않는다. 매 단계 **현재 역할의 범위 ∩ 시작 당시 범위**를 쓰고, 멤버가 아니면 `failed`(`actor_revoked`). 시작한 actor의 종류(`human`/`agent`)도 그대로 유지한다.

## 비용

`StepContext.model(purpose, request)`는 모델 게이트웨이를 거친다 (egress·단가·예산·장부 순서 동일). 호출마다 `model_calls`를 올리고 종량제 실지출을 `cost_usd`에 더한다. 단가를 모르면 `cost_unknown`. 구독 프록시의 정가 환산액은 더하지 않는다. 실패·대기한 단계에서 이미 쓴 비용도 남긴다.

## 외부 한도 (§17.14)

| 오류 | 처리 |
|---|---|
| `rate_limited`, `timeout`, `connection_error` (재시도 가능) | `queued` + `wait_reason`, `retry_at` = `wait_base·2^n`초 뒤 (최대 15분). **같은 엔드포인트**로만 다시. `max_waits`(기본 6) 넘으면 `failed` |
| `quota_exceeded`(OpenAI 429 `insufficient_quota`), `budget_exceeded`, `price_unknown`, `egress_denied` | 바로 `failed`, `error_code`에 사유 |

게이트웨이는 라우트에 정한 엔드포인트 하나만 부른다. 다른 제공자나 유료 경로로 바꾸는 코드는 없다. Anthropic의 서버 측 거절 폴백(`refusal_fallback`)은 같은 제공자 안의 정책 거절 처리이며 한도 오류와 무관하다.

## 기본 workflow

| 이름 | 시작 권한 | 단계 |
|---|---|---|
| `save_job_urls` | `jobs.write` | URL 하나씩 저장 (`{"urls": [...]}`, 최대 50). 같은 공고는 기존 항목 |
| `submit_application` | `applications.write` | 제출 승인 요청 → 사람 결정 대기 → 승인으로 제출 (`{"application_id", "revisions", "note", "sensitive_ack"?}`). 민감 정보 경고가 있으면 `sensitive_ack`도 승인 내용에 들어간다 |
| `suggest_document_batch` | `documents.ai` | 문서의 문단마다 AI 수정안 1회 (`{"application_id", "document_id", "base_revision", "block_ids"?, "instruction"?}`, 지원 문서만, 최대 50문단). 비용은 run에, 문단별 `effect_key`로 재개 시 중복 없음 (`applications.md`) |

새 workflow는 `career_core.workflows.builtin.WORKFLOWS`에 `Workflow(name, action, steps, validate)`로 추가한다. `action`은 `ACTION_SCOPES`에 등록된 행동이어야 한다.

## 승인 (plan §11)

승인 1건 = 행위(`action`) · 대상(`target_kind`, `target_id`) · 대상 버전(`target_version`) · 내용 해시(`payload_hash`) · 만료(`expires_at`) · 결정자(`decided_by`, `decided_via`) · 멱등 키.

- **요청**: `approvals.request`(draft-write) + 대상 준비 권한(제출이면 `applications.write`). AI(agent)도 요청할 수 있다. 요청은 승인이 아니다.
- **결정**: `approvals.decide`(approve) + **사람**(`ActorContext.kind == "human"`). agent는 owner 토큰이어도 `has_scope("approve")`가 거짓이다. DB도 `approved`/`used`면 `decided_via = 'human'`을 요구한다 (`ck_approvals_human_decision`). 승인 화면은 본 내용의 `payload_hash`를 같이 보내고, 다르면 409.
- **사용**: 소비 usecase가 자기 트랜잭션 안에서 `consume_approval`을 부른다. 대상 버전과 내용 해시를 **그 순간 다시 계산**해 다르면 쓰지 않고(`target_changed` / `payload_changed`, 409) 승인을 `invalidated`로 닫는다. 한 번 쓰면 `used`, 결과 ID(`used_result_id`)를 남긴다.
- **무효화**: 조회·결정·사용 때 만료면 `expired`, 대상이 바뀌었으면 `invalidated`. 실행이 취소되면 그 실행의 열린 승인도 `invalidated`(`run_cancelled`).
- 묶음 칸(행위·대상·버전·내용·요청자·만료)은 만든 뒤 바꿀 수 없다(trigger). 닫힌 승인(`used`·`rejected`·`invalidated`·`expired`)은 다시 열 수 없다.

### 제출 승인 (`applications.submit`)

- 내용: `{"revisions": {document_id: revision_no}, "note": "…"}` (키 순서 무관).
- 대상 버전: 지원 상태, 문서별 현재 revision·본문 해시·글자 제한·세는 기준·문항, 공고 확정 버전·확정 값 해시·변경 대기 항목. 문서 제목은 넣지 않는다.
- `POST /core/applications/{id}/submit`
  - `approval_id` 없음: 제출자가 승인 행위를 한다 → `applications.submit`(approve, 사람만).
  - `approval_id` 있음: 사람이 미리 승인했다 → `applications.submit_approved`(draft-write). 편집자·AI 실행이 제출할 수 있다.

## HTTP

| 경로 | 권한 |
|---|---|
| `GET /core/runs/workflows` | `runs.read` |
| `POST /core/runs` `{workflow, input, idempotency_key?}` → 202 | `runs.start` + workflow의 `action` |
| `GET /core/runs?status=&limit=` · `GET /core/runs/{id}` · `GET /core/runs/{id}/events?after=` | `runs.read` |
| `POST /core/runs/{id}/cancel` · `/interrupt` · `/resume` | `runs.control` |
| `POST /core/approvals` `{action, target_id, payload, payload_hash?, reason?, expires_in_minutes?, idempotency_key?}` | `approvals.request` |
| `GET /core/approvals?status=pending` · `GET /core/approvals/{id}` | `approvals.read` |
| `POST /core/approvals/{id}/approve` · `/reject` `{payload_hash?, note?}` | `approvals.decide` (사람) |

다른 공간의 실행·승인 ID는 404다 (RLS). 같은 경로를 MCP 도구가 같은 usecase로 쓴다: `docs/architecture/mcp.md`.

## 하지 않은 것

- outbox 테이블: 지금 단계의 부수효과는 모두 DB 안의 멱등 usecase라 따로 두지 않았다. 알림·외부 전송처럼 DB 밖 부수효과가 생기면 추가한다 (plan §18).
- LangGraph 체크포인트: 단계가 짧고 상태가 ID 몇 개라 `position` JSON으로 충분하다.
