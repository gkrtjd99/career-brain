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
7. `POST .../confirm {version_id?, overrides, clear}` — 사용자가 고친 값은 근거 `user`. 마감은 ISO 날짜/시각 또는 `always`.
8. 실패(`login_required`, `rate_limited`, `source_unavailable`, `too_large`, `unsupported_content`…)는 마감이 아니다. `POST .../manual-text`로 본문을 붙여넣을 수 있다.

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
- `200`·`400`(선택형: 보기 고르기) 등은 넣지 않는다. 자기소개서가 아니고 보기를 문서로 쓸 일이 없다.
- 근거는 구조화 경로 `greeting:opening.questionnairesInfo[<번호들>].question·maxLength` (원래 배열 번호). 문항은 20개 상한, 모양이 이상한 항목(문자열 아닌 질문, 빈 질문)은 건너뛴다.
- 문항이 없거나 서술형이 하나도 없으면 구조화 항목을 만들지 않는다.

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

## PDF·이미지 공고

`HttpFetcher`는 `application/pdf`, `image/png|jpeg|webp|gif`를 바이트로 넘긴다 (`FetchedPage.body`). 선언한 형식과 파일 머리(매직 바이트)가 맞아야 하고, `application/octet-stream`이면 파일 머리로 판단한다. 크기 상한은 HTML과 같다 (6MB). 버전의 `method`는 그대로 `http`, `adapter`가 `pdf`/`image`다 (migration 없음).

- **PDF**: 원본 자료와 같은 로컬 파서(`PdfTextParser`, pypdf, 300쪽 상한)로 글자를 꺼내 본문 경로(LLM 인용 확인)로 넘긴다. 외부 전송은 LLM 추출 하나뿐이다. `notes`에 `pdf_pages:<글자 있는 쪽>/<전체 쪽>`. 암호·손상 파일은 `pdf_unreadable:<코드>`. 글자가 없으면(스캔본) 아래 스캔 PDF 경로.
- **스캔 PDF** (`pdf_no_text`): 쪽을 이미지로 바꿔 이미지 공고와 같은 비전 경로(`job_extract_image`, 자료 등급 `public`)로 보낸다.
  - 렌더러는 선택 설치 `career-core[pdf-render]` (pypdfium2, 휠 ~4MB·설치 ~8MB, 의존성 없음, BSD-3/Apache-2.0). 이미지 라이브러리 없이 회색조 비트맵을 표준 라이브러리(zlib)로 PNG로 쓴다 (`adapters/pdf_render.py`).
  - 상한: 앞 **4쪽**, 150dpi, 쪽마다 긴 변 1600px·전체 1600×1600px 이하로 줄여 그린다. PNG 하나가 5MB를 넘으면 거기서 멈춘다. 폼·주석은 그리지 않는다. PDFium은 스레드 안전하지 않아 프로세스 안에서 한 번에 하나만 그린다 (`asyncio.to_thread` + 잠금).
  - 라우트부터 본다: 없으면 그리지도 않고 `needs_manual_check` + `pdf_no_text`, `vision_route_missing`. 렌더러가 없으면 `pdf_no_text`, `pdf_render_missing` (화면: "스캔 PDF를 읽으려면 pdf-render 설치"), 그리기 실패는 `pdf_render_failed:<코드>`.
  - 성공하면 `notes`에 `pdf_rendered:<보낸 쪽>/<전체 쪽>`, `ocr_pdf_by_model`. 버전 `adapter`는 `pdf`, 첫 쪽 이미지를 스크린샷 칸에 둔다. 같은 파일(`pdf:<sha256>`)을 다시 가져오면 모델을 부르지 않는다.
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
- 스캔 PDF 비전 비용: 쪽 수만큼 이미지가 간다 (최대 4장). 쪽이 많은 스캔 공고는 앞 4쪽만 읽는다 → 필요하면 쪽 선택.
- 그리팅 선택형 문항(보기)은 넣지 않는다. 지원서 작성 화면에서 보여 줄 필요가 생기면 별도 항목으로.
- 초안 정리(`purge_unconfirmed`)를 주기적으로 돌리는 유지보수 루프
