# 백업·내보내기·복원 (O11)

두 가지 방법이 있다. 목적이 다르므로 서버 설치라면 둘 다 둔다.

| | 공간 내보내기 (`personal-api export`) | DB 백업 (`pg_dump`) + 원본 볼륨 |
|---|---|---|
| 단위 | 공간 하나 | DB 전체 (모든 공간·토큰 해시·migration 기록) |
| 누가 | 공간 소유자 (API 토큰, `workspace-admin`) | 서버 관리자 (DB 소유자 계정) |
| 들어가는 것 | 공간 자료 + 참조된 원본 파일, 행 수·해시 manifest | 전부 (검색 벡터·작업 큐 포함) |
| 복원 대상 | 같은 버전의 **빈** 공간: ID 그대로(같은 공간 ID가 없어야 함) 또는 `--new-ids`로 새 공간. 또는 `--merge`·웹 복원으로 **자료가 있는 공간에 합치기** | 같은 DB를 통째로 되돌림 |
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
- **대기 작업**: 내보낼 때 큐에서 대기·실행 중이던 파싱(`parse`)·추출(`extract`)·공고 가져오기(`job_fetch`)를 manifest `pending_work`에 `{kind, target_table, target_id, status}`로 적는다 (큐 표 자체는 넣지 않는다).
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
- 형식 버전과 별개로 **core migration head가 다른 묶음**(표·칸이 다른 묶음)은 아직 바꾸지 않는다 (plan §18).

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
| 서비스 계정·결제 (`platform`) | **빠짐** | career-service 소유 |

묶음에는 개인 자료가 그대로 들어 있다. `--recipient`로 암호화하거나 서버 밖에 암호화해서 둔다. 해시는 무결성 검사(손상·부분 수정)이고 서명이 아니다.

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
2. 버전 확인: `format_version`, core migration head가 이 설치와 같아야 한다. 표 목록과 칸 이름·타입도 대상 DB와 비교한다. 다르면 거부(`version_mismatch` / `schema_mismatch`) — 먼저 같은 버전으로 맞춘다.
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
- 알려진 차이: 승인의 `target_version`·`payload_hash`처럼 **ID를 넣어 계산한 해시**는 다시 계산하면 맞지 않는다. 그래서 가져온 공간의 열린 승인은 조회·결정 때 `target_changed`로 닫힌다 (다시 요청). 제출본·revision의 본문 해시는 ID를 담지 않아 그대로 맞다. 멱등 키 안의 ID도 같이 바뀐다.

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
  4. 같은 내용 (`content`): 기본 키를 뺀 모든 칸의 정규 JSON이 같다. 키가 없는 표(경험·주장 등)의 마지막 기준이다.

  키 칸은 부모 표의 결정이 끝난 뒤의 값(바뀐 ID)으로 비교한다. 묶음 안에서 서로 겹치는 행도 잡는다.
- **겹침 처리 (`--on-duplicate`)**: `skip`(기본)은 기존 행을 그대로 두고 건너뛴다. `fail`은 하나라도 겹치면 아무것도 하지 않고 `duplicates_found: <표> <수>, …`로 멈춘다.
- **넣지 않는 표**: 공간(대상 공간 그대로), 구성원(`memberships`), 사람(`actors`: 묶음의 사람은 대상 공간의 같은 ID 구성원이면 그대로, 아니면 합치는 사람으로 바꾼다), 백업 기록(`export_runs`: 합치기는 이 공간에 `kind=merge`로 남는다), 묶음 공간의 삭제 기록. 보고의 `not_merged`에 이유가 있다.
- **한 트랜잭션** (REPEATABLE READ): 겹침 찾기 → 넣기 → 확인 → 삭제 기록 → 대기 작업 → 원본 저장 → commit. 어디서든 실패하면 rollback. 넣다가 제약에 걸리면 `merge_conflict: <제약>`. FK는 모두 바뀐 ID로 맞춰지고, DEFERRABLE FK는 commit 전에 검사된다.
- **확인**: 넣기 전후로 표마다 순서 무관 다중집합 해시(행 정규 JSON sha256의 합)를 잰다. `넣은 뒤 − 넣기 전 = 넣은 행의 해시`이고 행 수도 맞아야 한다 — 기존 행이 하나도 바뀌지 않았다는 확인이다. 어긋나면 `verify_failed`.
- **삭제 기록** (`--deletions`): 이번에 **넣은 행**에만 다시 적용한다. 원래 있던 행(겹침)의 삭제 기록은 `kept_existing`으로 건너뛴다 (합치기는 기존 자료를 지우지 않는다).
- **대기 작업**: 넣은 행의 것만 다시 넣는다. 원본 파일은 아직 참조되는 것 중 이 공간에 없던 것만 저장한다.
- 승인 요청(`approvals`)은 넣는 동안 대상 변경으로 닫히지 않는다 (`career.restoring`). 넣은 열린 승인은 조회·결정 때 해시 확인이 막는다 (ID가 바뀐 대상은 `target_changed`).
- 버전: 묶음 형식은 위 단계로 바꿔 읽고, core migration head는 같아야 한다 (복원과 같음).

