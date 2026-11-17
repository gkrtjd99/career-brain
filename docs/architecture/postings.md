# 채용 공고 저장 (O06)

사용자가 붙여넣은 URL만 한 건씩 가져온다. 사이트 순회·주기 재확인·구독은 하지 않는다 (plan §8 "보류").

## 흐름

1. `POST /core/job-postings {urls}` — URL 정규화 → 같은 공간의 같은 공고면 기존 항목(`deduplicated: true`). 새 공고면 `job_fetch` job.
2. 가져오기 (`adapters/web_fetch.py`)
   - HTTP 먼저. 본문이 200자 미만이면(스크립트로 그리는 페이지) 선택 설치한 브라우저(Playwright)로 다시 시도, 없으면 `needs_manual_check`.
   - 매 요청·리다이렉트(최대 5회)마다 DNS를 풀어 **모든 주소가 공개 주소**일 때만, 확인한 IP로 접속한다 (Host 헤더·SNI는 원래 이름, TLS 검증 유지). 사설망·루프백·링크로컬·클라우드 메타데이터는 `blocked_address`.
   - 쿠키·로그인 정보 없음, 프록시 환경변수 무시, 6MB·20초 상한. User-Agent에 개인 정보 없음.
   - 압축(gzip·deflate)은 직접 풀며 조각마다 상한을 본다 (압축 폭탄 방지). 다른 인코딩은 받지 않는다.
   - 브라우저 경로: Chromium은 직접 네트워크에 나가지 않는다. 모든 요청을 가로채 위 fetcher로 대신 가져와 채워 준다 (Chromium의 DNS·리다이렉트·rebinding 우회 차단). GET 외 요청·서비스 워커·WebSocket 차단, 하위 요청 150개·페이지 24MB 상한.
3. 본문 고르기 (`adapters/html_extract.py`, 표준 라이브러리만) — 메뉴·머리말·꼬리말·댓글·사이드바를 빼고 글이 많고 링크 비율이 낮은 요소. 반응형 페이지의 PC·모바일 중복 사본과 본문 아래 바뀌는 "관련 공고" 목록을 뺀다 (다시 가져올 때 거짓 변경을 막는다).
4. 목록 페이지(같은 경로 모양 링크 8개 이상, 링크 비율 높음)면 `listing` — 후보 링크만 보여주고 따라가지 않는다.
5. 항목 추출 순서 (`adapters/ats.py`, 순서는 `usecases/postings.py::structured_fields`)
   1. 채용 시스템 페이지 어댑터 — 페이지에 박힌 JSON (아래 표). 근거 `structured: <시스템>:<경로>`
   2. 1에서 못 찾으면 공개 JSON API 어댑터 — URL(또는 페이지가 불러오는 임베드 스크립트)에서 API 주소를 만들어 **한 번** 요청한다 (아래 "추가 요청 규칙").
   3. schema.org `JobPosting` (JSON-LD) — 1·2가 없으면 주 어댑터, 있으면 빈 항목만 채운다 (예: Lever API에는 회사 이름이 없다). 근거 `ld:JobPosting.<경로>`
   4. LLM (`job_extract` 목적, 게이트웨이 경유: 전송 정책·단가·예산·장부). 모델은 값과 **원문 인용**을 낸다. 코드가 인용을 원문에서 찾지 못하면 버린다. 날짜는 월·일 숫자가 인용에 있어야 하고, 짧은 값은 인용 안에 있어야 한다. 버린 항목은 실행 기록 `notes`의 `llm_dropped:`.
   - 제목·회사·마감이 비면 `missing` → 사용자 확인.
   - 채용 시스템 본문(HTML)의 섹션 제목(h 태그·굵은 글씨, 또는 "자격요건"처럼 제목 낱말과 같은 줄) 아래 줄을 목록으로 고른다 (`담당 업무`·`자격 요건`·`우대 사항`·`전형 절차`). 근거 `<경로> § <섹션 제목>`. 분류 못 한 제목("회사 소개", "혜택")이 나오면 섹션이 끝나고, 더 작은 소제목("자격 요건" 아래 "[경험]")은 같은 섹션, `<li>` 목록 뒤의 일반 문단은 섹션 밖이다. 목록 안의 굵은 글씨는 제목이 아니다.
   - 본문 스냅샷: 채용 시스템 어댑터의 본문을 쓴다 (페이지에는 D-day처럼 매일 바뀌는 글이 섞여 거짓 변경이 생긴다). JSON-LD 설명은 일부만 담기도 해서(예: 주요 업무만) 페이지 본문이 1.2배보다 길면 페이지 본문을 쓴다.
   - LLM 출력 보정 (인용 규칙은 그대로)
     - 같은 줄이 여러 목록(주요 업무·자격·우대·절차)에 들어가면 한 곳만 남긴다: 줄에 "우대"·"preferred"·"a plus"가 있으면 우대, 아니면 원문에서 그 줄 바로 앞의 섹션 제목, 그래도 모르면 앞 목록. 한 목록 안의 중복도 뺀다. `notes`의 `llm_deduped:N`.
     - 제목이 페이지 제목의 조각("SW 개발")이고 페이지 제목이 `회사｜[...] 직무` 꼴이면, 그 조각을 담은 부분에서 회사 이름·"채용 공고" 같은 꼬리를 뺀 더 긴 제목("[2026 하반기] SW 개발 직무")으로 넓힌다. 넓힌 값도 본문에서 인용으로 찾아야 쓴다.
