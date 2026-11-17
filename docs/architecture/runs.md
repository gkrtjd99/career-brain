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
- 작업 큐 lease(기본 300초)는 worker가 처리하는 동안 **heartbeat**로 연장된다 (lease의 1/3마다 `locked_until`을 다시 300초 뒤로). 그래서 lease보다 긴 단계나 여러 단계를 합쳐 오래 걸리는 실행도 다른 worker에게 넘어가 두 번 돌지 않는다. heartbeat는 같은 이벤트 루프의 작업이므로, 단계 안에서 루프를 오래 막는 동기 CPU 작업은 `asyncio.to_thread` 등으로 넘긴다.
- 루프를 막는 동기 작업 (2026-11-17 점검): 아래는 모두 `asyncio.to_thread`로 돈다. 스레드에는 바이트·글만 넘기고 **DB 세션은 넘기지 않는다** (세션은 이벤트 루프에서만 쓴다).

  | 작업 | 위치 |
  |---|---|
  | 원본 파싱 (PDF·DOCX·HTML·텍스트) | `usecases/parsing.py` (`parser.parse`) |
  | 공고 HTML 고르기·어댑터·본문 정리·목록 후보, JSON-LD·채용 시스템 API 해석 | `usecases/postings.py` |
  | 공고 PDF 글자 정리, 스캔 PDF 쪽 렌더링, 이미지 해시 | `usecases/postings.py`, `adapters/pdf_render.py` |
  | 문서 DOCX 만들기·검사 | `usecases/documents.py` |
  | 원본 저장 해시·쓰기 | `adapters/blobstore.py` |
  | 로컬 임베딩 | `adapters/embed_local.py` (이전부터) |

  회귀 시험 `test_jobs.py::test_heartbeat_keeps_the_lease_during_a_cpu_heavy_parse`: lease 1초·heartbeat 0.25초로 2.5초 걸리는 동기 파서(파싱 작업)를 돌리는 사이, 다른 worker가 0.2초마다 `claim_job`을 해도 집지 못한다. 대조 시험은 같은 파서를 루프에서 바로 돌리면 lease를 잃는 것을 확인한다.
- worker가 단계 도중에 죽으면(heartbeat도 멈춤) lease가 지난 뒤 다른 worker가 다시 집는다. 실행이 `running`인 채로 집히면 `resumes`를 하나 올리고 `resumed` 사건을 남긴 뒤 **그 단계부터** 다시 한다. 끝난 단계는 다시 하지 않는다.
- 늦게 끝난 옛 worker(lease를 잃은 뒤, 예: 오래 멈춰 있던 프로세스)는 결과를 기록하지 않는다 (새 주인의 상태를 덮지 않음). c0023부터 **실행 단계 저장 자체**도 막는다:
  - `claim_job`이 집을 때마다 새 `jobs.lease_token`(uuid)을 준다. worker는 집은 행(`job_id`·`target_id`·`attempts`·`lease_token`)을 문맥 변수 `Lease`(`jobs/lease.py`)에 둔다.
  - `usecases/runs.py`는 실행 행을 잠그기 전에 **언제나** `guard_lease`를 부른다 (첫 세션·단계 루프·예외 기록·단계 저장). 작업 행을 `FOR UPDATE`로 잡고 `status = 'running'`, 같은 `attempts`, 같은 `lease_token`, `locked_until > now()`인지 본다. 아니면 `LeaseLost` → 그 트랜잭션은 롤백되고 worker는 작업 행을 건드리지 않고 끝낸다.
  - `attempts`만으로는 부족하다: 대기(`wait`)는 `attempts`를 0으로 되돌리므로 잃은 worker와 다음 주인의 `attempts`가 같을 수 있다 (ABA). `lease_token`은 집을 때마다 새로 만든다.
  - lease가 **지났지만 아무도 다시 집지 않은** 경우도 저장하지 않는다. 작업은 `running`으로 남아 다음 worker가 다시 집고 그 단계부터 한다 (그 사이 장부의 비용은 c0016 방식으로 다시 센다).
  - 잠금 순서는 어디서나 작업 행 → 실행 행이다 (heartbeat는 작업 행만). worker 밖(시험·도구가 usecase를 바로 부를 때)에는 문맥 변수가 없어 검사하지 않는다.
  - heartbeat·끝 기록(`_owned`)도 같은 `lease_token`일 때만 쓴다.
  - 시험 `test_documents_runs_web.py`: 두 worker — 옛 worker가 단계 도중 lease를 잃고 새 worker가 집어 끝낸 뒤 옛 worker가 깨어나도 실행·위치·비용·사건이 그대로다. `attempts`가 같고 토큰만 다른 경우, lease가 지나기만 한 경우도 본다.