### 웹 복원 (`/system` → 백업·유지보수 → 복원)

소유자만 (`workspace.restore`). 묶음(`.tar.gz`, 암호화했으면 `.age`)과 선택으로 삭제 기록 `.jsonl`을 올리고, **확인(dry-run) → 확인 대화상자 → 합치기** 순서로 한다. 확인 결과(더할 행·건너뛸 행·원본 수·표별 겹침 기준·건너뛸 삭제 기록과 이유)를 본 뒤에만 합칠 수 있다.

| 요청 | 하는 일 |
|---|---|
| `POST /api/core/maintenance/restore` (본문 = 파일 그대로, `Content-Type: application/octet-stream`·`application/gzip`, 선택 `X-Filename`) | 임시 폴더에 흘려 받고 sha256을 잰다 → 작업(201) |
| `POST …/restore/{id}/deletions` (본문 = jsonl) | 삭제 기록을 붙인다 (머리말·공간 검사, 잘못되면 400) |
| `POST …/restore/{id}/verify` `{on_duplicate, identity?}` | 배경에서 dry-run (202) |
| `GET …/restore/{id}` | 상태 `uploaded`→`verifying`→`verified`→`applying`→`done`/`failed`, 단계(`read`·`decrypt`·`match`·`insert`·`verify`·`deletions`·`blobs`·`commit`)와 진행 수, 보고 |
| `POST …/restore/{id}/apply` `{on_duplicate, identity?, archive_sha256}` | 확인한 파일·정책과 같을 때만 합치기 (아니면 409 `not_verified`·`archive_changed`·`policy_changed`) |
| `DELETE …/restore/{id}` | 작업과 임시 파일 지우기 |

- **본문 상한**: 이 업로드 경로(`POST /api/core/maintenance/restore`) 하나만 `CAREER_MAX_RESTORE_MB`(기본 2048)을 쓴다. 다른 경로는 `CAREER_MAX_UPLOAD_MB` 그대로. 넘으면 413 `too_large` (받던 파일은 지운다). 리버스 프록시 상한(`client_max_body_size` 등)도 같이 올린다.
- **임시 폴더만**: 올린 파일은 시스템 임시 디렉터리의 `career-restore-upload-*` 폴더에만 둔다 (권한 0700). 합치기가 끝나거나(`done`), 지우거나, 1시간 쓰지 않거나, 서버가 끝나면 폴더째 지운다. 서버가 시작할 때 남은 옛 폴더도 지운다. 원본 저장소에는 합치기가 commit할 때 필요한 원본만 들어간다.
- **age 신원**: 확인·합치기 요청 본문으로만 보낸다 (형식이 틀리면 바로 400 `invalid_identity`). 서버는 그 작업 동안만 0600 임시 파일로 두고 지운다. 저장하지 않는다.
- **작업 상태는 프로세스 메모리에 있다** (공간마다 하나, 서버 전체 8개까지). 진행 중이면 같은 공간의 새 작업은 409 `restore_busy`. 여러 프로세스로 띄우면 같은 프로세스로 요청이 가야 한다 (plan §18).
- 화면은 1초마다 상태를 읽어 단계와 진행 막대를 보여 준다. 끝나면 유지보수 정보에 "마지막 합치기"가 보인다.
- 원본 저장소가 없는 설치(`restore_available=false`)에서는 화면에 CLI 안내만 나온다.

### 대기 작업 다시 넣기

manifest `pending_work`의 각 항목을 대상 행(`parse_runs`·`extraction_runs`·`job_fetch_runs`)이 복원된 공간에 있을 때만 `queued` job으로 넣는다 (같은 종류·대상이면 한 번, 새 ID면 바뀐 대상 ID로). 결과는 복원 보고 `requeued`와 CLI 출력 `re-queued N parse job(s)`. worker(`serve` 또는 `worker`)가 처리한다.

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
- 마지막 내보내기·복원 시각·해시·크기 (`core.export_runs`)
- 정리 대기 초안: 가져왔지만 확정하지 않고 7일이 지난 공고 수. `POST /api/core/maintenance/purge-drafts`로 지운다 (버전·가져오기 기록은 FK CASCADE, 지운 공고는 삭제 기록에 남는다)
- 마지막 복원에는 합치기(`kind=merge`)도 들어간다. `restore_available`·`max_restore_bytes`는 웹 복원 가능 여부와 상한
- 웹: `/system` → 백업·유지보수 (행 수, 원본 크기, 마지막 내보내기·합치기, 내려받기·암호화, 삭제 기록 내려받기, 복원(합치기), 초안 정리)
- 초안 정리는 `serve`·`worker`가 `CAREER_MAINTENANCE_INTERVAL_SECONDS`(기본 6시간, `0`이면 끔)마다 모든 공간에 자동으로 한다. 여러 worker여도 advisory lock으로 한 번에 하나만 하고, 다시 해도 결과가 같다. 수동: `personal-api maintenance` ([install.md](install.md#7-작은-서버-조정))