6. 버전 — 본문 해시가 같으면 새 버전도 LLM 호출도 없다 (`unchanged`). 확정한 공고가 바뀌면 `changed_fields` → `pending_changes`; 확정 값은 사용자가 다시 확인할 때까지 그대로.
7. `POST .../confirm {version_id?, overrides, clear}` — 사용자가 고친 값은 근거 `user`. 마감은 ISO 날짜/시각 또는 `always`, 접수 시작(`opens_at`)은 ISO 날짜/시각.
8. 실패(`login_required`, `rate_limited`, `source_unavailable`, `too_large`, `unsupported_content`…)는 마감이 아니다. `POST .../manual-text`로 본문을 붙여넣을 수 있다.

## 접수 시작·접수 상태·채용 시기

- **접수 시작 `opens_at`** (항목, `fields`/`extracted` JSONB의 키 — 스키마 변경 없음): 원문이 접수 시작을 적었을 때만 채운다. 나인하이어 `reception.startAt`, 그리팅 `openingsInfo.openDate`(화면의 접수 기간 시작), LLM은 인용 안에 그 월·일 숫자가 있을 때만. 게시일(`posted_at`)·`createdAt`·오늘 날짜로 채우지 않는다. `always`는 받지 않는다. JSON-LD에는 접수 시작이 없다 (`jobStartDate`는 입사일이다).
- **접수 상태 `lifecycle`** (목록·상세 응답, 저장하지 않고 요청마다 계산): 공간 표시 시간대(설정, 없으면 Asia/Seoul — 오늘 화면·복습과 같은 `workspace_zone`) 기준.
  1. 마감이 지났으면 `closed`. 날짜만 있는 마감은 그날 끝(다음 날 0시)까지 접수 중, 시각이 있으면 그 시각까지. 시간대 없는 시각은 공간 시간대의 벽시계.
  2. 접수 시작 전이면 `upcoming` (날짜만 있으면 그날 0시부터 시작).
  3. 마감이 `always`면 `always`.
  4. 마감이 있으면 `open`.
  5. 마감이 없으면 `unknown` — 시작만 지난 공고도 여기다 (접수 중으로 단정하지 않는다). 가져오기 실패도 `unknown`이지 마감이 아니다.
- **채용 시기 `season`**: `"2026-H1"`(1–6월)·`"2026-H2"`(7–12월). 마감 날짜 → 접수 시작 → 저장(없으면 추가)한 날 순으로 본다. 화면은 "2026 상반기"로 보인다.
- `GET /core/job-postings?lifecycle=&status=` — 거름이 없으면 예전처럼 추가한 순 전체. `lifecycle`을 주면 그 상태만, 상태별 순서로: `open` 마감 가까운 순, `upcoming` 시작 가까운 순, `closed` 최근 마감 순, `always`·`unknown` 최근 저장 순. 모르는 값은 422 `invalid_lifecycle`·`invalid_status`.
- **지원 요약 `application`** (목록·상세): `{id, status, questions, answered, submitted_at, stage}`. 지원이 여럿이면 철회하지 않은 가장 최근 것. `stage`는 지원 목록의 지금 전형 단계(아래)와 같다. `applications.read`가 없는 행위자에게는 `null`.
- **한꺼번에 보관** `POST /core/job-postings/archive {ids}`·`POST /core/job-postings/unarchive {ids}` (`jobs.write`, 최대 200개) → `{changed: [id…]}`. 이 공간의 것만 바뀐다 (다른 공간 id는 조용히 0건). 보관을 풀면 저장한 적이 있는 공고는 `saved`, 아니면 `draft`.

