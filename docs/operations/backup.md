# 백업·내보내기·복원 (O11)

두 가지 방법이 있다. 목적이 다르므로 서버 설치라면 둘 다 둔다.

| | 공간 내보내기 (`personal-api export`) | DB 백업 (`pg_dump`) + 원본 볼륨 |
|---|---|---|
| 단위 | 공간 하나 | DB 전체 (모든 공간·토큰 해시·migration 기록) |
| 누가 | 공간 소유자 (API 토큰, `workspace-admin`) | 서버 관리자 (DB 소유자 계정) |
| 들어가는 것 | 공간 자료 + 참조된 원본 파일, 행 수·해시 manifest | 전부 (검색 벡터·작업 큐 포함) |
| 복원 대상 | 같은 버전(또는 변환 단계가 있는 옛 버전) 묶음을 **빈** 공간: ID 그대로(같은 공간 ID가 없어야 함) 또는 `--new-ids`로 새 공간. 또는 `--merge`·웹 복원으로 **자료가 있는 공간에 합치기** | 같은 DB를 통째로 되돌림 |
| 쓰임 | 서버 이전, 개인 보관, 복원 훈련 | 장애 복구, 업데이트 전 백업 |

## 1. 공간 내보내기

```bash
export CAREER_API_TOKEN=cbp_...          # 소유자 토큰 (bootstrap에서 받은 것)
uv run --no-sync personal-api export --out career-$(date +%F).tar.gz
# Docker (app.env에 CAREER_API_TOKEN). 마운트한 폴더는 컨테이너 사용자(uid 10001)가 쓸 수 있어야 한다
mkdir -p backup && sudo chown 10001 backup
docker compose run --rm -v "$PWD/backup":/backup app export --out /backup/career-$(date +%F).tar.gz
```

웹(`/system` → 백업·유지보수)·API에서는 `GET /api/core/maintenance/export` (소유자만)로 같은 묶음을 내려받는다. 응답 헤더 `X-Archive-SHA256`이 파일 해시다.

- **흘려 쓰기**: 표는 한 줄씩 임시 파일에 쓰고, 묶음에도 그 파일을 조각씩 넣는다. 원본 파일은 한 번에 하나만 메모리에 둔다. 복원도 묶음을 임시 폴더에 조각씩 풀고 표 파일을 줄 단위로 500줄씩 넣는다. 메모리에 올리는 것은 manifest와, FK가 행 단위로 순환하는 표 묶음(지금은 없음)뿐이다. 임시 폴더는 시스템 임시 디렉터리(`TMPDIR`)에 만들고 끝나면 지운다 — 공간 크기만큼 여유가 있어야 한다.
- **암호화 (age, 선택)**: `--recipient age1…`(여러 번 가능)를 주면 묶음 전체를 age로 암호화해 쓴다. 평문 묶음은 임시 파일로만 있다가 지워진다. API는 `?recipient=age1…`. 공개 키만 서버에 주고, 신원 파일(비밀 키)은 서버 밖에 둔다.

  ```bash
  age-keygen -o ~/career-backup-key.txt          # 서버 밖에서. 출력의 "public key: age1…"
  personal-api export --out career.tar.gz.age --recipient age1...
  personal-api restore career.tar.gz.age --identity ~/career-backup-key.txt
  ```

  구현은 `age` 명령이 PATH에 있으면 그것, 없으면 `pyrage`(선택 설치 `career-core[age]`, 루트 개발 환경 포함). `CAREER_AGE_BACKEND=binary|pyrage`로 고른다. 비밀번호(scrypt) 방식은 두지 않았다 (명령행·환경변수에 남기 쉬움). `export_runs`의 해시는 받은 파일(암호문) 기준이다.
- **대기 작업**: 내보낼 때 큐에서 대기·실행 중이던 파싱(`parse`)·추출(`extract`)·공고 가져오기(`job_fetch`)·회사 페이지 가져오기(`company_fetch`, c0033)를 manifest `pending_work`에 `{kind, target_table, target_id, status}`로 적는다 (큐 표 자체는 넣지 않는다).
- **삭제 기록**: `personal-api export-deletions --out deletions.jsonl` (API `GET /api/core/maintenance/deletions`)는 삭제 기록만 작은 JSONL로 쓴다 (첫 줄 머리말 `{"format": "career-brain-deletions", "format_version": 1, "workspace_id", …}`, 그다음 줄마다 `{target_table, target_id, deleted_at}`). 묶음보다 자주 받아 둔다.

### 묶음 형식 (tar.gz, `format_version` 2)

```text
manifest.json                      형식·버전, core migration head, 만든 시각, 공간 id·이름,
                                   표마다 {행 수, sha256, 칸 이름·타입}, 원본 {key, 크기, 참조한 칸}, 뺀 표와 이유
tables/core.<table>.jsonl          한 줄 = 한 행 (PostgreSQL to_jsonb, UTC), 기본 키 순서
blobs/<sha256>                     행이 참조하는 원본 파일 (업로드 원본, 공고 스크린샷)
```

manifest의 `pending_work`(대기 작업)는 c0014에서 더한 선택 키다. 없는 묶음도 그대로 읽는다.
`tombstone_tables`(삭제 기록 대상 표 목록)는 형식 2에서 더했다 (c0018에서 대상 표가 4개 → 16개로 늘었다).
`proofs`(c0034)는 증빙 원본 표시다: `{sources: [원본 id], blobs: [파일 key]}`, 원본 목록의 해당 항목에도 `purpose: "proof"`. 묶음 안의 id 그대로라 새 ID로 복원하면 복원 뒤 id와 다를 수 있다. 읽기만 하는 안내 키라 없는 묶음도 그대로 읽는다.

#### 형식 버전 올리기 (`format_upgrade`)

옛 형식 묶음은 읽을 때 지금 형식으로 바꾼다. `career_core.usecases.export`의 `UPGRADES`에 한 단계씩 등록한다:

```python
@format_upgrade(1, "삭제 기록 대상 표 목록(tombstone_tables)을 manifest에 적는다")
def _upgrade_v1(arc: _Archive) -> None:
    arc.manifest.setdefault("tombstone_tables", sorted(_V1_TOMBSTONE_TABLES))
```

- 단계 하나 = `from_version` → `from_version + 1`. 받는 것은 푼 묶음(`_Archive`)이다: manifest는 dict로 바로 고치고, 표 파일은 `arc.rewrite(name, change)`(줄마다 dict → dict 또는 `None`=행 빼기)로 고친다.
- 읽을 수 있는 버전(`readable_versions()`) = 지금 버전 + 지금까지 단계가 끊김 없이 이어지는 옛 버전. 그 밖은 `version_mismatch`.
- 순서: 묶음 확인(형식·표 sha256·행 수, 옛 형식 그대로) → 단계 적용 → 표 sha256·행 수 다시 계산 → 나머지 확인(공간·migration head·칸·대기 작업). manifest에 없는 표 파일을 단계가 만들거나 지우면 `upgrade_failed`.
- 바꾼 묶음은 파일 해시가 묶음과 달라지므로, 복원 확인은 원래 해시 대신 **논리 해시**(행마다 정규 JSON sha256의 합, `--new-ids`와 같은 방식)로 한다. 보고에 `format_upgraded_from`과 노트가 남는다.
- 형식 1 → 2는 manifest 키 하나를 채우는 것뿐이다 (v1 단계는 사실상 아무것도 바꾸지 않는다). 테스트(`test_older_format_archives_are_upgraded`)가 합성 v1 묶음을 만들어 복원·합치기를 확인하고, 단계가 없는 v0은 거부되는지 본다.

#### 옛 migration head 묶음 (`schema_step`, c0024 · 하한 c0011)

형식 버전과 별개로, **core migration head가 다른 묶음**(그 사이 migration이 표·칸을 바꾼 묶음)도 읽는다. `SCHEMA_STEPS`에 migration revision마다 하나씩 등록한 단계를, 묶음의 head **다음** revision부터 이 설치의 head까지 차례로 적용한다.