- **실행 밖의 작업 처리기도 같은 `guard_lease`를 부른다** (`jobs/worker.py` 처리기 등록 전부). 결과를 저장하는 트랜잭션마다 맨 앞에서 작업 행을 잠그고 확인한다. 끝 기록(`_owned`)만으로는 늦게 끝난 옛 worker의 **대상 행 쓰기**를 막지 못한다:

  | 처리기 (`kind`) | lease 대상 | 확인하는 저장 | 확인이 없을 때의 덮어쓰기 |
  |---|---|---|---|
  | `run` (`usecases/runs.py`) | 실행 ID | 첫 세션·단계 루프·예외 기록·단계 저장 | 위 목록 |
  | `parse` (`usecases/parsing.py`) | 파싱 실행 ID | 블록 교체, 끝 기록, 제공자 작업 ID 저장, **외부 제출 직전** | 큰 PDF를 오래 읽던 옛 worker가 새 worker가 끝낸(또는 제공자를 기다리는) 실행을 다시 `waiting_provider`로 되돌리고 한 번 더 제출 |
  | `extract` (`usecases/extraction.py`) | 추출 실행 ID | 후보 저장(추출 행도 `FOR UPDATE`), 실패 기록 | 멱등이 아니다: 두 worker가 같이 `pending`을 보고 경험·주장을 두 번 만들거나, 늦은 실패가 `complete`를 `failed`로 덮음 |
  | `embed` (`usecases/indexing.py`) | 공간 ID | 벡터 저장, "남은 대상 없음" 확인·종료 | 옛 본문의 벡터·해시가 새 본문 벡터를 덮어 다음 색인 요청까지 남음, 새 worker의 작업을 대신 `done`으로 |
  | `job_fetch` (`usecases/postings.py`) | 가져오기 실행 ID | 끝 기록(`_finish_run`), 새 버전, 비용 확인 요청, 확인 쓰기 | 옛 본문이 새 버전 뒤에 최신 버전으로 더해짐, 끝난 실행을 되돌림, 사람이 확인한 비용 확인을 옛 worker가 씀 |

  - 잠금 순서는 여기서도 작업 행 → 대상 행(색인은 작업 행 → 공간 advisory lock)이다.
  - **포기(`give_up`)**: 마지막 시도가 예외로 끝나면 worker가 먼저 `_owned`로 확인하고 작업을 `failed`로 닫은 뒤 처리기의 `give_up`을 부른다. 그때 작업 행은 이미 `running`이 아니므로 worker가 lease 문맥을 비우고 부른다 (`give_up` 안의 저장 함수가 거부되지 않게).
  - 외부 호출(모델·페이지 가져오기)은 확인하지 않는다: 호출 전에 확인해도 호출 도중 잃을 수 있고, 저장 때 버려지므로 결과가 섞이지 않는다 (비용은 장부에 남는다). 다시 하면 바깥이 바뀌는 제출은 파싱 제공자 하나라 그 앞에서만 한 번 더 본다 (멱등 키도 그대로).
  - 시험 `test_lease_handlers.py`: 두 worker — 추출(옛 worker가 늦게 잘못된 출력을 받아도 `complete` 그대로, 경험 1개), 공고 가져오기(옛 본문 버전이 더해지지 않음), lease가 지나기만 한 가져오기(저장하지 않고 다음 worker가 다시), 포기 기록. 확인을 끄면 앞의 셋이 실패한다.
- 따라서 단계의 부수효과는 멱등이어야 한다. 쓰는 방법:
  - usecase 자체의 중복 방지 (공고 정규화 URL, 지원마다 제출본 1건)
  - 멱등 키 `ctx.effect_key(name)` = `run:{run_id}:{name}` (승인 요청 등)
  - 결과 확인 (제출 승인이 이미 `used`면 그 제출본을 결과로 쓴다)