## 채용 시스템 어댑터

판별은 호스트가 아니라 데이터 모양으로 한다 (자체 도메인을 쓰는 채용 페이지가 많다). API 어댑터만 URL 모양을 본다.

| 어댑터 (`adapter`) | 읽는 곳 | 항목 (근거 경로 접두어) | 추가 요청 |
|---|---|---|---|
| `greenhouse` (호스팅) | `job-boards.greenhouse.io/<board>/jobs/<id>` 페이지의 Remix `window.__remixContext` → `loaderData.*.jobPost` | 제목·회사·근무지·게시일·마감(`application_deadline`), 본문 섹션 목록 (`greenhouse:jobPost.*`) | 없음 |
| `greenhouse` (임베드·API) | 위 페이지에 데이터가 없을 때, 또는 회사 자체 페이지 URL의 `gh_jid` + 페이지가 불러오는 `boards.greenhouse.io/embed/job_board/js?for=<board>` | 제목·회사·근무지·게시일·마감, 본문 섹션 목록 (`greenhouse_api:job.*`) | `GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>` (Job Board API, 공개 문서) |
| `greeting` | 그리팅 페이지(`*.career.greetinghr.com/<locale>/o/<id>` 또는 자체 도메인)의 `__NEXT_DATA__` → React Query `getOpeningById` | 제목·회사(채용 페이지 이름 `groupInfo.name`)·마감(`dueDate`, 없으면 비움)·게시일·근무지·고용형태·경력, 본문 섹션 목록 (`greeting:opening.*`), 자기소개서 문항·글자 수 (`greeting:opening.questionnairesInfo[i].question·maxLength`, 아래) | 없음 |
| `ninehire` | `__NEXT_DATA__` → `pageProps.recruitment` | 제목·회사·근무지·고용형태·경력·마감(상시 포함)·게시일, 본문 섹션 목록 (`ninehire:*`) | 없음 |
| `lever` | `jobs.lever.co/<site>/<uuid>` (EU `jobs.eu.lever.co`) | 제목(`text`)·근무지(`allLocations`)·고용형태(`commitment`)·게시일(`createdAt`), `lists[]` 제목별 목록 (`lever:posting.*`). 회사는 페이지 JSON-LD | `GET https://api.lever.co/v0/postings/<site>/<uuid>` (Postings API, 공개 문서) |
| `workday` | `<tenant>.wdN.myworkdayjobs.com/[locale/]<site>/job/...` | 제목·회사(`hiringOrganization.name`, 법인명일 수 있음)·근무지(추가 근무지 포함)·고용형태(`timeType`)·게시일(`startDate`), 본문 섹션 목록 (`workday:*`). 페이지 JSON-LD 설명은 줄바꿈이 사라져 있어 CXS의 HTML 본문을 쓴다 | `GET https://<같은 호스트>/wday/cxs/<tenant>/<site>/job/...` |
| `json_ld` | schema.org `JobPosting` | 제목·회사·근무지·고용형태·경력(`experienceRequirements`가 글일 때)·마감·게시일 (`ld:JobPosting.*`) | 없음 |
| `pdf` | `application/pdf` 응답 (아래) | LLM 인용 경로 | 없음 |
| `image` | 이미지 응답 (아래) | LLM 인용 경로 (본문은 모델이 읽은 글) | 게이트웨이 비전 라우트 |
| `text` | 본문 | LLM 인용 경로 | 없음 |

JSON-LD 보정: 날짜만 있는 값(`2026-12-31`)은 날짜로 둔다 (자정 시각으로 바꾸면 마감 당일이 빠진다). 도로명 주소에 지역·시군구가 이미 있으면 겹쳐 쓰지 않는다. 비어 있는 주소 항목(`null`)은 건너뛴다.

### 그리팅 자기소개서 문항 (`questionnairesInfo`)

`getOpeningById` 응답의 `questionnairesInfo[]`(`openingsInfo`와 같은 단계)에 지원서 문항이 온다. 실제 공개 페이지에서 본 모양:
`questionnaireId`, `question`, `type`, `need`(필수), `description`, `maxLength`, `items`/`optionItems`(선택형 보기), `isDirectly`.