| revision | 등록 | 하는 일 |
|---|---|---|
| c0012 | `schema_step` | `application_documents`에 `source_document_id`·`source_revision_id`(null), `document_suggestions`에 `idempotency_key`(null), 새 표 `application_stages`·`document_settings`(빈 표) |
| c0013 | `schema_step` | 새 표 `study_item_merges`·`study_settings` (빈 표. `study_items.status`는 값만 넓어짐) |
| c0014 | `schema_step` | 새 표 `audit_events`·`deletions` (빈 표) |
| c0015 | `no_data_change` | 작업 큐 NOTIFY trigger·유지보수 함수뿐 |
| c0016 | `schema_step` | `model_calls`에 `run_id`·`run_step`, `runs`에 `cost_from_ledger`(server default `false`), `interview_sessions`에 `idempotency_key`, `interview_questions`에 `request_id`, 새 표 `document_suggestion_requests`·`interview_question_requests` |
| c0018 | `no_data_change` | trigger·`export_runs.kind` 넓힘 (형식 2는 `UPGRADES`가 맡는다) |
| c0019 | `no_data_change` | 승인 함수·trigger, 내보내지 않는 표뿐 |
| c0020 | `no_data_change` | 유지보수 함수·내보내지 않는 표뿐 |
| c0021 | `schema_step` | `core.study_settings` → `core.workspace_settings` (이름 바꾸기, `timezone` 그대로, `tech_aliases = []`) |
| c0022·c0023 | `no_data_change` | `judge_cache`·`jobs.lease_token` (내보내지 않음) |
| c0024 | `no_data_change` | `restore_jobs`(내보내지 않음) |
| c0025 | `schema_step` | 새 표 `job_fetch_confirmations` (빈 표) |
| c0027 | `no_data_change` | `restore_jobs.started_by`·`near_choices` (내보내지 않음) |
| c0029 | `schema_step` | `parse_runs.hidden_text = 'keep'` |
| c0030 | `schema_step` | 옛 묶음에는 빈 `model_connections`·`model_route_overrides`·`model_settings` (키 암호문 표는 내보내지 않음) |
| c0031 | `schema_step` | `experiences`에 `kind = 'other'`·`parent_id = null`·`ongoing`·`tags = []`, `started_on`/`ended_on`은 migration과 같이 원문 기간이 분명할 때만 |
| c0032 | `schema_step` | `sources`에 `purpose = 'experience'` (옛 묶음의 원본은 모두 경험 추출 대상이었다). 원본 파일·버전 해시는 그대로 — 증빙 원본도 바이트 그대로 복원된다 |
| c0033 | `schema_step` | 새 표 `companies`·`company_pages`·`company_facts` (옛 묶음엔 빈 표), `job_postings.company_id = null` (옛 공고는 회사에 잇지 않은 상태) |
| c0034 | `schema_step` | 새 표 `credentials` (옛 묶음엔 빈 표) |
| c0035 | `schema_step` | 새 표 `reference_items`·`reference_notes` (옛 묶음엔 빈 표). manifest `reference_tables`가 참고 지식 표(회사·참고 글)를 표시한다 — 내 경험이 아니다 |
| c0036 | `schema_step` | 새 표 `profiles`·`profile_photos` (옛 묶음엔 빈 표). 사진 바이트는 `blob_key`로 `blobs/`에 들어가 복원 후 sha256이 같다. 합치기는 DB UNIQUE로 겹침을 찾고(프로필은 공간당 하나라 받는 공간 것이 남는다, 사진은 sha256), 기본 사진은 받는 공간 것이 그대로다 |
| c0037 | `schema_step` | 새 표 `application_preps` (옛 묶음엔 빈 표). 지원당 하나(DB UNIQUE)라 합칠 때 받는 공간의 준비 문서가 남는다. 다시 만들 수 있는 산출물이다 |
| c0039 | `schema_step` | `sources.purpose_pending = false`·`purpose_hint = null` (옛 자료는 모두 확인된 용도), `parse_runs.text_layer = null`, `document_blocks.bbox = null`. 이 기기에서 읽은 블록(`origin = device_ocr`)은 다른 블록처럼 묶음에 들어가고, 증빙 원본 그림은 바이트 그대로 복원된다 |
| c0040 | `schema_step` | 새 표 `education` (옛 묶음엔 빈 표). 합치기는 same_except |
| c0041 | `schema_step` | `profiles`에 병역·보훈·장애·희망 조건 칸 (옛 묶음엔 빈 값: 목록 칸은 빈 배열, 나머지는 NULL). restricted 칸이라 묶음은 지금처럼 사용자만 연다 |
| c0042 | `schema_step` | 새 표 `answer_assets`·`answer_asset_claims`·`answer_asset_uses` (옛 묶음엔 빈 표). 쓴 곳 기록은 내용만 합친다(겹침을 찾지 않는다) |
| c0043 | `schema_step` | `sources.origin_url = null` (옛 원본은 파일로 올렸다). 공고 버전 `method`에 `browser_capture`가 더해졌을 뿐 옛 버전은 그대로 |
| c0046 | `no_data_change` | `export_runs.kind`에 `markdown` (Markdown으로 내보내기 기록, CHECK만 넓힘) |
| c0047 | `schema_step` | 새 표 `resume_versions`·`resume_revisions`·`resume_line_claims` (옛 묶음엔 빈 표). 본문 해시는 ID 칸을 빼고 만들어 새 ID로 복원해도 근거 상태가 그대로다 |
| c0048 | `schema_step` | 새 표 `interview_preps`·`interview_prep_questions` (옛 묶음엔 빈 표). 합치기는 DB UNIQUE: 지원마다 준비 하나, 준비 안의 출처 열쇠(`source_key`) |
| c0049 | `schema_step` | 새 표 `portfolio_items`·`portfolio_item_revisions`·`portfolio_line_claims`·`portfolio_images`·`portfolio_bundles`·`portfolio_bundle_items` (옛 묶음엔 빈 표). 이미지 바이트는 `blob_key`로 `blobs/`에 들어가 복원 후 sha256이 같다. `github_tokens`는 묶음에 넣지 않는다 |

- 받는 head의 하한은 `SCHEMA_FLOOR = "c0011"`(내보내기가 처음 생긴 head, O11)이다. 그보다 옛 묶음은 있을 수 없다. c0011~c0017 묶음은 형식 1이라 먼저 `UPGRADES`로 형식 2가 된 뒤 head 단계를 지난다. 그보다 옛 묶음, 단계가 빠진 revision을 지나야 하는 묶음은 `version_mismatch: … no conversion step for cNNNN`. 이 설치보다 **새** 묶음(모르는 revision)도 `version_mismatch` — 먼저 설치를 올린다.
- 순서: 묶음 확인(받은 그대로의 해시) → 형식 변환(`UPGRADES`) → head 변환(`SCHEMA_STEPS`) → 표 sha256·행 수 다시 계산 → 대상 DB와 표 구성 비교. 단계가 만들거나 고친 표는 manifest의 칸 목록이 옛 것이므로 대신 **모든 행의 칸 이름이 대상 표의 칸과 정확히 같은지** 본다 (`schema_mismatch: <표> row N after conversion: missing […], unexpected […]`). 손대지 않은 표는 칸 이름·타입을 그대로 비교한다 (단계 없이 칸이 바뀐 표를 잡는다).
- 복원 확인은 논리 해시(행 정규 JSON)로 한다. 보고에 `schema_converted_from`(원래 head)과 노트 `archive migrations c0020 were converted to …`가 남는다. CLI·웹 복원·합치기 모두 같다.
- 시험: `test_archive_from_an_older_migration_head_is_converted`는 임시 DB를 c0020까지만 올려 거기서 **진짜 옛 묶음**(`study_settings` 행 포함)을 만들고, 지금 head로 새 ID 복원·합치기를 해 본다. `test_archive_from_heads_before_c0018_is_converted`는 c0011~c0016 head마다 임시 DB를 그 head까지 올려 합성 자료(실행·모델 장부·인터뷰 포함)로 묶음을 만들고(형식 1로 적음), 지금 head로 새 ID 복원과 합치기 dry-run(더할 행 0)을 해 본다.