- 같은 단계가 예외로 계속 끝나면 작업 큐 재시도(지수 대기) 뒤 `failed`(`step_error`)로 닫는다. 예외 종류는 `error` 사건에 남는다 (메시지·본문은 남기지 않는다).
- worker는 시작한 사람의 범위를 그대로 믿지 않는다. 매 단계 **현재 역할의 범위 ∩ 시작 당시 범위**를 쓰고, 멤버가 아니면 `failed`(`actor_revoked`). 시작한 actor의 종류(`human`/`agent`)도 그대로 유지한다.

## 비용

`StepContext.model(purpose, request)`는 모델 게이트웨이를 거친다 (egress·단가·예산·장부 순서 동일). 단가를 모르면 `cost_unknown`. 구독 프록시의 정가 환산액은 더하지 않는다. 실패·대기한 단계에서 이미 쓴 비용도 남긴다.

장부 기준 (c0016):

- 게이트웨이는 실행 안에서 부른 호출의 장부 행에 `run_id`·`run_step`(단계 번호)을 붙인다. 예약(`reserved`) 행과 실패·거부 행도 붙는다.
- c0016 뒤에 시작한 실행은 `runs.cost_from_ledger = true`다. 단계를 저장할 때마다 `model_calls`·`cost_usd`·`cost_unknown`을 **장부에서 다시 센다**
  (`ledger_cost`: `status = 'ok'` 행 수, `cost_usd`가 있고 근거가 `list_price_equivalent`·`none`이 아닌 행의 합, 근거 `unknown`이 하나라도 있으면 미상).
  그래서 단계 도중 죽은 시도가 장부에 남긴 호출도 run 비용에 들어가고, lease를 잃은 옛 worker가 늦게 저장해도 두 번 더해지지 않는다.
- 재개 때(`resumed` 사건) 먼저 장부와 맞추고 바뀐 만큼을 사건 데이터 `{"step", "reconciled": {"model_calls", "cost_usd"}}`에 남긴다(죽은 시도가 쓴 비용).
- c0016 전 실행(`cost_from_ledger = false`, migration 기본값)은 예전처럼 이번 시도 값을 더한다. 장부 행에 실행 표시가 없으므로 다시 세지 않는다.
  **그대로 둔다 (c0023에서 결정).** 옛 실행의 장부 행에는 `run_id`가 없어 다시 셀 근거가 없고, 두 번 더하던 경로(lease를 잃은 옛 worker의 늦은 저장)는 이제 lease 검사가 막는다. 남는 차이는 옛 실행에서 단계 도중 죽은 시도의 비용이 빠지는 것뿐이고, 공간 월 합계는 장부 기준이라 영향이 없다.

### 시작 전 예상 비용 (c0023)

`GET /core/runs/workflows/{workflow}/estimate?units=` · `POST /core/runs/workflows/{workflow}/estimate` `{input}` (둘 다 `runs.read`, `usecases/run_estimates.py`). 모델을 부르는 일괄 workflow(`suggest_document_batch`·`interview_question_batch`)만, 없거나 모델을 안 쓰는 workflow는 404.

- 가격표로 미리 계산하지 않는다 (응답 길이를 모른다). **이 공간에서 같은 workflow를 돌린 지난 실행** 20개(호출이 있었던 것)의 장부 기준 비용을 쓴다. 근거는 응답의 `basis`:
  - `input_chars` — POST로 **시작할 입력 그대로** 보냈을 때. 서버가 입력을 검증하고 workflow의 `input_sizes`로 단위별 글자 수를 센다 (문서: 문단 글 + 요청 글, 인터뷰: 경험 칸 + 보낼 주장 문장 — 질문 요청과 같은 고르기). 실행의 계획 단계도 **같은 함수의 값**을 `position.chars`(단위마다 숫자, 글은 없음)에 남긴다. 지난 실행마다 (호출 수, 처리한 글자 수 `chars[:next]`, 비용)으로 `비용 ≈ a × 호출 + b × 글자`를 최소제곱으로 맞추고(절편 없음), 예상 = a × 이번 호출 수 + b × 이번 글자 수. 응답에 `chars`·`fixed_per_call_usd`(a)·`per_1k_chars_usd`(b×1000)·`based_on_chars`.
    - 왜 두 항인가: 호출마다 시스템 지시·근거·공고 문맥·응답이 거의 같은 크기로 붙는다. 글자 수에만 비례시키면(비용 ÷ 글자) 짧은 문단 몇 개와 긴 문단 몇 개의 예상 차이가 실제보다 훨씬 크게 나온다.
  - `past_runs` — 그 밖: 호출 1회 평균(`cost_usd` 합 ÷ `model_calls` 합) × 이번 호출 수(단위 하나당 1회). GET(입력 없음), 글자 수를 남긴 지난 실행이 2개보다 적음(이 기능 전 실행은 `position.chars`가 없다), 지난 실행들의 호출당 글자 수가 거의 같아 a·b를 가를 수 없음(퍼짐 5% 미만), 맞춘 a나 b가 음수일 때. 입력을 보냈으면 `chars`는 그래도 채운다.