- **`type = 100`(서술형)만** 자기소개서 문항(`QUESTIONS`)으로 넣는다: `prompt` = `question`(공백 정리, 1000자 상한), `char_limit` = `maxLength`(1~100000 정수일 때만, 아니면 비움).
- `200`·`400`(선택형: 보기 고르기)은 자기소개서 문항에 넣지 않고 별도 항목 `application_questions`로 둔다 (아래). 그 밖의 종류는 넣지 않는다.
- 근거는 구조화 경로 `greeting:opening.questionnairesInfo[<번호들>].question·maxLength` (원래 배열 번호). 문항은 20개 상한, 모양이 이상한 항목(문자열 아닌 질문, 빈 질문)은 건너뛴다.
- 문항이 없거나 서술형이 하나도 없으면 구조화 항목을 만들지 않는다.

### 그리팅 선택형 문항 (`application_questions`, c0025)

지원서에서 보기를 고르는 문항(`type 200`·`400`)을 구조화 항목 `application_questions`로 둔다: `[{prompt, required, options[], source_type}]`.

- `prompt` = `question`(공백 정리, 1000자 상한), `required` = `need`(참/거짓일 때만, 아니면 null), `options` = `optionItems`(없으면 `items`)의 보기 글자 (문자열이거나 `{item: 글}`인 것만, 공백 정리·200자 상한, 50개 상한). 보기가 하나도 없는 문항은 건너뛴다. 문항은 20개 상한.
- 근거는 구조화 경로 `greeting:opening.questionnairesInfo[<번호들>,<번호들>].question·need·optionItems` (원래 배열 번호).
- **구조화 데이터에서만 온다** (`STRUCTURED_ONLY_FIELDS`): LLM에게 묻지 않고(빠진 항목 목록에도 없다), LLM이 이 이름으로 답해도 버린다. 확정 화면에서 손으로 고치는 항목이 아니라(`POSTING_FIELDS`에 없다) 공고 화면에 읽기 전용 "지원서 선택형 문항"으로 보이고, 확정하면 다른 항목처럼 `fields`·`evidence`에 들어간다.
- 하나 고르기·여러 개 고르기는 구분하지 않는다 (`source_type`에 원래 번호만. 공개 자료에서 두 번호의 뜻을 확인하지 못했다).
  - **보류** (2026-11-17 다시 찾음): 그리팅의 공개 개발자 문서·도움말에서 `type` 번호 목록을 찾지 못했다 (일반 웹 검색, 로그인 없이). 다른 채용 솔루션 도움말의 "단일 선택·다중 선택" 구분은 그리팅 번호와 대응한다는 근거가 아니다. 공개 공고 몇 건의 보기 모양만으로 뜻을 짐작하지 않는다 (추측한 뜻을 화면에 보이면 지원자가 잘못 고를 수 있다). 관리자 화면 로그인이나 지원서 제출 흐름을 따라가 확인하는 것은 이 레포 규칙 밖이다. 그리팅이 공개 문서(또는 공개 API 응답에 이름 붙은 칸, 예: `multiple: true`)로 뜻을 밝히면 그때 `selection: single|multiple`을 더한다.
- fixture는 합성이다 (`test_postings_ops.py`).

### 추가 요청 규칙

- API 주소는 어댑터 코드가 정한 모양으로만 만든다 (보드·사이트 이름은 `[A-Za-z0-9_.-]`, 공고 ID는 숫자/UUID). 페이지 글이 호스트를 정하지 못한다.
- `domain/postings.py::ats_request_allowed`: https, 기본 포트, 계정 정보 없음, 그리고 **공고 페이지와 같은 호스트**(Workday)이거나 **`ATS_API_HOSTS`**(`api.lever.co`, `api.eu.lever.co`, `boards-api.greenhouse.io`)일 때만.
- 요청은 같은 `HttpFetcher`를 지난다: 매 요청·리다이렉트마다 공개 주소 검사와 IP 고정, 쿠키 없음, 같은 User-Agent, 6MB·20초 상한. `Accept: application/json`만 다르다.
- 한 가져오기에 API 요청은 최대 1회. 실패하면 `notes`에 `ats_api_failed:<어댑터>:<코드>`를 남기고 페이지의 JSON-LD·본문·LLM으로 계속한다.

## 봇 차단 (`bot_blocked`)

응답이 **봇 확인 화면**이면 `rate_limited`가 아니라 `bot_blocked`로 끝낸다 (`adapters/web_fetch.py::detect_bot_block`). 화면: "사이트가 자동 요청을 막음 — 직접 붙여넣기".