**migration이 내보내는 표의 모양을 바꿀 때 (필수)** — 표를 더하거나 지우거나, 칸 이름·타입을 바꾸거나, 칸을 더하면 `career_core/usecases/export.py`에 같은 revision으로 단계를 더한다:

```python
@schema_step("c0030", "applications.note → applications.memo, 새 칸 priority 기본 0")
def _schema_c0030(arc: _Archive) -> None:
    def change(row):
        row["memo"] = row.pop("note", None)
        row["priority"] = 0  # 서버 기본값과 같은 값을 to_jsonb 모양으로
        return row

    arc.rewrite("core.applications", change)


@schema_step("c0031", "새 표 core.application_links (옛 묶음엔 빈 표)")
def _schema_c0031(arc: _Archive) -> None:
    arc.add_table("core.application_links")


no_data_change("c0032", "색인·함수만")  # 내보내는 표의 모양이 그대로면
```

- 도구: `arc.rewrite(표, 행 → 행|None)`, `arc.rename_table(옛, 새)`, `arc.add_table(표, rows=())`(빈 표·나눈 표), `arc.ensure_tables(표, …)`(없으면 빈 표), `arc.drop_table(표)`, `arc.iter_rows(표)`(합치기·나누기에서 다른 표 읽기), `arc.has(표)`.
- **칸 더하기**: `arc.add_columns(표, "server default를 쓰는 칸", …, 칸=값)`. 이름만 준 칸은 **대상 DB 카탈로그의 server default**(`pg_attrdef`)를 그 칸 값으로 넣는다 — 위 예의 `row["priority"] = 0`처럼 기본값을 코드에 다시 적지 않는다. DEFAULT가 없는 칸은 null. 기본값은 변환을 시작할 때 `read_column_defaults`가 대상 DB에서 한 번 읽어(`to_jsonb(<식>)`, 상수 식만) 묶음에 붙인다. `now()`·`gen_random_uuid()`처럼 상수가 아닌 기본값은 `upgrade_failed: default of <표>.<칸> is not a constant` — 그런 칸은 값을 직접 준다 (`칸=값`). 이미 있는 칸을 더하면 `upgrade_failed`. 하나의 기본값은 `arc.column_default(표, 칸)`. c0016(`runs.cost_from_ledger`)·c0021(`workspace_settings.tech_aliases`)이 이것을 쓴다; 시험은 쓴 기본값이 지금 head의 카탈로그 값과 같은지도 본다 (`test_schema_steps_cover_every_migration`, `test_column_defaults_from_the_catalog`).
- 값은 PostgreSQL `to_jsonb` 출력 모양으로 쓴다 (시각은 `2026-11-17T09:00:00+00:00`, numeric은 그대로). 복원 확인이 DB가 다시 내놓은 행과 비교한다.
- 칸 값이 null인 행에서도 깨지지 않게 쓴다. `test_schema_steps_cover_every_migration`이 migration을 하나씩 올리며 (1) `SCHEMA_FLOOR` 다음 모든 revision에 등록이 있는지, (2) `no_data_change` revision의 앞뒤 카탈로그가 같은지, (3) 단계를 앞 모양의 합성 묶음(칸이 모두 null인 행 하나씩)에 적용하면 뒤 모양의 표·칸이 되는지 본다. 등록을 빠뜨리거나 표시가 틀리면 이 시험이 실패한다.
- 내보내지 않는 표(`EXCLUDED_TABLES`)·함수·trigger·색인만 바꾸면 `no_data_change`.

- 표는 DB 카탈로그에서 찾는다: `core`의 `workspace_id`가 있는 표 전부 + `workspaces`(이 공간 행) + `actors`(이 공간 구성원). 새 migration이 표를 더하면 코드 수정 없이 들어간다. 빼려면 `career_core.usecases.export.EXCLUDED_TABLES`에 이유를 적는다 (`test_export.py`가 분류되지 않은 표를 잡는다).
- 표 해시는 그 줄 파일의 sha256이다. 복원 뒤 같은 방식으로 다시 읽은 값과 비교한다.
- 내보내기는 한 스냅샷(REPEATABLE READ, 읽기 전용)에서 읽는다. 쓰는 중에 바뀐 자료가 반쯤 섞이지 않는다.
- 원본 파일은 저장소에서 다시 읽어 sha256을 확인한 뒤 넣는다. 없거나 손상된 원본은 `missing_blobs`로 적고 경고한다 (내보내기는 계속).

### 들어가는 것 / 빠지는 것

| 자료 | 묶음 | 이유 |
|---|---|---|
| 원본·버전·파싱 결과·블록, 경험·주장·근거·인터뷰 | 들어감 | 브레인 원천 |
| 공고·버전(스냅샷)·가져오기 기록, 지원·문서·revision·수정안·제출본 | 들어감 | 제출본은 고정된 그대로 (복원도 INSERT만) |
| 학습 요건·항목·기록·응시·오답·복습·경험 후보 | 들어감 | |
| 모델 호출 장부 (`model_calls`) | 들어감 | 토큰 수·비용만 있고 프롬프트·응답·키는 원래 없다. 다른 행의 `model_call_id`가 가리킨다 |
| 내보내기·복원 기록 (`export_runs`) | 들어감 | 마지막 백업 시각 |
| 검색 벡터 (`embeddings`) | **빠짐** | 모델·차원에 따라 다르고 원문에서 다시 만든다. 복원 명령이 재색인 job을 넣는다 |
| 작업 큐 (`jobs`) | **빠짐** | 일시 상태. 대기 중이던 파싱·추출·공고 가져오기는 manifest `pending_work`로 적고 복원 때 다시 넣는다 |
| 삭제 기록 (`deletions`) | 들어감 | 사용자가 지운 행의 기록 (c0014·c0018 trigger, 아래 표 목록). ID와 시각만 |
| 감사 기록 (`audit_events`) | 들어감 | MCP 도구 호출 (인자 해시만) |
| 외부 로그인 연결 (`oauth_identities`) | **빠짐** | 설치·인가 서버마다 다시 연결한다 (`personal-api oauth-link`) |
| API 토큰 (`personal.api_tokens`), 세션 | **빠짐** | 비밀. 복원하면 새 토큰을 발급한다 |
| 모델 키 (`.env`, `app.env`), `models.toml` | **빠짐** | 설정 파일. DB에 없다 |
| 웹에서 만든 모델 연결·라우트·예산 (`model_connections`, `model_route_overrides`, `model_settings`) | 들어감 | 키 끝 4자리까지. c0030 |
| 웹에서 넣은 모델 키 암호문 (`model_connection_secrets`), 암호 키(`secret.key`) | **빠짐** | 비밀. 복원하면 연결은 남고 키는 "다시 넣어야 함"(`needs_reentry`) |
| 포트폴리오 항목·본문·이미지·묶음 (c0049) | 들어감 | 이미지 파일은 `blobs/`. 나만 항목·이미지도 들어간다(내 백업이다) |
| GitHub 토큰 암호문 (`github_tokens`, c0049) | **빠짐** | 비밀. 복원한 공간에서 비공개 저장소를 읽으려면 토큰을 다시 넣는다 |
| 서비스 계정·결제 (`platform`) | **빠짐** | career-service 소유 |

묶음에는 개인 자료가 그대로 들어 있다. `--recipient`로 암호화하거나 서버 밖에 암호화해서 둔다. 해시는 무결성 검사(손상·부분 수정)이고 서명이 아니다.

### Markdown으로 내보내기

노트 앱(Obsidian 같은)에서 폴더째 여는 **읽기 전용** Markdown 묶음(zip)이다. 위 백업 묶음과 목적이 다르다.