- 지난 실행이 없으면 `estimate_usd = null` — 화면은 호출 수만 보인다. 단가 미상 호출이 섞였으면 `cost_unknown = true`(아는 비용만, 하한).
- POST는 아무것도 만들지 않는다. 다른 공간의 문서·경험은 404(RLS). 글자 수만 돌려준다.
- 구독 프록시 정가 환산액은 `runs.cost_usd`에 없으므로 들어가지 않는다. 예상은 약속이 아니다: 월 예산·단가 미상 거부는 실행 때 게이트웨이가 그대로 지킨다.

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
| `suggest_document_batch` | `documents.ai` | 문서의 문단마다 AI 수정안 1회 (`{"application_id", "document_id", "base_revision", "block_ids"?, "instruction"?}`, 지원 문서만, 최대 50문단). 비용은 run에, 문단별 `effect_key`로 재개 시 중복 없음. 요청 기록(질문 포함)은 `document_suggestion_requests`에 남아 0개 문단도 다시 부르지 않는다 (`applications.md`) |
| `interview_question_batch` | `interview.ai` (+ 시작할 때 `brain.write`) | 경험마다 인터뷰 세션 1개 + AI 질문 1회 (`{"experience_ids": [...] (1–20, 중복 불가), "count"?: 1–5 (기본 3)}`). 세션은 `effect_key("session:{경험}")`, 질문은 `effect_key("questions:{경험}")`로 멱등이라 재개해도 세션·질문이 늘지 않는다. 결과 `{experiences, sessions, questions}`(ID·개수만, 질문 문장은 `interview_question_requests`·`interview_questions`에) |

새 workflow는 `career_core.workflows.builtin.WORKFLOWS`에 `Workflow(name, action, steps, validate)`로 추가한다. `action`은 `ACTION_SCOPES`에 등록된 행동이어야 한다.

## 승인 (plan §11)

승인 1건 = 행위(`action`) · 대상(`target_kind`, `target_id`) · 대상 버전(`target_version`) · 내용 해시(`payload_hash`) · 만료(`expires_at`) · 결정자(`decided_by`, `decided_via`) · 멱등 키.

