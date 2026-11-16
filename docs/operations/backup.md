# 백업·내보내기·복원 (O11)

두 가지 방법이 있다. 목적이 다르므로 서버 설치라면 둘 다 둔다.

| | 공간 내보내기 (`personal-api export`) | DB 백업 (`pg_dump`) + 원본 볼륨 |
|---|---|---|
| 단위 | 공간 하나 | DB 전체 (모든 공간·토큰 해시·migration 기록) |
| 누가 | 공간 소유자 (API 토큰, `workspace-admin`) | 서버 관리자 (DB 소유자 계정) |
| 들어가는 것 | 공간 자료 + 참조된 원본 파일, 행 수·해시 manifest | 전부 (검색 벡터·작업 큐 포함) |
| 복원 대상 | 같은 버전의 **빈** 공간: ID 그대로(같은 공간 ID가 없어야 함) 또는 `--new-ids`로 새 공간 | 같은 DB를 통째로 되돌림 |
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

### 묶음 형식 (tar.gz, `format_version` 1)

```text
manifest.json                      형식·버전, core migration head, 만든 시각, 공간 id·이름,
                                   표마다 {행 수, sha256, 칸 이름·타입}, 원본 {key, 크기, 참조한 칸}, 뺀 표와 이유
tables/core.<table>.jsonl          한 줄 = 한 행 (PostgreSQL to_jsonb, UTC), 기본 키 순서
blobs/<sha256>                     행이 참조하는 원본 파일 (업로드 원본, 공고 스크린샷)
```

manifest의 `pending_work`(대기 작업)는 c0014에서 더한 선택 키다. 없는 묶음도 그대로 읽는다 (`format_version`은 1 그대로).

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
| 삭제 기록 (`deletions`) | 들어감 | 원본·공고·지원·학습 항목을 지운 기록 (c0014 trigger). ID와 시각만 |
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

같은 묶음을 같은 대상에 ID 그대로 두 번 복원할 수 없다. 기존 공간에 합치기는 아직 없다 (plan §18).

### 새 ID로 가져오기 (`--new-ids`)

```bash
personal-api restore career.tar.gz --new-ids [--dry-run]
# → "imported with new ids (source workspace …)" + 새 공간 소유자의 새 토큰
```

- 묶음의 모든 기본 키(uuid) 값마다 새 UUID를 만든다 (공간·actor 포함). 그리고 모든 줄에서 그 값이 나오는 곳을 같은 규칙으로 바꾼다: FK 칸, FK 없는 참조(승인 대상·실행 입력·감사 actor), JSON 안의 ID와 ID를 키로 쓰는 맵(`{"revisions": {document_id: n}}`)까지. 묶음에 없는 UUID(바깥 ID)는 그대로 둔다. 유일 제약은 모두 바뀐 ID를 포함하므로 같은 설치에 사본을 만들어도 충돌하지 않는다.
- 확인: 넣은 행을 다시 읽어 **새 ID를 옛 ID로 되돌린 뒤** 행마다 정규 JSON(키 순서 무관)의 sha256을 더한 값(순서 무관 다중집합 해시)과 행 수를 묶음과 비교한다. 다르면 rollback.
- 결과 공간은 새 공간이다. 원래 공간과 같은 설치에 있어도 서로 보이지 않는다 (RLS).
- 알려진 차이: 승인의 `target_version`·`payload_hash`처럼 **ID를 넣어 계산한 해시**는 다시 계산하면 맞지 않는다. 그래서 가져온 공간의 열린 승인은 조회·결정 때 `target_changed`로 닫힌다 (다시 요청). 제출본·revision의 본문 해시는 ID를 담지 않아 그대로 맞다. 멱등 키 안의 ID도 같이 바뀐다.

### 대기 작업 다시 넣기

manifest `pending_work`의 각 항목을 대상 행(`parse_runs`·`extraction_runs`·`job_fetch_runs`)이 복원된 공간에 있을 때만 `queued` job으로 넣는다 (같은 종류·대상이면 한 번, 새 ID면 바뀐 대상 ID로). 결과는 복원 보고 `requeued`와 CLI 출력 `re-queued N parse job(s)`. worker(`serve` 또는 `worker`)가 처리한다.

### 삭제 기록 다시 적용 (`--deletions`)

c0014부터 원본·공고·지원·학습 항목(`core.sources`·`job_postings`·`applications`·`study_items`)을 지우면 trigger가 `core.deletions`에 (표, ID, 시각)을 남긴다 (추가만, 그 공간 문맥의 삭제만 — 공간 통째 삭제는 남기지 않는다). 지난주 묶음으로 복원하면 그 뒤 지운 자료가 되살아나므로:

```bash
personal-api restore career-weekly.tar.gz --deletions deletions-today.jsonl
# → "re-applied N deletion(s), skipped M"
```

- 기록 파일의 `workspace_id`가 묶음의 공간과 다르면 거부(`deletions_workspace_mismatch`). 표 이름은 위 4개만 받는다.
- 한 건씩 savepoint 안에서: 원래 삭제 시각으로 기록을 먼저 남기고 그 행을 지운다 (자식 행은 FK CASCADE). 묶음에 없는 대상은 건너뛴다(기록도 남기지 않음). 지울 수 없는 대상(아직 RESTRICT FK가 가리킴)은 되돌리고 `skipped`로 센다.
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

cron 예시 (서버, 매일 03:30):

```cron
30 3 * * * cd /srv/career-brain/deploy/docker && docker compose exec -T db pg_dump -U career -d career_brain -Fc > /srv/backup/career-db-$(date +\%F).dump
```

## 5. 유지보수 정보

`GET /api/core/maintenance` (소유자만):

- 표별 행 수(내보내기에 들어가는 표, 빠지는 표 따로), 참조된 원본 수, 원본 저장소의 파일 수·바이트
- 마지막 내보내기·복원 시각·해시·크기 (`core.export_runs`)
- 정리 대기 초안: 가져왔지만 확정하지 않고 7일이 지난 공고 수. `POST /api/core/maintenance/purge-drafts`로 지운다 (버전·가져오기 기록은 FK CASCADE, 지운 공고는 삭제 기록에 남는다)
- 웹: `/system` → 백업·유지보수 (행 수, 원본 크기, 마지막 내보내기, 내려받기·암호화, 삭제 기록 내려받기, 초안 정리)