| | 백업 묶음 (`export`, tar.gz) | Markdown으로 내보내기 (zip) |
|---|---|---|
| 쓰임 | 복원·합치기·서버 이전 | 내 자료를 노트 앱에서 읽고 링크로 따라가기 |
| 다시 가져오기 | 됨 (`restore`, 웹 복원) | **안 됨** (ID·해시·표가 없다). 묶음 안 README에 적혀 있다 |
| 들어가는 것 | 공간의 모든 표 + 원본 파일 (증빙 포함, 바이트 그대로) | 확인한 사실과 근거 인용, 경험·내 글·지원·참고·자료 목록. 기본은 민감 정보·확인 전 후보·원본 파일 없음 |
| 증빙 서류 | 원본 바이트 그대로 (manifest `proofs`로 표시) | 제목·날짜만. 원본 바이트와 읽은 글(블록·OCR)은 어떤 선택에도 넣지 않는다 |
| "마지막 내보내기" | 셈 | 세지 않음 (`export_runs.kind = 'markdown'`, c0046) |

```bash
personal-api export-markdown --out career-notes.zip           # 기본 (확인한 사실만, 민감 정보 없음)
personal-api export-markdown --out career-notes.zip --sensitive --candidates --files
```

웹: 연결·관리 → 백업·유지보수 → "Markdown으로 내보내기". API: `GET /api/core/maintenance/export/markdown?sensitive=&candidates=&files=` (소유자만, `workspace.export`; 편집자·보기 권한은 403). 응답은 `application/zip`, `X-Archive-SHA256`, `Cache-Control: no-store`.

```text
README.md                     무엇인지·내보낸 때·수·"이 폴더는 다시 가져올 수 없습니다"
나/프로필.md                   이름·영문 이름·한 줄 소개·이메일·전화·링크
나/학력.md · 나/자격·어학·수상.md · 나/기술.md
경험/<경험>.md                 머리말(kind·org·role·period·tags·parent) + 확인한 사실과 근거(인용 + 원본 제목·쪽, 또는 인터뷰 답)
내 글/기본 문서/ · 내 글/공통 답변/ · 내 글/지난 제출본/   (제출본은 고정한 글 그대로)
지원/<지원>.md                 머리말(company·role·deadline·link·status·stage) + 공고 요약·전형·문서의 마지막 저장 글
참고/회사/ · 참고/참고 글/       머리말 purpose: reference + "근거로 쓰지 않습니다"
자료/목록.md                   올린 자료(제목·용도·날짜). 원본 파일 포함을 고르면 자료/원본/
```

- **선택** (모두 기본 꺼짐): 민감 정보 포함(`sensitive`: 생년월일·주소·병역·보훈·장애·희망 조건, 자격 번호 전체 — 아니면 끝 4자리, 등급 restricted인 참고 글·원본 파일), 확인 전 사실 포함(`candidates`: 확인 전 주장·회사 항목·참고 메모·자격 후보를 "확인 전"에 따로), 원본 파일 포함(`files`: 증빙과 용도 확인 전 자료는 늘 뺀다). 원본 저장소가 없는 설치에서 `files`는 400 `files_unavailable`.
- **근거의 원본이 나중에 증빙으로 바뀌었거나 용도 확인 전**이면 그 인용은 빼고 "증빙 자료라 인용은 넣지 않았습니다"로 원본 제목만 적는다. 보관·이의 제기한 사실은 넣지 않는다.
- **주민등록번호 모양**(`domain.sensitive`와 같은 규칙)은 노트 글 어디서든 뒤 6자리를 가린다 (`900101-1******`). 원본 파일 포함을 고른 경우의 원본 파일은 올린 바이트 그대로다.
- **이름**: 폴더 이름은 한국어 고정. 파일 이름은 NFC로 맞추고, Windows·노트 앱 링크에 못 쓰는 글자(`<>:"/\|?*#^[]`)는 `-`, 제어·보이지 않는 글자는 공백, 앞뒤 공백·점 빼기(`..`은 "제목 없음"), UTF-8 120바이트로 자르기, `CON`·`NUL` 같은 Windows 예약 이름 앞에 `_`. 같은 폴더에서 대소문자만 다른 이름도 겹친 것으로 보고 (이름, 만든 때, ID) 순서로 뒤의 것에 ` (2)`·` (3)`을 붙인다 — 같은 자료면 같은 이름이 나온다.
- **링크**: `[[경험/이름|보이는 제목]]` 꼴의 위키 링크(경로 포함). 머리말 값은 JSON으로 쓴 YAML이다(따옴표·콜론이 안전하다). 글 안의 `[[`는 링크가 되지 않게 `\[\[`로 쓴다.
- **크기**: 임시 파일에 한 파일씩 쓰고, 풀어 쓴 크기 합이 `CAREER_MARKDOWN_EXPORT_MAX_BYTES`(기본 512 MiB)를 넘으면 413 `too_large`로 멈춘다(반쯤 쓴 파일은 지운다). 원본 파일은 하나씩 읽고 해시를 확인한다.
- 다른 공간의 자료는 앱 계정(RLS) + 공간 문맥으로 빠진다. 모델을 부르지 않는다. 시험: `packages/core/tests/test_markdown_export.py`.

## 2. 복원 (빈 설치로)

```bash
uv run --no-sync personal-api migrate                  # 새 DB에 같은 버전 migration
uv run --no-sync personal-api restore career-2026-11-17.tar.gz --dry-run   # 검사만
uv run --no-sync personal-api restore career-2026-11-17.tar.gz
# → "restored workspace …: N rows, M blob(s), counts and hashes match" + 새 API 토큰 (한 번만 출력)
uv run --no-sync personal-api worker --once            # 또는 serve: 검색 재색인
# Docker: 빈 볼륨으로 docker compose up -d (migrate가 먼저 돈다) 후
docker compose run --rm -v "$PWD/backup":/backup:ro app restore /backup/career-2026-11-17.tar.gz
```

원본 디렉터리(`CAREER_BLOB_DIR`)는 비어 있거나 다른 공간 것만 있어야 한다. 같은 내용 주소의 파일은 그대로 둔다.

복원은 아래 순서로 하고, 하나라도 어긋나면 아무것도 남기지 않는다.

1. 묶음 확인: 정해진 이름의 일반 파일만(링크·경로 탈출·중복 거부), manifest가 맨 앞, 표 파일 sha256·행 수, 원본 sha256 = 이름.
2. 버전 확인: `format_version`(옛 형식은 단계로 바꿔 읽음), core migration head(옛 head는 `SCHEMA_STEPS`로 바꿔 읽음, 위). 표 목록과 칸 이름·타입도 대상 DB와 비교한다. 바꿀 수 없으면 거부(`version_mismatch` / `schema_mismatch`) — 이 설치보다 새 묶음이면 먼저 설치를 올린다.
3. 대상 확인: 같은 공간 ID의 자료가 하나라도 있으면 거부(`target_not_empty`). 덮어쓰거나 합치지 않는다.
4. 한 트랜잭션에서 FK 순서대로 INSERT (ID·시각·값 그대로). 앱 계정(`career_api`, RLS 적용)이 **그 공간 문맥으로만** 쓴다: 다른 공간 ID가 섞인 행은 RLS가 거부한다. 추가만 되는 표(제출본·revision·응시 등)도 INSERT만 하므로 권한을 넓히지 않는다. 순환 FK 2개(공고 ↔ 확정 버전, revision ↔ 수정안)는 c0011에서 `DEFERRABLE`로 바꿔 트랜잭션 안에서 검사한다.
5. 다시 읽어 표마다 행 수·sha256을 manifest와 비교. 같을 때만 원본 파일을 저장(sha256 재확인)하고 commit.
6. (`--deletions`가 있으면) 삭제 기록을 다시 적용, 대기 작업을 다시 큐에 넣기 (아래).
7. CLI가 재색인 job을 넣고, 소유자에게 새 API 토큰을 발급한다.

같은 묶음을 같은 대상에 ID 그대로 두 번 복원할 수 없다. 자료가 있는 공간에는 합치기(아래)를 쓴다.

### 새 ID로 가져오기 (`--new-ids`)

```bash
personal-api restore career.tar.gz --new-ids [--dry-run]
# → "imported with new ids (source workspace …)" + 새 공간 소유자의 새 토큰
```