- **요청**: `approvals.request`(draft-write) + 대상 준비 권한(제출이면 `applications.write`). AI(agent)도 요청할 수 있다. 요청은 승인이 아니다.
- **결정**: `approvals.decide`(approve) + **사람**(`ActorContext.kind == "human"`). agent는 owner 토큰이어도 `has_scope("approve")`가 거짓이다. DB도 `approved`/`used`면 `decided_via = 'human'`을 요구한다 (`ck_approvals_human_decision`). 승인 화면은 본 내용의 `payload_hash`를 같이 보내고, 다르면 409.
- **사용**: 소비 usecase가 자기 트랜잭션 안에서 `consume_approval`을 부른다. 대상 버전과 내용 해시를 **그 순간 다시 계산**해 다르면 쓰지 않고(`target_changed` / `payload_changed`, 409) 승인을 `invalidated`로 닫는다. 한 번 쓰면 `used`, 결과 ID(`used_result_id`)를 남긴다.
- **무효화**: 조회·결정·사용 때 만료면 `expired`, 대상이 바뀌었으면 `invalidated`. 실행이 취소되면 그 실행의 열린 승인도 `invalidated`(`run_cancelled`).
- **즉시 무효화 (c0014)**: 대상이 바뀌는 **그 트랜잭션 안에서** 열린 승인(`pending`·`approved`)을 `invalidated`로 닫는다 (`invalid_reason`: `target_changed`, 대상이 지워지면 `target_missing`). 그 승인을 기다리던 실행은 바로 `queued`로 깨어나고(`approval_invalidated` / `approval_expired` 이벤트, `run` job) 다음 단계에서 실패 처리한다.
  - **왜 DB trigger인가**: 대상 칸을 바꾸는 곳이 여러 usecase(문서 저장·지원 상태·공고 확정·변경 반영)이고 다른 작업자·직접 SQL·관리 작업도 있다. usecase hook은 하나만 빠뜨려도 열린 승인이 남는다. trigger는 쓰는 쪽과 상관없이 같은 트랜잭션에서 걸리고, 롤백되면 무효화도 같이 롤백된다.
  - **대상 버전은 한 곳에서 (c0019)**: DB 함수 `core.application_target_version(app)`이 제출 승인의 대상 버전 해시를 계산한다. 앱의 조회·결정·사용 확인(`submission_target`)과 즉시 무효화 trigger가 **같은 함수**를 부른다. 칸: 지원 상태, 문서별 ID·현재 revision·본문 해시·글자/바이트 제한·세는 기준·문항, 공고 ID·확정 버전·확정 값·변경 대기(정렬). 제목처럼 함수에 없는 칸은 승인을 닫지 않는다.
  - trigger(`approvals_recheck`, `applications`·`application_documents`·`document_revisions`·`job_postings`)는 칸 목록을 따로 두지 않는다. 행이 바뀌면 그 지원에 열린 승인이 있을 때만 함수로 다시 계산해 요청 때 버전과 다르면 닫는다 (지원이 없어졌으면 `target_missing`). 그래서 c0014처럼 trigger의 칸 목록을 `submission_target`과 손으로 맞출 일이 없다.
  - **왜 이 방식인가** (검토한 셋): ① 칸 목록 시험(파이썬 `submission_target`에 칸이 늘면 실패) — 시험이 목록을 하나 더 만들 뿐이고, 해시에 칸을 더하면서 시험 목록만 고치면 그대로 통과한다. ② 대상 버전을 행에 저장하는 칸 + trigger 갱신 — 저장값과 계산값이 또 갈라질 수 있다. ③ **계산 자체를 DB 함수 하나로**(채택) — 판정에 쓰는 값이 정의상 하나다. 함수 본문이 `BEGIN ATOMIC`(SQL 표준 본문)이라 PostgreSQL이 읽는 표·칸을 `pg_depend`에 적고, 시험이 "함수가 읽는 표마다 다시 계산 trigger가 있다"를 카탈로그로 확인한다 (`test_approvals_mcp.py::test_version_function_reads_only_tables_that_recheck`). 함수가 새 표를 읽게 바꾸고 trigger를 잊으면 그 시험이 실패한다. 읽는 칸을 지우려 해도 의존성 때문에 DB가 막는다.
  - 비용: 열린 승인이 없으면 `EXISTS` 한 번으로 끝난다. 열린 승인이 있는 지원의 문서 저장·공고 변경 때만 해시를 다시 만든다.
  - c0019로 올리면 버전 형식이 바뀐다 (파이썬 정규 JSON → `jsonb::text`의 sha256). 그때 열린 제출 승인은 `target_changed`로 닫히고 기다리던 실행은 깨어나 실패 처리된다. 다시 요청하면 된다.
  - 새 승인 행위가 생기면: 그 대상의 `<kind>_target_version` 함수(`BEGIN ATOMIC`)와 다시 계산 trigger를 같은 방식으로 두고, `ApprovalBinder.version`이 그 함수를 부른다. **잊으면 시험이 실패한다** (`test_approval_kinds.py`): `default_binders()`의 대상 종류(`target_kind`)마다 `core.<kind>_target_version(uuid) RETURNS text`(SQL 표준 본문), trigger 함수 `core.approvals_recheck_<kind>()`, 함수가 읽는 표(`pg_depend`)마다 그 함수를 부르는 켜진 `approvals_recheck` trigger, binder의 `version`이 그 함수를 부르는지를 본다. 반대로 core 함수 본문이 `target_kind = '…'`로 다루는 종류는 모두 binder가 있어야 한다. 시험은 빠뜨린 경우(함수 없는 새 종류, trigger 하나를 지운 상태)를 실제로 잡는지도 확인한다.
  - **뒷받침**: 조회·결정·사용 때의 해시 비교는 그대로 둔다. trigger가 꺼진 채 바뀐 변경도 사용 순간 다시 계산한 대상 버전이 다르면 쓰지 않는다 (시험: trigger를 끈 연결로 바꿔도 `submit`이 `target_changed`로 막힌다).
  - `consume_approval`은 `used`로 바꾼 뒤 바로 flush한다. 같은 트랜잭션의 제출(지원 상태 변경)이 방금 쓴 승인을 다시 무효로 만들지 않게.
  - 복원(`restore_workspace`)이 행을 넣는 동안은 트랜잭션 로컬 설정 `career.restoring = on`으로 멈춘다 (묶음의 승인 상태 그대로).