| 판단 근거 | 예 |
|---|---|
| 응답 헤더 (상태 코드 무관) | `cf-mitigated: challenge` (Cloudflare), `x-amzn-waf-action: captcha·challenge` (AWS WAF). 403이면 `x-datadome`도 |
| 403·429·503 본문 표시 (앞 6만 자, 대소문자 무시) | `/cdn-cgi/challenge-platform/`, `_cf_chl_opt`, `challenges.cloudflare.com`, `<title>just a moment...</title>`, Imperva·DataDome(`captcha-delivery.com`)·PerimeterX(`px-captcha`)·AWS WAF 확인 스크립트 |

- `notes`에 `bot_blocked_by:<cloudflare|imperva|datadome|perimeterx|aws_waf>`. 표시가 없는 429(`Retry-After`만 있는 것 포함)는 그대로 `rate_limited`, 403은 `login_required`/`source_unavailable`.
- **우회하지 않는다**: 같은 요청 1회, 쿠키 없음, 같은 User-Agent, 확인 스크립트를 풀거나 다른 경로(브라우저 렌더링 포함)로 바꿔 다시 시도하지 않는다. 가져오기 실패는 자동으로 다시 시도하지 않는다.
- 브라우저 렌더링(`CAREER_BROWSER_FETCH`)으로 받은 응답도 같은 규칙으로 판단한다 (응답 헤더 중 판단에 쓰는 몇 개만 넘긴다).
- 사용자는 공고 글을 직접 붙여넣거나, 화면 캡처·PDF를 올린다.

### 모르는 확인 화면 (`maybe_bot_blocked`, c0025)

위 표의 업체 표시는 없지만 확인 화면처럼 보이면 `maybe_bot_blocked`("자동 요청이 막혔을 수 있음 — 직접 붙여넣기")로 끝낸다. 확실하지 않으므로 `bot_blocked`가 아니고, 요청 한도(`rate_limited`)도 아니다 (`adapters/web_fetch.py::challenge_signs`).

| 흔적 (`notes`의 `bot_block_signs:<…>`) | 내용 |
|---|---|
| `captcha` | 캡차 위젯(`g-recaptcha`·`h-captcha`·`cf-turnstile` 클래스 등). 403·429·503이면 캡차 스크립트 주소만으로도 |
| `phrase` | 사람 확인 문구 ("checking your browser", "verify you are human" 등) |
| `js_challenge` | 쿠키를 심고 페이지를 다시 부르는 스크립트, 이름에 challenge가 든 스크립트·폼 |
| `tiny_body_script` | 보이는 글이 200자 미만인데 스크립트만 있다 (403·429·503일 때만) |

- 403·429·503: 흔적 하나면 `maybe_bot_blocked`. 순서는 알려진 업체(`bot_blocked`) → 로그인(401·407, 로그인 글이 있는 403) → 모르는 확인 화면 → 429 `rate_limited` → 그 밖의 오류.
- 200(HTML만): 보이는 글이 200자 미만이고 `captcha`·`phrase`·`js_challenge` 중 하나가 있을 때만 (캡차 스크립트를 불러오는 보통 페이지, 스크립트로 그리는 SPA와 구분). 이런 페이지는 브라우저 렌더링으로 다시 열지 않는다 (렌더링은 확인 화면을 푸는 다른 경로라서).
- 위와 같이 **우회하지 않는다**.

## PDF·이미지 공고

`HttpFetcher`는 `application/pdf`, `image/png|jpeg|webp|gif`를 바이트로 넘긴다 (`FetchedPage.body`). 선언한 형식과 파일 머리(매직 바이트)가 맞아야 하고, `application/octet-stream`이면 파일 머리로 판단한다. 크기 상한은 HTML과 같다 (6MB). 버전의 `method`는 그대로 `http`, `adapter`가 `pdf`/`image`다 (migration 없음).