- 묶음의 모든 기본 키(uuid) 값마다 새 UUID를 만든다 (공간·actor 포함). 그리고 모든 줄에서 그 값이 나오는 곳을 같은 규칙으로 바꾼다: FK 칸, FK 없는 참조(승인 대상·실행 입력·감사 actor), JSON 안의 ID와 ID를 키로 쓰는 맵(`{"revisions": {document_id: n}}`)까지. 묶음에 없는 UUID(바깥 ID)는 그대로 둔다. 유일 제약은 모두 바뀐 ID를 포함하므로 같은 설치에 사본을 만들어도 충돌하지 않는다.
- 확인: 넣은 행을 다시 읽어 **새 ID를 옛 ID로 되돌린 뒤** 행마다 정규 JSON(키 순서 무관)의 sha256을 더한 값(순서 무관 다중집합 해시)과 행 수를 묶음과 비교한다. 다르면 rollback.
- 결과 공간은 새 공간이다. 원래 공간과 같은 설치에 있어도 서로 보이지 않는다 (RLS).
- 알려진 차이 (의도한 결과): 승인의 `target_version`(문서 ID가 들어간 대상 버전 해시)·`payload_hash`(`{"revisions": {document_id: n}}`)처럼 **ID를 넣어 계산한 해시**는 새 ID로 다시 계산하면 맞지 않는다. 승인은 "그 ID의 그 상태"에 대한 동의이므로 새 ID에 옮겨 붙이지 않는다. c0024부터 가져온 공간의 열린 승인은 확인이 끝난 뒤 같은 트랜잭션에서 c0019 `recheck_application_approvals`로 다시 계산해 **바로** `target_changed`로 닫는다 (보고 `approvals_invalidated`와 노트, 다시 요청한다). ID 그대로 복원하면 해시가 그대로 맞으므로 묶음의 상태 그대로 둔다. 제출본·revision의 본문 해시는 ID를 담지 않아 그대로 맞다. 멱등 키 안의 ID도 같이 바뀐다 (같은 요청을 다시 보내면 새 요청이 된다).

### 기존 공간에 합치기 (`--merge`, 웹 복원)

```bash
export CAREER_API_TOKEN=cbp_...                      # 합칠 공간의 소유자 토큰
personal-api restore career-old.tar.gz --merge --dry-run          # 무엇이 들어갈지만 (아무것도 쓰지 않음)
personal-api restore career-old.tar.gz --merge [--on-duplicate skip|fail] [--deletions d.jsonl] [--identity key.txt]
# → "merged archive of <공간> into <공간>: N row(s) added, M duplicate(s) skipped, K blob(s)" + 표별 줄
```

토큰의 공간(`workspace.restore` 권한, 소유자)에 묶음을 **더한다**. 같은 공간의 예전 묶음(지운 것 되살리기)도, 다른 공간·다른 설치의 묶음도 된다.

- **기존 행은 고치지 않는다.** INSERT만 한다. 그래서 추가만 되는 표(제출본·revision·응시·스냅샷)도 불변 그대로다.
- **겹침 찾기**: FK 순서로 표마다, 묶음 행이 이 공간에 이미 있는지 아래 순서로 본다. 찾으면 그 기존 행의 ID로 바꾸고(자식 행의 FK도 따라 바뀐다) 넣지 않는다. 못 찾으면 새 UUID로 넣는다.
  1. 같은 ID (같은 공간의 예전 묶음). 이미 정한 FK 칸도 같아야 한다.
  2. DB의 UNIQUE 제약 (카탈로그에서 읽는다). 내용 주소·정규화 주소·멱등 키(`sha256`·`content_hash`·`canonical_url`·`idempotency_key`)가 든 것 먼저. 예: 공고는 정규화한 URL, 원본 버전은 sha256.
  3. DB 제약은 없지만 같은 것으로 보는 키: 블록은 (파싱 실행, 순번), 원본은 자식 버전의 sha256 (업로드 중복 방지와 같은 규칙).
  4. 같은 내용 (`content`): 기본 키를 뺀 모든 칸의 정규 JSON이 같다.
  5. 고치는 칸을 뺀 같은 내용 (`same_except:<칸>+…`, c0024): 표마다 나중에 바뀌는 칸(상태·진행·메모·`updated_at` 등)만 빼고 나머지(만든 시각·정체 칸 포함)가 같으면 같은 행. 묶음을 만든 뒤 상태·메모만 바뀐 행이 또 더해지지 않는다.
  6. **비슷한 행** (`near_duplicate`, 고를 때만): 5에서 정체 칸(아래 표)까지 빼고 같은 **기존** 행. 기본은 둘 다 둔다 (들어오는 행을 새 행으로, 지금까지와 같음). 보고 `near_duplicates`에 쌍마다 표·들어오는 ID·기존 ID·다른 칸·두 행의 정체 칸 값(칸마다 300자까지)과 처리(`keep_both`/`skip_incoming`)가 나온다. 합치기에 `near_duplicates=[{table, incoming_id, existing_id}]`로 "들어오는 행 건너뛰기"를 고른 쌍만 기존 행으로 잇는다 (자식 행의 FK도 기존 행으로). 정체 칸이 다른 쌍은 `--on-duplicate fail`의 겹침으로 세지 않는다.

  | 표 | 정체 칸 (비슷한 행에서 더 빼는 칸) |
  |---|---|
  | `experiences` | 제목·조직·역할·기간 |
  | `claims` | 문장·기여 |
  | `study_assessments`·`study_items` | 제목 |
  | `study_mistakes` | 문제·틀린 답·정답 |

  - 보고에는 앞 200쌍(`MAX_NEAR_DUPLICATES`)만 싣고 모두의 수는 `near_duplicates_total`. 보고에 없는 쌍은 고를 수 없다 (둘 다 둠).
  - 고른 쌍이 그 사이 맞지 않으면(확인 뒤 자료가 바뀌어 그 기존 행이 더 비슷한 행이 아니면) 아무것도 하지 않고 `near_duplicate_changed: <표> <들어오는 ID>`.
  - 기존 행끼리만 비교한다 (묶음 안 행끼리는 아님). 웹 복원은 확인 보고에서 쌍마다 고른다. CLI `--merge`는 고르지 않고 둘 다 두며, 쌍 수와 앞 10쌍을 출력한다.

  표별 내용 키 (`EXTRA_MERGE_KEYS`). 원칙: `created_at`과 정체를 정하는 칸(제목·문장·문제·입력)은 남기고, 사용하며 바뀌는 칸만 뺀다. 정체 칸을 고친 행은 다른 행으로 더해진다 — 겹침을 놓쳐 행이 하나 느는 쪽이, 다른 행을 잘못 합쳐 잃는 쪽보다 낫다.

  | 표 | 키 | 빼는 칸 (바뀌어도 같은 행) | 이유 |
  |---|---|---|---|
  | `document_blocks` | (파싱 실행, 순번) | – | 파싱 결과는 다시 만들 수 있는 파생 자료 |
  | `applications` | same_except | 제목·상태·결과 메모·제출 시각·`updated_at` | 지원은 진행하며 상태·메모가 바뀐다. 공고·공고 버전·만든 시각이 정체 |
  | `application_documents` | same_except | 제목·문항·제한·세는 규칙·현재 revision·`updated_at` | UNIQUE(지원, 문항 번호)는 문항 없는 문서(NULL)에 안 걸린다 |
  | `application_stages` | same_except | 순서·이름·일정·결과·결정 시각·메모·`updated_at` | 단계 기록을 고친다. 지원·제출본·종류·만든 시각이 정체 |
  | `extraction_runs`·`job_fetch_runs` | same_except | 상태·결과 수·오류·메모·`updated_at` | 실행하며 채워지는 칸 |
  | `experiences` | same_except | 상태·`updated_at`·종류·상위·시작/끝 달·진행 중·태그(c0031) | 확인과 정리(종류·상하·날짜·태그)만 바뀐다고 본다 (제목·조직·역할을 고치면 다른 행) |
  | `claims` | same_except | 상태·확인한 사람·확인 시각·`updated_at` | 문장·종류·기여가 정체 |
  | `company_pages` | same_except | 상태·오류·HTTP 상태·방식·최종 주소·제목·글·해시·가져온 시각·모델 호출·후보 수·메모·`updated_at` (c0033) | 가져오기 작업이 채우는 칸. 회사·입력 종류·주소·요청자·만든 시각이 정체 |
  | `company_facts` | same_except | 상태·확인한 사람·확인 시각·기준일·기준일 단위·`updated_at` (c0033) | 종류·본문·인용·인용 위치가 정체 (본문이나 인용을 고치면 다른 행) |
  | `credentials` | same_except | 상태·확인한 사람·확인 시각·원본·경험·파싱 실행·원문 위치·상세·`updated_at` (c0034) | 확인·연결만 바뀐다고 본다. 종류·이름·기관·번호·날짜·점수·시간이 정체 |
  | `reference_items` | same_except | 종류·제목·회사·직무·연도·출처 메모·자료 등급·본문·가린 곳 수·`updated_at` (c0035) | 원본·만든 사람·만든 시각이 정체 (가리기·태그 수정은 같은 행) |
  | `reference_notes` | same_except | 상태·확인한 사람·확인 시각·`updated_at` (c0035) | 참고 글·문항·문단 구성·소재·역량이 정체 (메모를 고치면 다른 행) |
  | `education` | same_except | 캠퍼스·전공·복수/부전공·상태·편입·입학/졸업 달·학점·만점·메모·증빙·경험·`updated_at` (c0040) | 학교와 학위 과정이 정체 (같은 학교의 학사·석사는 다른 행) |
  | `answer_assets` | same_except | 종류·`updated_at` (c0042) | 제목과 본문이 정체 (글을 고치면 다른 행) |
  | `portfolio_items` | same_except | 제목·공개 범위·기술 태그·링크·최신 revision·`updated_at` (c0049) | 경험·만든 사람·만든 시각이 정체. 본문은 `UNIQUE (item_id, revision_no)`, 이미지는 `UNIQUE (item_id, sha256)`으로 겹침을 찾는다 |
  | `portfolio_bundles` | same_except | 제목·직무·소개·이은 지원·revision·`updated_at` (c0049) | 종류·위 묶음·복사한 revision·만든 사람·만든 시각이 정체. 묶음 항목은 `UNIQUE (bundle_id, item_id)` |
  | `resume_versions` | same_except | 제목·목표 직무·템플릿·블라인드·쪽 수 한도·최신 revision·이은 지원·`updated_at` (c0047) | 종류·위 판본·복사해 온 revision·만든 사람·만든 시각이 정체. revision은 `UNIQUE (version_id, revision_no)`, 줄 근거는 `UNIQUE (revision_id, line_id)`로 겹침을 찾는다 |
  | `interview_sessions` | same_except | 상태 | 멱등 키 UNIQUE는 NULL이면 안 걸린다 |
  | `runs` | same_except | 상태·진행·결과·대기·비용·시작/끝 시각·`updated_at` 등 | workflow·입력·실행자·만든 시각이 정체 |
  | `approvals` | same_except | 결정·사용·무효 칸·`updated_at` | 행위·대상·대상 버전·내용 해시·요청 시각이 정체 |
  | `document_suggestions` | same_except | 상태·거절 이유·적용한 revision·`updated_at` | 검토 결과만 바뀐다 |
  | `study_assessments`·`study_items` | same_except | 상태·일정·메모·묶은 시험·완료 시각·`updated_at` | 제목·종류·요건·만든 시각이 정체 |
  | `study_attempts`·`study_sessions` | same_except | 메모(·자료) | 기록 값은 그대로 |
  | `study_mistakes` | same_except | 복습 칸(상태·반복·간격·쉬움·마감·마지막 복습)·이유 메모·`updated_at` | 문제·답·이유가 정체 |

  키 없이 같은 내용이면 되는 표(`CONTENT_ONLY_TABLES`): 감사 기록·인터뷰 답·모델 장부·복습 기록·요청 기록·공통 답변의 쓴 곳(c0042)(추가만, 고치지 않음), 주장 근거(CHECK로 범위·답 중 하나만 있어 UNIQUE 하나가 늘 걸린다). 그 밖의 표는 NULL 없는 DB UNIQUE가 있다. `test_merge_keys_cover_keyless_tables`가 새 표가 분류됐는지, 키 칸이 있고 칸 목록 키가 NOT NULL인지 본다.

  키 칸은 부모 표의 결정이 끝난 뒤의 값(바뀐 ID)으로 비교한다. 묶음 안에서 서로 겹치는 행도 잡는다.