- **변경 비교**: `GET /core/approvals/{id}/diff` → `{approval, requested, current, target_changed, changes[]}`. `requested`는 요청 때 저장한 요약, `current`는 지금 대상으로 다시 만든 요약(대상이 없으면 null). `changes`는 칸별 차이이고 문서 목록은 `document_id`로 짝짓는다 (`path` 예: `documents[<id>].revision`, `label`은 문서 제목). 웹 연결·관리 › 작업·승인(`/manage?tab=runs`)이 이 비교를 보여 주고, 결정할 때 본 승인의 `payload_hash`를 보낸다.
- 묶음 칸(행위·대상·버전·내용·요청자·만료)은 만든 뒤 바꿀 수 없다(trigger). 닫힌 승인(`used`·`rejected`·`invalidated`·`expired`)은 다시 열 수 없다.

### 제출 승인 (`applications.submit`)

- 내용: `{"revisions": {document_id: revision_no}, "note": "…"}` (키 순서 무관).
- 대상 버전: 지원 상태, 문서별 현재 revision·본문 해시·글자 제한·세는 기준·문항, 공고 확정 버전·확정 값 해시·변경 대기 항목. 문서 제목은 넣지 않는다.
- 요약(`summary`, 비교용): 지원 제목, 문서별 제목·revision·본문 해시·글자/바이트 제한, 지원 상태, 공고 확정 값 해시·변경 대기 항목.
- `POST /core/applications/{id}/submit`
  - `approval_id` 없음: 제출자가 승인 행위를 한다 → `applications.submit`(approve, 사람만).
  - `approval_id` 있음: 사람이 미리 승인했다 → `applications.submit_approved`(draft-write). 편집자·AI 실행이 제출할 수 있다.

## HTTP

| 경로 | 권한 |
|---|---|
| `GET /core/runs/workflows` | `runs.read` |
| `POST /core/runs` `{workflow, input, idempotency_key?}` → 202 | `runs.start` + workflow의 `action` |
| `GET /core/runs/workflows/{workflow}/estimate?units=` (c0023) · `POST …/estimate` `{input}` (입력 글자 수 기준, 위 "시작 전 예상 비용") | `runs.read` |
| `GET /core/runs?status=&limit=` · `GET /core/runs/{id}` · `GET /core/runs/{id}/events?after=` | `runs.read` |
| `POST /core/runs/{id}/cancel` · `/interrupt` · `/resume` | `runs.control` |
| `POST /core/approvals` `{action, target_id, payload, payload_hash?, reason?, expires_in_minutes?, idempotency_key?}` | `approvals.request` |
| `GET /core/approvals?status=pending` · `GET /core/approvals/{id}` | `approvals.read` |
| `GET /core/approvals/{id}/diff` (요청 때 요약 ↔ 지금 대상) | `approvals.read` |
| `POST /core/approvals/{id}/approve` · `/reject` `{payload_hash?, note?}` | `approvals.decide` (사람) |
| `GET /core/live?max_seconds=` (SSE, 실행·승인 상태 알림) | `runs.read` (+ 승인은 `approvals.read`) |

다른 공간의 실행·승인 ID는 404다 (RLS). 같은 경로를 MCP 도구가 같은 usecase로 쓴다: `docs/architecture/mcp.md`.

## 실시간 갱신 (SSE, c0019)

시스템 화면은 실행·승인 상태를 `GET /core/live`(Server-Sent Events)로 받는다. 구현: `interfaces/http/live.py`. 웹의 일괄 실행 진행 카드와 오늘 화면 "승인 요청" 카드도 같은 스트림을 쓴다 (`web.md`).