- **PDF**: 원본 자료와 같은 로컬 파서(`PdfTextParser`, pypdf, 300쪽 상한)로 글자를 꺼내 본문 경로(LLM 인용 확인)로 넘긴다. 외부 전송은 LLM 추출 하나뿐이다. `notes`에 `pdf_pages:<글자 있는 쪽>/<전체 쪽>`. 암호·손상 파일은 `pdf_unreadable:<코드>`. 글자가 없으면(스캔본) 아래 스캔 PDF 경로.
- **스캔 PDF** (`pdf_no_text`): 쪽을 이미지로 바꿔 이미지 공고와 같은 비전 경로(`job_extract_image`, 자료 등급 `public`)로 보낸다.
  - 렌더러는 선택 설치 `career-core[pdf-render]` (pypdfium2, 휠 ~4MB·설치 ~8MB, 의존성 없음, BSD-3/Apache-2.0). 이미지 라이브러리 없이 회색조 비트맵을 표준 라이브러리(zlib)로 PNG로 쓴다 (`adapters/pdf_render.py`).
  - 상한: **4쪽**(아래 쪽 고르기), 150dpi, 쪽마다 긴 변 1600px·전체 1600×1600px 이하로 줄여 그린다. PNG 하나가 5MB를 넘으면 거기서 멈춘다. 폼·주석은 그리지 않는다. PDFium은 스레드 안전하지 않아 프로세스 안에서 한 번에 하나만 그린다 (`asyncio.to_thread` + 잠금).
  - 라우트부터 본다: 없으면 그리지도 않고 `needs_manual_check` + `pdf_no_text`, `vision_route_missing`. 렌더러가 없으면 `pdf_no_text`, `pdf_render_missing` (화면: "스캔 PDF를 읽으려면 pdf-render 설치"), 그리기 실패는 `pdf_render_failed:<코드>`.
  - 성공하면 `notes`에 `pdf_rendered:<보낸 쪽>/<전체 쪽>`, `pdf_pages_read:<쪽들>`, (있으면) `pdf_blank_pages:<수>`, `ocr_pdf_by_model`. 버전 `adapter`는 `pdf`, 첫 쪽 이미지를 스크린샷 칸에 둔다. 같은 파일(`pdf:<sha256>`)을 다시 가져오면 모델을 부르지 않는다.

### 스캔 PDF 쪽 고르기·비용 확인 (c0025)

**쪽 고르기** (`adapters/pdf_render.py::pick_pages`): 앞 60쪽(+ 마지막 쪽)을 긴 변 96px 회색조로 작게 그려 어두운 점(밝기 200 미만)의 비율을 잰다. 0.4% 미만이면 빈 쪽으로 보고 뺀다. 남은 쪽이 4쪽 이하면 모두, 넘으면 **앞 3쪽 + 마지막 쪽** (마감·지원 방법·문의가 끝에 있는 공고가 많다). 모두 빈 쪽이면 앞 4쪽 (모델이 글자 없음으로 답한다). 고른 쪽만 150dpi로 그린다.

**예상 비용** (`usecases/postings.py::vision_estimate`): 가져오기 한 번이 부르는 두 호출을 따로 어림해 더한다. 금액은 게이트웨이의 `estimate_cost`(라우트의 단가·구간 그대로, 호출하지 않는다)로, 호출마다 예상(`estimated_usd`)과 입력·출력이 상한까지 나올 때(`max_usd`).

| 호출 | 입력 토큰 (예상 / 최대) | 출력 토큰 (예상 / 최대) |
|---|---|---|
| 이미지 읽기 `job_extract_image` | 이미지마다 Anthropic 식(`가로×세로/750`)과 OpenAI 타일 식(512px 타일 170 + 85) 중 큰 값 + 안내문 글자 수 (둘이 같다) | 쪽마다 1200 / 6000 (`per_page`) |
| 항목 추출 `job_extract` | 안내문 글자 수 + 옮긴 글. 옮긴 글 = 이미지 읽기의 출력이라 예상은 그 예상 출력(쪽마다 1200), 최대는 그 상한 6000 (본문 상한 3만 자 이하) | 이 공간 장부(`model_calls`)의 최근 성공한 `job_extract` 호출 20개 평균(3개 이상일 때, `history_<n>`), 모자라면 상수 1500 (`default`, 측정값이 아니라 항목 JSON + 인용 몇십 개 어림) / 6000 (호출 상한) |