- **겹침 처리 (`--on-duplicate`)**: `skip`(기본)은 기존 행을 그대로 두고 건너뛴다. `fail`은 하나라도 겹치면 아무것도 하지 않고 `duplicates_found: <표> <수>, …`로 멈춘다.
- **넣지 않는 표**: 공간(대상 공간 그대로), 구성원(`memberships`), 사람(`actors`: 묶음의 사람은 대상 공간의 같은 ID 구성원이면 그대로, 아니면 합치는 사람으로 바꾼다), 백업 기록(`export_runs`: 합치기는 이 공간에 `kind=merge`로 남는다), 묶음 공간의 삭제 기록. 보고의 `not_merged`에 이유가 있다.
- **한 트랜잭션** (REPEATABLE READ): 겹침 찾기 → 넣기 → 확인 → 삭제 기록 → 대기 작업 → 원본 저장 → commit. 어디서든 실패하면 rollback. 넣다가 제약에 걸리면 `merge_conflict: <제약>`. FK는 모두 바뀐 ID로 맞춰지고, DEFERRABLE FK는 commit 전에 검사된다.
- **확인**: 넣기 전후로 표마다 순서 무관 다중집합 해시(행 정규 JSON sha256의 합)를 잰다. `넣은 뒤 − 넣기 전 = 넣은 행의 해시`이고 행 수도 맞아야 한다 — 기존 행이 하나도 바뀌지 않았다는 확인이다. 어긋나면 `verify_failed`.
- **삭제 기록** (`--deletions`): 이번에 **넣은 행**에만 다시 적용한다. 원래 있던 행(겹침)의 삭제 기록은 `kept_existing`으로 건너뛴다 (합치기는 기존 자료를 지우지 않는다).
- **대기 작업**: 넣은 행의 것만 다시 넣는다. 원본 파일은 아직 참조되는 것 중 이 공간에 없던 것만 저장한다.
- 승인 요청(`approvals`)은 넣는 동안 대상 변경으로 닫히지 않는다 (`career.restoring`, 전후 해시 확인이 기존 행 불변을 보므로). 대신 확인이 끝난 뒤 **같은 트랜잭션에서** 넣었거나 겹친 지원마다 c0019 `recheck_application_approvals`(대상 버전 함수 `application_target_version`)를 불러, 요청 때와 버전이 달라진 열린 승인을 `target_changed`로 닫는다 (c0024). 예: 내보낸 뒤 지운 지원 문서를 합치기가 되살리면, 그 사이 요청한 제출 승인은 닫힌다. 넣은 승인도 대상(새 ID의 지원)의 버전이 달라 같이 닫힌다. 바뀐 것이 없으면 그대로 둔다. 보고 `approvals_invalidated`와 노트 (dry-run도 수를 보여 주고 되돌린다).
- 버전: 묶음 형식·옛 core migration head 모두 위 단계로 바꿔 읽는다 (복원과 같음).

### 웹 복원 (`/system` → 백업·유지보수 → 복원)

소유자만 (`workspace.restore`). 묶음(`.tar.gz`, 암호화했으면 `.age`)과 선택으로 삭제 기록 `.jsonl`을 올리고, **확인(dry-run) → 확인 대화상자 → 합치기** 순서로 한다. 확인 결과(더할 행·건너뛸 행·원본 수·표별 겹침 기준·건너뛸 삭제 기록과 이유)를 본 뒤에만 합칠 수 있다.