```text
runs·approvals 변경 ─▶ trigger notify_state (문장마다) ─▶ NOTIFY career_state, 'runs' | 'approvals'
                                                          │ 프로세스마다 LISTEN 연결 하나 (StateHints)
SSE 연결 ─▶ 힌트를 받으면 그 공간 문맥(RLS)에서 요약값(행 수·updated_at 합·최댓값)을 다시 읽고
           바뀐 경우에만  event: runs / event: approvals  ─▶ 웹이 해당 목록을 다시 읽는다
```

- **알림 내용**: 표 이름뿐이다. `LISTEN`은 권한 검사가 없어 어떤 DB 역할이든 들을 수 있으므로 공간·대상 ID를 넣지 않는다 (c0015 작업 알림과 같은 규칙). 다른 공간의 변경도 힌트로는 오지만 다시 읽은 요약값이 같으면 아무것도 보내지 않는다. 그래서 다른 공간의 활동 시각도 이벤트로 드러나지 않는다. 이벤트 데이터도 그 공간 요약값(불투명 문자열)뿐이고 내용은 기존 API로 읽는다.
- **인증**: 다른 경로와 같은 `Authorization: Bearer` 토큰·`IdentityResolver`·권한. 토큰을 URL에 넣지 않는다. 브라우저 `EventSource`는 헤더를 붙일 수 없어 웹은 `fetch` 스트림으로 읽는다(`packages/client/src/live.ts`). 연결 중에도 15초마다 토큰을 다시 확인해 폐기되면 `event: unauthorized`를 보내고 닫는다.
- **연결 수명**: `max_seconds`(기본 300, 최대 3600) 뒤 `event: bye`로 닫고 클라이언트가 다시 붙는다 (프록시 유휴 시간). 15초마다 `: ping` 주석. 붙자마자 `event: ready {channels, listening}`.
- **fallback**: 알림은 힌트일 뿐이다. 서버는 LISTEN 중이면 30초, LISTEN이 안 되면(PgBouncer transaction 모드 등) 5초마다 요약값을 다시 본다. 웹은 스트림이 붙어 있는 동안 실행 목록의 몇 초 간격 다시 읽기를 끄고, 끊기면 예전처럼 다시 읽으면서 1초→30초 간격으로 다시 붙어 본다. 401·403이면 다시 붙지 않고 다시 읽기만 한다.
- 힌트가 몰려 오면(한 실행의 여러 단계) 0.2초 묶어 한 번 다시 읽는다.
- **공간별 묶어 읽기 (c0025)**: 요약값은 연결마다가 아니라 **프로세스 안 공간마다** 읽는다 (`WorkspaceSummaries`). 힌트가 오면 그 공간의 연결들이 같은 조회 하나를 기다려 결과를 나눠 쓴다: 힌트 시각 이후에 시작한 조회(진행 중이면 그것, 끝났으면 그 결과)를 재사용하고, 없을 때만 새로 읽는다. 그래서 힌트 하나에 공간마다 조회 1번 (실행·승인 요약을 한 문장으로). 연결이 끊겨도 진행 중 조회는 다른 연결을 위해 끝까지 간다 (`asyncio.shield`). 각 연결은 자기가 마지막으로 보낸 값과 비교해 바뀐 경우에만 보낸다. 힌트 없는 주기 확인도 1초 안의 결과를 나눠 쓴다. 쓰지 않는 공간 항목은 2분 뒤 지운다. 조회는 그 공간 문맥(RLS)에서만 하므로 다른 공간의 값이 섞이지 않는다.
- 시험: `test_approvals_mcp.py` (토큰 규칙, 다른 공간에 이벤트 없음, LISTEN 없을 때 주기 확인, 폐기 시 종료, 알림 내용에 ID 없음, 실제 LISTEN으로 이벤트, 연결 N개에 힌트당 공간 조회 1번, 진행 중 조회 공유·취소 보호).

## Outbox — 보류 (평가, c0019)