- 글자 수를 토큰으로 세는 안내문은 넘치게 잡는 쪽이다. 장부에서는 출력 토큰 수만 읽는다 (본문 없음).
- 합계(`input_tokens`·`output_tokens`)는 두 호출의 합, 금액(`estimated_usd`·`max_usd`)은 **종량제 호출만** 더한다. 구독·로컬 호출은 정가 환산("API였다면", `price_as`)을 호출별 줄에만 보이고 합계·상한 비교에서 뺀다. 종량제 호출 하나라도 단가를 모르면 합계는 모름 (→ 묻는다).
- 추출 라우트가 없으면(기본 라우트도 없으면) 추출 줄이 빠진다.
- 호출별 예상은 실행 메모 `estimate_part:<목적>:<과금>:<입력>:<최대 입력>:<출력>:<최대 출력>:<예상 USD>:<최대 USD>:<근거>:<엔드포인트>:<모델>`로 남고 (확인 요청 행에는 합계만, migration 없음), API는 `confirmation.parts[]`로 돌려준다. 웹 확인 카드에 호출별 표가 보인다. 이 변경 전의 요청은 `parts`가 비어 합계만 보인다.

**확인** (`needs_confirmation`):

1. 이미지 읽기 엔드포인트가 종량제(`billing = metered`)이고 종량제 합계 `max_usd`(이미지 읽기 + 종량제면 항목 추출)가 `CAREER_VISION_CONFIRM_USD`(기본 0.10 USD)를 넘거나 단가를 모르면, 모델을 부르지 않고 실행을 `needs_confirmation`으로 멈춘다. `core.job_fetch_confirmations`에 쪽·이미지 수·합계 토큰·합계 금액·이미지 읽기 모델·파일 해시를 남긴다 (원본 바이트 없음). 같은 공고의 앞선 대기 요청은 `superseded`. `notes`: `pdf_no_text`, `pdf_pages_read:…`, `pdf_blank_pages:N`, `vision_estimate_usd:<이미지 읽기 최대|unknown>`, `estimate_part:…`(호출마다).
2. 이미지 읽기가 구독(`subscription`)·로컬 엔드포인트면 묻지 않는다 (호출마다 돈이 나가지 않는다). 이때 종량제 추출 호출은 다른 공고(HTML·글자 있는 PDF)의 추출과 같이 게이트웨이 예산·장부 규칙만 따른다 — 스캔 PDF에서만 추출 비용을 묻지 않게. `CAREER_VISION_CONFIRM_USD=off`면 묻지 않는다.
3. 웹 공고 화면에 예상(읽을 쪽, 토큰, 금액, 기준)이 보이고 **확인하고 읽기** → `POST /api/core/job-postings/<id>/fetch-runs/<run>/confirm` (202, 같은 주소로 새 가져오기를 넣는다. 이미 대기 중인 가져오기가 있으면 그것) 또는 **읽지 않기** → `.../decline` (실행은 `failed`/`needs_manual_check` + `vision_declined`, 직접 붙여넣기). 사람(웹·HTTP 토큰)만, `jobs.write`. MCP 등 에이전트는 403 `human_confirmation_required`, 이미 결정한 요청은 409 `confirmation_not_pending`.
4. 새 가져오기는 확인한 요청 중 **같은 파일 해시·같은 쪽·같은 모델이고 금액이 확인한 최대 이하**인 것을 한 번만 쓴다 (`used`, `notes`에 `vision_confirmed`). 파일이 바뀌었거나 더 비싸면 다시 묻는다.
5. 오늘 화면의 "가져오기 확인" 목록에 "비용 확인"으로 보인다.

확인은 두 호출의 합계 금액에 대한 것이다. 실제 호출은 각각 게이트웨이의 예산·장부 규칙을 그대로 따른다 (예상이 실제 비용을 막지 않는다).
- **이미지(포스터)**: `models.toml`에 `job_extract_image` 라우트를 **명시**했을 때만 게이트웨이로 보낸다 (default 라우트로 넘어가지 않는다: 비전을 못 하는 모델로 보내지 않게). 자료 등급 `public`(공개 공고), 게이트웨이의 egress·단가·예산·장부 규칙 그대로. 제공자 SDK를 직접 부르지 않는다 (`ModelRequest.images`).
  1. 모델이 이미지의 글자를 그대로 옮긴다 (번역·요약 금지, 이미지 속 지시는 무시).
  2. 옮긴 글이 버전 본문이 되고, 항목은 같은 LLM 인용 확인을 거친다. `notes`에 `ocr_by_model` (화면: "AI가 읽은 글, 원본과 대조").
  3. 원본 이미지는 스크린샷 칸(`screenshot_key`)에 보관한다. 같은 이미지(바이트 해시 `image:<sha256>`)를 다시 가져오면 모델을 부르지 않는다.
  - 라우트가 없으면 `needs_manual_check` + `vision_route_missing`, 5MB 초과 `image_too_large`, 글자 없음 `image_no_text`, 거부 `vision_denied:<코드>`.