| 요청 | 하는 일 |
|---|---|
| `POST /api/core/maintenance/restore` (본문 = 파일 그대로, `Content-Type: application/octet-stream`·`application/gzip`, 선택 `X-Filename`) | 임시 폴더에 흘려 받고 sha256을 잰다 → 작업(201) |
| `GET …/restore` | 이 공간의 지금 작업 (없으면 404). 화면을 다시 열면 이어 본다 |
| `POST …/restore/{id}/deletions` (본문 = jsonl) | 삭제 기록을 붙인다 (머리말·공간 검사, 잘못되면 400) |
| `POST …/restore/{id}/verify` `{on_duplicate, identity?}` | worker 큐에 dry-run을 넣는다 (202, 단계 `queued`) |
| `GET …/restore/{id}` | 상태 `uploaded`→`verifying`→`verified`→`applying`→`done`/`failed`, 단계(`read`·`decrypt`·`match`·`insert`·`verify`·`deletions`·`blobs`·`commit`)와 진행 수, 보고, 실행 중이면 `heartbeat_at` |
| `POST …/restore/{id}/apply` `{on_duplicate, identity?, archive_sha256, near_duplicates?}` | 확인한 파일·정책과 같을 때만 worker 큐에 합치기를 넣는다 (아니면 409 `not_verified`·`archive_changed`·`policy_changed`). `near_duplicates`: 확인 보고의 비슷한 행 쌍 중 들어오는 행을 건너뛸 것 `[{table, incoming_id, existing_id, decision: "skip_incoming"}]` (보고에 없는 쌍은 409 `near_duplicate_unknown`, 1000개까지) |
| `DELETE …/restore/{id}` | 작업과 임시 파일 지우기 (큐에서 기다리는 작업이면 큐 작업도 닫는다) |

- **본문 상한**: 이 업로드 경로(`POST /api/core/maintenance/restore`) 하나만 `CAREER_MAX_RESTORE_MB`(기본 2048)을 쓴다. 다른 경로는 `CAREER_MAX_UPLOAD_MB` 그대로. 넘으면 413 `too_large` (받던 파일은 지운다). 리버스 프록시 상한(`client_max_body_size` 등)도 같이 올린다.
- **작업 폴더만**: 올린 파일은 `CAREER_RESTORE_DIR`(개인 앱 기본 `data/restore`, 이미지 `/data/restore`. 설정이 없는 조립은 시스템 임시 디렉터리)의 `career-restore-upload-<작업 ID>` 폴더에만 둔다 (권한 0700). 합치기가 끝나거나(`done`), 지우거나, 1시간 쓰지 않으면 폴더째 지운다. 작업을 읽거나 돌리거나 받는 동안 폴더 시각을 고치고, 서버 시작·새 업로드 때 그 시각이 1시간 넘은 폴더를 지운다 (다른 프로세스·비정상 종료가 남긴 것 포함). 폴더 수 상한은 8개(넘으면 429). 원본 저장소에는 합치기가 commit할 때 필요한 원본만 들어간다. 지워진 파일로 확인·합치기를 시작하면 410 `upload_missing`(다시 올린다).
- **age 신원**: 확인·합치기 요청 본문으로만 보낸다 (형식이 틀리면 바로 400 `invalid_identity`). API가 작업 폴더에 0600 파일(`identity-key.txt`)로 두고, worker가 쓴 뒤 지운다 — worker가 집기 전까지(큐에서 기다리는 동안) 폴더에 남는다. 작업 지우기·끊김 정리·폴더 정리 때도 지운다. DB에는 저장하지 않는다.
- **작업 상태는 DB에 있다** (c0024 `core.restore_jobs`, 공간마다 하나, FORCE RLS). 어느 API 프로세스로 요청이 가도 같은 작업을 보고, 서버를 다시 시작해도 남는다. 진행 중이면 같은 공간의 새 작업은 409 `restore_busy`. 확인·합치기 시작은 조건부 UPDATE라 두 프로세스가 같이 시작하지 못한다.
- **확인·합치기는 worker가 돈다** (c0027). 시작 요청은 상태를 바꾸는 조건부 UPDATE와 같은 트랜잭션에서 작업 큐에 `restore` 작업(대상 = 복원 작업 ID)을 넣고 202를 돌려준다. 그래서 API 프로세스가 큰 묶음을 풀고 합치는 동안 묶이지 않고, API를 다시 시작해도 작업은 계속된다. worker는 시작한 사람(`started_by`)의 **지금** 역할로 합친다 (그 사이 소유자가 아니면 `failed`/`forbidden`). worker가 없으면(`serve --no-worker`만 띄움) 작업은 `queued`에 머문다 — 화면에서 취소할 수 있다. 실행 중에는 `CAREER_RESTORE_HEARTBEAT_SECONDS`(기본 2)초마다 진행 수와 생존 신호(`heartbeat_at`)를 DB에 쓴다. 큐에서 기다리는 작업은 오래 기다려도 끊긴 것이 아니다. 실행 중인데 신호가 `CAREER_RESTORE_STALE_SECONDS`(기본 60)초 넘게 없으면 (worker가 집은 뒤 lease도 지났거나 큐 작업이 없으면) 읽는 쪽이 `failed` / `interrupted`("stopped during <단계>")로 닫는다 — worker 재시작·죽은 프로세스. 닫힌 작업은 worker가 나중에 집어도 돌지 않는다. 자동 재시도는 하지 않는다. 합치기는 한 트랜잭션이라 끊기면 아무것도 바뀌지 않았다. 파일은 남아 있으므로 같은 작업을 다시 확인하면 된다 (commit 직후 끊겼다면 다시 확인이 "더할 행 0"을 보여 준다).
- **API와 worker가 같은 폴더를 본다**: `CAREER_RESTORE_DIR`를 API·worker 모두가 읽고 쓰는 같은 폴더(같은 볼륨, 예: `/data/restore`)로 둔다. 서비스 조립은 `CoreDeps(restore_dir=…)`와 worker의 `restore_handlers(RestoreDeps(root=…))`로 준다. 폴더가 프로세스마다 다르면 worker가 파일을 못 찾아 `upload_missing`이 된다. 합치기의 원본 풀기(staging)도 그 작업 폴더 안에서 한다 (컨테이너의 작은 `/tmp` tmpfs를 쓰지 않는다).
- 화면: 확인이 끝나면 비슷한 행 쌍(위 6)을 두 행의 정체 칸 값과 함께 보여 주고, 쌍마다 "둘 다 두기"(기본)·"들어오는 행 건너뛰기"를 고른 뒤 합친다. 끝난 보고에는 고른 처리가 남는다. 묶음 올리기는 XHR `upload.onprogress`로 보낸 바이트·퍼센트를 보여 주고 멈출 수 있다 (fetch 본문은 진행률이 없다). 받은 뒤에는 1초마다 상태를 읽어 단계와 진행 막대를 보여 준다. 화면을 다시 열면 `GET …/restore`로 지금 작업을 이어 보고, 파일을 다시 고르지 않아도 서버에 남은 파일로 다시 확인할 수 있다. 끝나면 유지보수 정보에 "마지막 합치기"가 보인다. 상한은 그대로 이 경로만 `CAREER_MAX_RESTORE_MB`.
- 원본 저장소가 없는 설치(`restore_available=false`)에서는 화면에 CLI 안내만 나온다.

### 대기 작업 다시 넣기

manifest `pending_work`의 각 항목을 대상 행(`parse_runs`·`extraction_runs`·`job_fetch_runs`·`company_pages`)이 복원된 공간에 있을 때만 `queued` job으로 넣는다 (같은 종류·대상이면 한 번, 새 ID면 바뀐 대상 ID로). 결과는 복원 보고 `requeued`와 CLI 출력 `re-queued N parse job(s)`. worker(`serve` 또는 `worker`)가 처리한다.

### 삭제 기록 다시 적용 (`--deletions`)

사용자가 행을 지우면 trigger가 `core.deletions`에 (표, ID, 시각)을 남긴다 (추가만). 대상 표:

| 부터 | 표 |
|---|---|
| c0014 | `sources`, `job_postings`, `applications`, `study_items` |
| c0018 | `experiences`, `claims`, `claim_evidence`, `interview_sessions`, `interview_questions`, `application_documents`, `application_stages`, `study_requirements`, `study_requirement_claims`, `study_assessments`, `study_sessions`, `study_mistakes` |

- **그 공간 문맥의 삭제만** 남긴다: 공간 통째 삭제와 문맥 없는 관리 작업은 빠진다 (c0014 규칙 그대로).
- **직접 지운 행만** 남긴다 (c0018): FK CASCADE로 같이 지워진 행(부모가 이미 없음)은 남기지 않는다 — 부모 기록을 다시 적용하면 같이 지워진다. trigger 인자로 표마다 CASCADE 부모 (칸, 표)를 주고, 지울 때 그 부모가 아직 있는지 본다. 새 CASCADE FK를 더하면 migration의 목록도 고친다 (`test_deletion_log_covers_user_deleted_tables`가 DB의 FK와 비교한다).
- 파생 자료(블록·파싱·벡터·가져오기 기록)와 인터뷰 답(앱 계정이 지울 수 없고 질문·세션 CASCADE로만 지워진다)은 대상이 아니다.

지난주 묶음으로 복원하면 그 뒤 지운 자료가 되살아나므로:

```bash
personal-api restore career-weekly.tar.gz --deletions deletions-today.jsonl
# → "re-applied N deletion(s), skipped M"
#     skipped core.sources 1b2c… — still_referenced: core.applications (fk_…)
```

- 기록 파일의 `workspace_id`가 묶음의 공간과 다르면 거부(`deletions_workspace_mismatch`). 표 이름은 위 대상 표만 받는다 (옛 4개 표만 담은 기록 파일도 그대로 읽는다).
- 한 건씩 savepoint 안에서: 원래 삭제 시각으로 기록을 먼저 남기고 그 행을 지운다 (자식 행은 FK CASCADE). 묶음에 없는 대상은 건너뛴다(기록도 남기지 않음). 지울 수 없는 대상은 되돌리고 **이유와 함께** 건너뛴다 — 보고 `skipped_deletions: [{target_table, target_id, reason, detail}]` (API·웹), CLI는 한 줄씩:

  | reason | 뜻 | detail |
  |---|---|---|
  | `still_referenced` | 아직 RESTRICT FK가 가리킨다 | 가리키는 `schema.table (제약)` |
  | `not_permitted` | 앱 계정이 지울 수 없는 표 | DB 메시지 |
  | `rejected` | trigger·검사가 거부 (예: 불변 규칙) | DB 메시지 |
  | `kept_existing` | 합치기에서 원래 있던 행 (지우지 않음) | – |
  | `error` | 그 밖의 DB 오류 | sqlstate |

  `deletions_skipped`(수)는 그대로 둔다.
- 행 수·해시 확인은 삭제 전에 끝낸다 (묶음 그대로 들어갔는지 먼저 보고, 그다음 기록을 적용). 삭제로 더는 참조되지 않는 원본 파일은 되살리지 않는다.

## 3. DB 전체 백업 (서버)

공간 내보내기와 별도로, DB 전체를 `pg_dump`로 정기 백업한다. 실행 중인 DB 데이터 폴더를 그대로 복사하지 않는다 (plan §14).

```bash
cd deploy/docker
docker compose exec -T db pg_dump -U career -d career_brain -Fc > career-db-$(date +%F).dump
docker run --rm -v career-brain_app-data:/data:ro -v "$PWD":/backup busybox \
  tar czf /backup/career-blobs-$(date +%F).tar.gz -C /data blobs
```

- 덤프에는 API 토큰 해시와 모든 공간의 자료가 들어 있다. 서버 밖에 암호화해서 둔다.
- 덤프 복원: 빈 볼륨으로 `docker compose up -d db` → `docker compose exec -T db pg_restore -U career -d career_brain --clean --if-exists < career-db-YYYY-MM-DD.dump` → 원본 tar를 `app-data`에 풀기 → `docker compose up -d`.
- 로컬 개발 DB: `pg_dump "postgresql://career:…@127.0.0.1:5432/career_brain" -Fc > career-db.dump`, 원본은 `data/blobs`.

## 4. 일정 제안

| 주기 | 무엇 | 보관 |
|---|---|---|
| 매일 | `pg_dump` + 원본 볼륨 tar (cron/systemd timer), 서버 밖으로 암호화 복사 | 7일치 + 주 1개 4주 |
| 매주 | 공간 내보내기 (`personal-api export`) | 4~8개 |
| 업데이트 전 | 두 가지 모두 | 다음 업데이트까지 |
| 분기 1회 | 복원 훈련: 빈 DB에 `migrate` → `restore --dry-run` → `restore`, 유지보수 화면의 행 수 확인 | – |

`pg_dump`는 앱(`serve`/`worker`)이 하지 않는다. DB 소유자 계정이 필요하고 결과를 서버 밖으로 보내야 하므로 서버의 cron이나 systemd timer가 맡는다. 미확정 초안 정리는 앱이 스스로 한다 (아래 5절).

cron 예시 (서버, 매일 03:30):

```cron
30 3 * * * cd /srv/career-brain/deploy/docker && docker compose exec -T db pg_dump -U career -d career_brain -Fc > /srv/backup/career-db-$(date +\%F).dump
```

systemd timer 예시 (`/etc/systemd/system/career-backup.{service,timer}`, 7일 지난 덤프 삭제 포함):

```ini
# career-backup.service
[Unit]
Description=career-brain pg_dump + blobs

[Service]
Type=oneshot
WorkingDirectory=/srv/career-brain/deploy/docker
ExecStart=/bin/sh -c 'docker compose exec -T db pg_dump -U career -d career_brain -Fc > /srv/backup/career-db-$(date +%%F).dump'
ExecStart=/bin/sh -c 'docker run --rm -v career-brain_app-data:/data:ro -v /srv/backup:/backup busybox tar czf /backup/career-blobs-$(date +%%F).tar.gz -C /data blobs'
ExecStartPost=/usr/bin/find /srv/backup -name 'career-*' -mtime +7 -delete

# career-backup.timer
[Unit]
Description=daily career-brain backup

[Timer]
OnCalendar=*-*-* 03:30
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now career-backup.timer
systemctl list-timers career-backup.timer      # 다음 실행 시각
journalctl -u career-backup.service            # 마지막 결과
```

서버 밖 암호화 복사(예: `age` 후 `rclone copy`)는 같은 서비스에 `ExecStart` 줄로 더한다.

## 5. 유지보수 정보

`GET /api/core/maintenance` (소유자만):

- 표별 행 수(내보내기에 들어가는 표, 빠지는 표 따로), 참조된 원본 수, 원본 저장소의 파일 수·바이트
- 마지막 내보내기·복원 시각·해시·크기 (`core.export_runs`). Markdown으로 내보내기(`kind = markdown`)는 백업이 아니라서 "마지막 내보내기"에 세지 않는다
- 정리 대기 초안: 가져왔지만 확정하지 않고 7일이 지난 공고 수. `POST /api/core/maintenance/purge-drafts`로 지운다 (버전·가져오기 기록은 FK CASCADE, 지운 공고는 삭제 기록에 남는다)
- 마지막 복원에는 합치기(`kind=merge`)도 들어간다. `restore_available`·`max_restore_bytes`는 웹 복원 가능 여부와 상한
- 웹: `/system` → 백업·유지보수 (행 수, 원본 크기, 마지막 내보내기·합치기, 내려받기·암호화, 삭제 기록 내려받기, 복원(합치기), 초안 정리)
- 초안 정리는 `serve`·`worker`가 `CAREER_MAINTENANCE_INTERVAL_SECONDS`(기본 6시간, `0`이면 끔)마다 모든 공간에 자동으로 한다. 여러 worker여도 advisory lock으로 한 번에 하나만 하고, 다시 해도 결과가 같다. 수동: `personal-api maintenance` ([install.md](install.md#7-작은-서버-조정))