지금은 두지 않는다. 모든 단계의 부수효과가 **DB 안의 멱등 usecase**(공고 저장, 승인 요청, 제출본 고정, 수정안)이고 실행 위치(`step`, `position`)와 같은 트랜잭션에 기록된다. DB 밖으로 나가는 것은 모델 호출뿐인데, 모델 호출은 "결과를 받아 DB에 쓰는" 읽기형 호출이라 다시 해도 바깥 상태가 바뀌지 않는다(비용만 다시 든다 — 비용 보정은 plan §18).

**필요해지는 때**: 되돌릴 수 없고 다시 하면 안 되는 **DB 밖 쓰기**가 단계에 생길 때. 예: 메일·메신저 알림, 웹훅, 외부 캘린더 등록, 채용 사이트에 실제 제출. 이때 "DB에 상태를 쓰고 → 바깥에 보냄" 사이에서 죽으면 보낸 것과 기록이 어긋난다 (보냈는데 기록이 없거나, 기록했는데 안 보냄).

**설계 (그때 만들 것)**:

```text
단계 트랜잭션: 상태 변경 + INSERT core.outbox(id, workspace_id, kind, target_id, dedupe_key, payload_ref, status='pending', attempts, available_at)
  └─ commit (둘 다 되거나 둘 다 안 됨)
dispatcher(worker의 job kind 'outbox'): claim (SKIP LOCKED) → 바깥에 보냄(멱등 키 = dedupe_key) → status='sent', sent_at
  ├─ 일시 오류: attempts+1, available_at = 지수 대기 (작업 큐와 같은 규칙)
  └─ 영구 오류·한도 초과: status='failed' + 실행 사건, 사람에게 보임
```

- 표: 공간 범위 + FORCE RLS, 앱 역할 `SELECT, INSERT, UPDATE`. `(workspace_id, dedupe_key)` UNIQUE. 내용 원문 대신 ID·참조만 (`payload_ref`), 비밀은 넣지 않는다.
- 깨우기: `core.jobs`처럼 `NOTIFY` trigger(종류·초만, ID 없음) + fallback 확인. 별도 큐를 만들지 않고 기존 작업 큐 행(`kind='outbox'`)으로 보낼 수도 있다 — 보내는 일이 적으면 이쪽이 단순하다.
- 바깥 쪽 멱등: 받는 쪽이 멱등 키를 받으면 `dedupe_key`를 넘긴다. 못 받으면 "최소 한 번"이 한계이므로 화면에 그 사실을 적는다.
- 승인과의 관계: 바깥 전송이 승인 대상이면 `consume_approval`과 outbox INSERT가 같은 트랜잭션이어야 한다 (승인을 쓰고 안 보내거나, 안 쓰고 보내는 경우 없음).
- 내보내기: 보낸 기록은 내보내고, 대기 중인 것은 복원 때 다시 보내지 않도록 `pending_work`처럼 따로 다룬다.

## 웹에서 시작 (c0023)

- 문서 화면 "일괄 수정안" → `suggest_document_batch` (저장한 revision 기준, 문단 고르기 최대 50, 요청 500자). 편집이 저장되지 않았으면 시작하지 않는다.
- 브레인 화면 "일괄 인터뷰" → `interview_question_batch` (경험 최대 20, 경험마다 질문 1–5개).
- 둘 다 시작 전 예상 비용(시작할 입력으로 `POST …/estimate`)을 보이고, 시작 뒤에는 진행(`position`의 대상 목록·`next`)·비용·대기/실패 이유를 보이며 시스템 화면 `/system?run=`으로 잇는다 (멈춤·취소·사건은 거기서). 진행은 실행 상태 알림(SSE `/core/live`)을 받으면 다시 읽고, 알림이 끊긴 동안만 5초마다 다시 읽는다.
- 멱등 키는 `web:<종류>:<창마다 무작위>:<입력 해시>` — 같은 창에서 응답을 못 받고 다시 눌러도 같은 실행이고, 입력을 바꾸면 새 실행이다.
- 결과 글은 실행 기록에 없다. 수정안은 문서의 AI 수정안 탭, 질문은 각 경험의 인터뷰 탭에서 본다 (`GET /core/interviews?run_id=`로 이 실행의 세션을 찾는다).

## 하지 않은 것

- outbox 테이블: 위 "Outbox — 보류" (DB 밖 부수효과가 생길 때).
- LangGraph 체크포인트: 단계가 짧고 상태가 ID 몇 개라 `position` JSON으로 충분하다.