## 실제 확인 (2026-11-16)

| 페이지 | 결과 |
|---|---|
| ninehire 채용 페이지 | `ninehire` 어댑터: 제목·회사·고용형태·경력·마감·근무지 구조화 근거, 요건·우대·절차 인용 |
| 공고 모음 사이트 목록 | `listing`, 후보 43건 (따라가지 않음) |
| 공고 모음 사이트 글 | `text`+LLM: 제목·회사·고용형태·요건·절차 인용 근거, 마감은 원문에 없어 `missing` |

세 페이지 모두 다시 가져오기 3회에 새 버전 0건.

## 실제 확인 (2026-11-17, 채용 시스템 어댑터)

실제 공개 페이지는 구조를 익히는 데만 썼고 커밋하지 않았다 (테스트 fixture는 같은 모양의 합성 데이터).

| 페이지 | 결과 |
|---|---|
| Greenhouse 호스팅 페이지 (`job-boards`, 옛 `boards` 주소는 301로 넘어감) | `greenhouse`: 제목·회사·근무지·게시일, 섹션 목록 3종 |
| 회사 자체 채용 페이지 + Greenhouse 임베드 (`gh_jid`) | `greenhouse`(API): 제목·회사·근무지·게시일, 섹션 목록 2종 |
| 그리팅 채용 페이지 (마감 있음·없음 각 1건) | `greeting`: 7개 항목 + 섹션 목록 4종. 마감 없는 공고는 마감 비움 |
| Lever 채용 페이지 | `lever`: API 목록 2종 + JSON-LD 회사 |
| 원티드 공고 | `json_ld`: 제목·회사·근무지·고용형태·경력·게시일. 설명이 주요 업무뿐이라 본문은 페이지에서 |
| Workday (3개 테넌트) | **이 환경에서 미확인**: Cloudflare가 httpx 요청을 429로 막는다 (curl은 200). 봇 차단은 우회하지 않는다 → `rate_limited`. CXS 응답 구조는 별도로 받은 응답으로 확인했다 |

넣지 않은 것: 점핏·사람인 (구조화 데이터가 없거나 RSC 조각뿐이고, 포털 전용 수집기는 오픈소스에 넣지 않는다 — plan §8). 원티드도 전용 어댑터 없이 JSON-LD로만 읽는다.

## 실제 확인 (2026-11-17, 문항·봇 차단)

실제 공개 페이지는 구조를 익히는 데만 썼고 커밋하지 않았다 (일반 User-Agent, 쿠키 없음, 페이지당 1회).

| 페이지 | 결과 |
|---|---|
| 그리팅 채용 페이지 3곳 | 2곳에서 서술형(`type 100`) 문항 + `maxLength 1000`, 1곳은 선택형(`200`·`400`)만. 합성 fixture가 같은 모양 |
| Workday 테넌트 (Cloudflare 앞단) | 429 + `cf-mitigated: challenge`, 본문에 `/cdn-cgi/challenge-platform/` → `bot_blocked`. 다른 테넌트는 200 |

## 범위 밖 / 백로그

- ~~스캔 PDF: 쪽을 이미지로 바꿔 비전 경로로 넘기기~~ → 해결 (위 "스캔 PDF", `pdf-render` extra)
- ~~Workday·Cloudflare 앞단 사이트의 429를 봇 차단으로 구분~~ → 해결 (`bot_blocked`)
- ~~그리팅 `questionnairesInfo` 구조화~~ → 해결 (서술형만)
- ~~스캔 PDF 비전 비용: 쪽 수만큼 이미지가 간다 (최대 4장). 쪽이 많은 스캔 공고는 앞 4쪽만 읽는다 → 필요하면 쪽 선택.~~ → 해결 (c0025, 위 "쪽 고르기·비용 확인")
- ~~그리팅 선택형 문항(보기)은 넣지 않는다.~~ → 해결 (c0025, `application_questions`. 하나/여러 개 고르기 구분은 후속)
- ~~모르는 업체의 봇 확인 화면~~ → 해결 (c0025, `maybe_bot_blocked`)
- 비용 확인은 이미지 읽기 호출만 추정한다. 뒤의 `job_extract` 호출까지 합친 추정은 후속.
- 초안 정리(`purge_unconfirmed`)를 주기적으로 돌리는 유지보수 루프
