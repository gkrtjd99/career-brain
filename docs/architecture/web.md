# 개인 대시보드 웹 (O10, O07·O09 화면)

`apps/personal-web` — React 19 + Vite + TypeScript. 데스크톱은 왼쪽 메뉴, 375px 폭 모바일은 아래 탭(오늘·브레인·공고·검색·더보기). 밝게/어둡게/기기 설정. 흑백 테마(검정·흰색, 오류만 빨강).

## 실행

```bash
npm install
uv run uvicorn personal_api.main:app --reload          # API: http://127.0.0.1:8000
npm run dev                                            # 웹: http://localhost:5173 (/api → 8000)
CAREER_API_PROXY=http://127.0.0.1:8765 npm run dev     # API 주소가 다르면
npm run typecheck && npm run build                     # 결과: apps/personal-web/dist
```

처음 열면 설정 화면에서 `uv run personal-api bootstrap` 이 출력한 API 토큰을 넣는다. 저장 전에 `/api/core/me` 로 확인하고, 이 브라우저의 `localStorage` 에만 둔다. 401이 오면 설정 화면으로 돌아간다.

배포 빌드는 정적 파일(`dist/`)이다. 같은 출처에서 `/api` 를 personal-api 로 넘기면 된다. API 주소가 다르면 빌드 때 `VITE_API_BASE` 로 바꾼다. 테마 초기화는 외부 파일(`/theme-init.js`)이라 `script-src 'self'` CSP와 맞는다.

## 구조

```text
packages/client/src/
  types.ts        코어 HTTP 응답 타입 (interfaces/http 의 Pydantic 모델과 맞춤, 손으로 작성)
  index.ts        createClient({ baseUrl, getToken }) — 모든 코어 경로, ApiError(status, code)
packages/ui/src/  Button, Badge, Card/Section/EmptyState/Spinner, Input/Textarea/Select/Label,
                  Sheet(옆 패널, Radix Dialog), ConfirmDialog(가운데 확인 창), Highlight(원문 위치 강조), EvidenceCard/StatusBadge,
                  MarkdownView(위키용 최소 Markdown, HTML 해석 안 함)
apps/personal-web/src/
  router.tsx      TanStack Router (코드 기반 route, 파일 생성기 없음)
  lib/api.ts      토큰 저장, client, QueryClient (401 → 설정)
  lib/labels.ts   상태·오류·검사 코드 → 한국어 문구
  lib/diff.ts     단어 단위 비교 (AI 수정안 보기)
  lib/photo.ts    사진 변환 순수 로직: cm↔px, 자르기 기하(틀 채우기·위치 제한), 용량 맞추기(품질 이분 탐색), JPEG JFIF·PNG pHYs dpi 쓰기
  lib/dates.ts    날짜 표시 도우미: 공간 표시 시간대(설정 "작업 공간")로 날짜·시각·D-day·datetime-local 변환 (AppShell이 설정을 읽어 정한다)
  components/     AppShell(메뉴), ClaimItem, ClaimPicker(검색으로 주장 고르기), InterviewPanel, SpanSheet,
                  SourceSheet(검색 결과 → 원본 블록), UploadSheet, DdayBadge
  components/doc/ BlockEditor(문단 편집), ChecksPanel(품질 검사), SuggestionsPanel(AI 수정안), BatchSuggestSheet(문단별 일괄 수정안, c0023)
  components/runs/ RunParts(예상 비용·실행 진행·멱등 키), InterviewBatchSheet(경험별 일괄 인터뷰 질문, c0023)
  components/study/ parts(응시·오답 기록, 복습 카드)
  components/settings/ WorkspaceSettingsSection(표시 시간대, 기술명 별칭 사전), JudgeCacheSection(판정 응답 캐시 크기·비우기, egress 권한)
  pages/          Today, Brain, Experience, Search, Postings, Posting, Applications, Application, Document,
                  Study, StudyItem, Usage, Settings, PhotoTool(사진 변환)
```

`packages/ui` 의 클래스는 앱의 색 토큰(`bg-surface`, `text-muted`, `border-line`, `text-accent`, `bg-ok-soft` …)을 쓴다. 토큰은 `apps/personal-web/src/index.css` 의 CSS 변수로 정의하고, 다른 앱(서비스)은 같은 이름으로 자기 값을 주면 된다.

## 화면

| 경로 | 내용 |
|---|---|
| `/` 오늘 | 지원 대시보드, 카드·통계 타일 없이 목록과 여백으로. 제목 아래 한 줄 요약(2주 안 마감·쓰는 중·확인할 것). 다가오는 일정(14일, 날짜별: 저장 공고 마감 + 그 지원의 남은 문항, 제출한 지원의 결과 전 전형, 시험) → 진행 중인 지원(준비 중은 남은 문항·D-day, 제출함은 다음 전형) → 아직 지원 전인 공고(있을 때만). 옆 칸: 작은 월 달력(날짜 + 일정 점, 마감 = 채운 점·전형 = 빈 점·시험 = 점선 점, 날짜를 누르면 그날 일정만) → 확인할 것(개수만) → 최근 저장 3개(주장 확인·공고 저장·문서 저장·응시). 좁은 화면은 달력 대신 한 주 띠를 맨 위에, 확인할 것을 일정 바로 아래에. 토큰 만료·승인 요청은 있을 때만 맨 위, 모델 비용은 예산 80% 이상·오류가 있을 때만 맨 아래 한 줄. 전형 일정은 제출한 지원마다 `stages`를 읽는다 (개인 규모) |
| `/brain` | 경험 목록(확인 필요/확인됨 수), 경험 추가, 일괄 인터뷰(c0023), 자료 올리기(업로드 → 읽기 상태 → 경험 추출 요청 → 결과), 올린 자료 목록(읽기·추출 상태, 추출 요청) |
| `/brain/:id` | 주장 탭: 확인 대기/확인함/이견/보관, 맞아요(기여 미상이면 본인·팀 선택)·고치기·사실과 달라요·보관, 원문 근거 패널. 위키 탭: Markdown 보기·복사·내려받기. 인터뷰 탭: 서버의 세션 목록(최근 세션 기본, 여러 개면 고르기)·질문·답변·AI 보충 질문 |
| `/search` | 검색어, 종류(주장·원문·인터뷰 답변) 필터, 키워드/의미 모드와 이유, 원문 결과 → 원본 보기(앞뒤 2블록, 일치 범위 강조) |
| `/postings` | URL 여러 개 붙여넣기(최대 20), 요청 결과, 상태별 목록, 가져오는 중이면 자동 새로고침 |
| `/postings/:id` | 항목별 추출값과 근거(구조화 데이터 경로 / 원문 인용 → 원문 패널에서 강조 / 직접 입력), 고쳐서 저장(바뀐 값은 `overrides`, 비운 값은 `clear`), 다시 가져오기, 보관, 실패 사유, 목록 후보 고르기, 본문 직접 붙여넣기, 전체 버전 목록·가져오기 기록, 저장한 공고면 지원 만들기/열기·요건별 준비 상태 |
| `/usage` | 기간·묶음별 호출·토큰·청구·환산 비용, 월 예산 |
| `/system` | 탭: 실행(상태 필터, 상세 시트: 진행·비용·대기 이유·이벤트, 멈춤·다시 시작·취소) · 승인(상태 필터, 상세 시트: 요청한 문서·버전, **요청 이후 바뀐 것** 비교, 메모, 승인·거절 — 본 내용의 `payload_hash`를 같이 보냄) · 백업·유지보수(행 수, 원본 크기, 마지막 내보내기, 내려받기·age 암호화, 삭제 기록 내려받기, 초안 정리) · MCP 기록(지난 24시간 도구별 호출·오류, 결과·도구 필터, 보존 기간) · 원격 연결(OAuth 설정 요약, 연결 추가·끊기, 최근 토큰 `jti`·폐기, 소유자만). 실행·승인은 SSE(`/core/live`)로 바로 갱신하고 끊기면 몇 초 간격 다시 읽기로 돌아간다. `?tab=&approval=&run=`로 바로 열린다. 오늘 화면 맨 위 "승인 요청" 카드(대기 중일 때만)가 승인 시트로 연결 (같은 SSE로 바로 갱신, 끊기면 30초 간격 다시 읽기) |
| `/settings` | 토큰, 문서 검사(상투어 목록 편집·기본 목록으로 되돌리기, 본인 연락처), 작업 공간(표시 시간대, 기술명 별칭 사전), 테마 |
| `/applications` | 칸반: 관심(지원 없는 저장 공고, 지원 만들기) → 준비 중 → 제출 → 결과. 카드에 D-day, 안 쓴 문항, 다음 할 일 |
| `/applications/:id` | 문항·문서 목록(글자 수/제한, 안 씀·초과, 검사 요약), 문서 추가(새로 쓰기 / 공용 문서에서 복사, 복사 출처 표시), 요건 가시성 표(충족/누락/과장), 철회·결과 기록, 제출: 확인 창(문서별 r#·글자 수·경고, 빈 문서 제외, 민감 정보 경고가 있으면 확인 체크) → 제출본 고정 → 전형 단계 타임라인(추가·결과·일정) + 읽기 전용 제출본(해시·본문·근거·요건) |
| `/applications/:id/documents/:docId` | 문단 편집기, 서버 계산 글자/바이트 수(입력 중 350ms 뒤 `/count`), 저장(`base_revision`, 멱등 키, Ctrl/Cmd+S), 409 충돌 창(비교 / 버리고 최신 받기 / 확인 후 내 글로 새 revision), 저장 안 한 채 떠나면 막음, 문단별 근거 연결(검색으로 확인된 주장 고르기), 검사 탭(제한·위험·조작·근거·참고 묶음, 문단으로 이동), AI 수정안 탭(요청 → 단어 비교 → 적용/거절, 사실 변경 거부 사유 표시), 기록 탭(이전 revision 읽기), 고른 문장에만 근거 붙이기(문장 근거 칩), 내보내기(형식 고르기 → 만든 파일 검사 경고 → 확인 후 받기, PDF는 인쇄 창), 공용 문서에서 복사한 문서면 출처·원본 변경 안내, 제출 뒤 읽기 전용 |
| `/documents` · `/documents/:docId` | 공용 문서 목록(복사해 간 지원 수)·만들기, 편집기는 지원 문서와 같음(공고 문맥 없음) |
| `/study` | 탭: 오늘 복습(답 보기 → 다시·어려움·보통·쉬움 → 다음 날짜) · 요건별 준비(공고 고르기, 근거 있음/확인 대기/학습함/학습 중/빈칸, 주장 연결·해제, 줄별·전체 학습 계획) · 학습 항목 · 시험·과제(상태, 응시, 응시별 오답) · 오답(상태 필터) |
| `/tools/photo` 사진 변환 | 지원서 사진을 정한 px·형식·용량으로 자르고 저장. 브라우저 안에서만 처리하고 API를 부르지 않아 토큰 없이 열린다. 아래 "사진 변환" |
| `/study/items/:id` | 상태, 연결 요건, 학습 기록(분·메모·자료), 연습 응시, 오답, 경험 후보로 올리기(완료나 응시가 있어야, 문장은 직접 쓰고 확인 대기로 들어감) |

## 의존성

런타임: `@tanstack/react-query`, `@tanstack/react-router`, `@radix-ui/react-dialog`, `lucide-react`, `clsx`, `tailwind-merge`, `pretendard`. 빌드: `tailwindcss` v4 + `@tailwindcss/vite`.

### 문서 편집기: Tiptap 대신 문단별 textarea

O07 문서는 `{id, text, origin, claim_ids}` 블록 목록이고, 저장·근거 연결·검사 결과·AI 수정안이 모두 블록 ID에 묶인다. 편집기는 문단 하나에 `<textarea>` 하나를 쓴다.

- 서식이 없다. 자기소개서 칸은 일반 글이고 글자 수는 서버의 `count_text` 가 센다. 리치 텍스트 모델을 블록 ID 목록으로 되돌리는 변환이 필요 없다.
- 블록 ID를 그대로 들고 있어 저장이 `set_blocks` 한 번이다 (새 문단은 `id: null`, 서버가 ID를 준다). Tiptap 은 노드 속성으로 ID를 따로 지켜야 한다.
- 번들: Tiptap + ProseMirror 는 gzip 약 100 KB 이상이다. 지금 편집 화면 전체가 gzip 약 11 KB 다.
- 키: Enter 문단 나누기(한글 조합 중에는 무시), Shift+Enter 줄바꿈, 맨 앞 Backspace 앞 문단과 합치기, 위·아래 화살표 문단 이동, 빈 줄이 있는 글 붙여넣기는 문단으로 나눔.

문장 단위 근거·서식이 필요해지면 다시 본다.

### 저장 충돌

저장은 언제나 `base_revision` 을 보낸다. 409(`revision_conflict`)면 덮어쓰지 않고 창을 띄운다: 지금 서버 글과 비교, 내 글 버리고 최신 받기, 또는 최신 revision 위에 내 글을 새로 저장(사용자가 버튼을 눌러야 함). 같은 저장 재시도는 `idempotency_key`(문서·기준 revision·내용 해시)로 한 번만 반영된다.

빌드 크기(대략): 첫 화면 JS 약 525 KB (gzip 약 166 KB), 지원 문서·지원 상세·학습 화면은 따로 나눠 처음 열 때 받는다(합계 약 81 KB, gzip 약 28 KB). CSS 약 80 KB (대부분 Pretendard `@font-face`). O07·O09 화면 전 기준은 JS 약 500 KB (gzip 약 156 KB). 글꼴은 Pretendard 동적 subset woff2(92개, 합계 약 3 MB)를 함께 내보내지만 브라우저는 화면에 나온 글자 범위의 파일만 받는다. CDN을 쓰지 않는다.

## 외부 자료 라이선스

- Pretendard — SIL Open Font License 1.1 (글꼴 CSS 머리말에 고지 포함)
- React, TanStack Query/Router, Radix UI, lucide-react, clsx, tailwind-merge, Tailwind CSS — MIT (lucide-react 는 ISC)

## 화면용 조회 API

O10에서 비어 있던 것을 작은 읽기 경로로 더했다 (migration 없음).

| 경로 | 권한 | 내용 |
|---|---|---|
| `GET /core/interviews?experience_id=` | `brain.read` | 인터뷰 세션 목록(질문·답 수, 마지막 답 시각) |
| `GET /core/claims?ids=` | `brain.read` | 주장 요약 여러 개(최대 200, 요청 순서, 문서의 근거 칩용) |
| `GET /core/sources` | `sources.read` | 원본 목록 + 최신 버전·읽기·추출 상태 |
| `GET /core/activity?limit=&before=&kinds=` | `workspace.read` + 종류별 읽기 권한 | 주장·공고·가져오기·문서 저장·응시를 시간 역순으로 |
| `GET /core/job-postings/{id}/versions` | `postings.read` | 공고 버전 목록(본문 없음) |
| `POST /core/applications/{id}/documents/{doc}/count` | `applications.read` | 저장 전 글자/바이트 수(저장과 같은 정규화) |
| `GET /core/study/mistakes?status=` | `study.read` | 오답 목록(다음 복습순) |

c0023에서 더한 것: `GET /core/runs/workflows/{workflow}/estimate?units=`(`runs.read`, 일괄 실행 예상 비용 — `runs.md`), `GET /core/interviews?run_id=`(이 일괄 실행이 질문을 만든 세션). 뒤에 더한 것(migration 없음): `POST /core/runs/workflows/{workflow}/estimate` `{input}`(입력 글자 수로 잰 예상, `runs.md`).
인터뷰 질문에 `run_id`(그 질문을 만든 일괄 실행, 직접 요청·정해진 질문은 `null`), 인터뷰 세션 목록에 `run_ids`(질문을 만든 실행들, 오래된 것부터). 숨김 글 검사 코드 `hidden_text_same_color`·`hidden_text_annotation`·`annotation_text`.

c0029: `POST /core/sources` 폼 `hidden_text`(`keep` 기본 · `exclude` = DOCX 숨김 서식 글을 원본 블록에서 뺌), 응답 `UploadOut.hidden_text`·원본 상세 `ParseOut.hidden_text`(실제 값). 자료 올리기 화면은 DOCX를 고르면 "숨김 서식 글은 빼고 읽기"를 보여 준다. 숨김 글 검사 코드 `hidden_text_covered`(PDF 글 뒤에 덮은 불투명 도형·이미지, 이름표 "덮어 가린 글").

함께 바뀐 응답: 공고 목록에 `created_at`·`saved_at`, 지원 목록에 `questions`·`unwritten`, 검색의 원문 결과에 `block_id`·`parse_run_id`·`ordinal`·`match_start`·`match_end`.

## 웹에서 일괄 실행 시작 (c0023)

- 문서 화면(지원 문서, 편집 가능할 때) 머리의 "일괄 수정안" → 시트: 문단 체크(기본 전부, 최대 50), 요청(선택, 500자), 예상 비용, 시작. 저장하지 않은 편집이 있으면 시작 버튼이 꺼지고 먼저 저장하라고 알린다
  (실행은 저장한 revision 기준이고, 실행 중에 저장하면 남은 문단은 멈춘다). 끝나면 수정안 목록을 다시 읽고 "AI 수정안 탭에서 보기"로 넘어간다. 수정안 적용은 여전히 하나씩 사람이 고른다.
- 브레인 화면 머리의 "일괄 인터뷰" → 시트: 경험 체크(확인 필요가 있는 경험이 기본, 최대 20), 경험마다 질문 수 1–5(기본 3), 예상 비용, 시작. 끝나면 이 실행의 세션 목록(답 수)을 보이고 각 경험의 인터뷰 탭으로 잇는다.
- 진행 카드(`RunProgress`): 상태 배지, 대상 n/전체, 진행 막대, 비용·호출 수, 대기·실패 이유, 시스템 화면 실행 상세 링크. 시스템 화면과 같은 실행 상태 알림(SSE `/core/live`, `components/system/live.tsx`의 `LiveProvider`)을 받으면 다시 읽는다. 알림이 끊긴 동안(연결 전·LISTEN 불가·401/403)에만 끝날 때까지 5초마다 다시 읽는다.
- 예상 비용(`CostEstimate`): 시작할 입력 그대로 `POST /core/runs/workflows/{workflow}/estimate`에 보낸다 (요청 글을 치는 동안은 0.4초 묶음). 호출 수·보낼 글 글자 수와, 지난 실행이 있으면 예상과 그 근거를 보인다: "글 길이 기준"(지난 실행에서 잰 호출당 고정분 + 1천 자당) 또는 "호출 평균 기준"(글 길이로 잴 지난 실행이 모자랄 때). 없으면 "예상 없음"과 실행 뒤 시스템 화면에서 실제 비용을 보라는 안내.
- 실시간 연결은 화면마다 하나다: `LiveProvider` 안의 `LiveProvider`는 바깥 연결을 그대로 쓴다. 진행 카드·오늘 화면 승인 요청 카드는 각자 감싸므로 그 카드가 보일 때만 연결한다.
- 인터뷰 탭: 일괄 실행이 만든 세션 칩에 "일괄", 그 질문 카드에 "일괄 <실행 앞 8자>" 배지(시스템 화면 링크). 실행이 만든 질문이 있으면 "실행별로 묶기" — 정해진 질문 / 직접 요청한 AI 질문 / 실행별로 나눠 보인다.
- 자료 올리기는 DOCX도 받는다 (`UploadSheet` 의 accept·안내).
- 375px에서 두 시트·진행·인터뷰 묶기를 합성 데이터로 확인했다 (가로 넘침 없음).

## 사진 변환 (`/tools/photo`)

지원서 양식이 사진을 "가로 150 × 세로 200 px, jpg, 100KB 이하"나 "3×4cm"처럼 정할 때 쓴다. 메뉴는 "사진 변환"(모바일은 더보기).
화면은 `pages/PhotoTool.tsx`, 계산은 `lib/photo.ts` (DOM 객체를 만들지 않는 순수 함수, Node에서 타입만 지우고 돌릴 수 있는 문법).

- **서버로 보내지 않는다.** 파일 읽기·자르기·인코딩을 모두 canvas로 한다. API 호출이 없고 route에 토큰 검사도 없다. 화면 위에 그 안내와
  "다시 인코딩하므로 원본 EXIF(촬영 기기·시각·GPS)는 결과에 없다"를 적는다.
- 열기: 파일 고르기·끌어 놓기. `createImageBitmap(file, { imageOrientation: "from-image" })`로 EXIF 방향을 따른다. 실패하면 `<img>`로 한 번 더
  (Safari는 HEIC를 이쪽으로 연다). 둘 다 못 열면 HEIC면 아이폰 설정('높은 호환성')·JPG 내보내기 안내, 아니면 형식 안내. 40MB까지.
- 크기: 미리 정한 크기 3×4 cm(354×472), 3.5×4.5 cm(413×531), 2.5×3 cm(295×354) — 모두 300 dpi 계산값이고 기관 규격이라고 하지 않는다 —
  와 150×200 px, 직접 입력(px + dpi), cm + dpi(px는 `round(cm / 2.54 × dpi)`). 16–4000 px, 72–1200 dpi. 잘못 넣은 동안은 마지막으로 맞았던 크기를 쓴다.
- 자르기: 틀 = 출력 비율. 끌어서 옮기기, 휠·트랙패드 확대, 두 손가락 확대·이동(pointer events, `touch-action: none`), 확대 막대(1–8배, 로그 눈금),
  90° 회전, 기울기 보정 ±10°, 화살표·+/- 키. 배율 1 = 틀을 빈틈없이 덮는 가장 작은 크기이고, 위치는 회전한 틀의 네 귀퉁이가 사진 안에 남도록 자른다
  (`clampTransform`: 사진 축으로 본 틀의 외접 사각형이 사진 안 ⇔ 네 귀퉁이가 안). 그래서 결과에 빈 곳이 생기지 않는다. 위치는 틀 너비 단위라 같은 비율로
  px만 바꾸면 구도가 그대로다. 참고 안내선(머리 위 10%·턱 80%·세로 가운데)은 켜고 끌 수 있고 규격이 아니라고 적는다.
- 결과: 편집 화면과 같은 `drawPhoto`로 정확히 W×H 캔버스에 그려 `toBlob`. 바뀐 뒤 0.15초에 다시 만들고, 늦게 끝난 옛 결과는 버린다.
  실제 인코딩 결과를 1:1(CSS px)로 보이고(넓으면 상자 안에서 밀어 보기, "화면에 맞추기" 전환), px·형식·품질·KB(1024)·바이트·인쇄 크기를 적는다.
  파일 이름 `photo_354x472.jpg`.
- 형식: JPG·PNG·WebP. JPG·WebP는 품질 막대. 브라우저가 그 형식을 못 쓰면(`blob.type`이 다름) 안내하고 내려받기를 끈다.
  JPG는 투명을 배경색(흰색 기본, 연회색·하늘색·직접)으로 채운다. PNG·WebP는 "투명 유지"를 고를 수 있다.
- 최대 용량: 1KB = 1,000바이트로 맞춘다(양식이 1,000/1,024 어느 쪽으로 세도 통과). 품질 상한 = 막대 값, 하한 5%에서 이분 탐색 7번
  (`fitToBytes`, 인코딩 최대 9번). 돌려주는 결과는 실제로 잰 크기가 제한 이하인 것뿐이다. 5%로도 넘으면 그 크기와 "px를 줄이라"는 안내.
  PNG는 품질이 없어 "용량 제한은 JPG·WebP로"라고 안내한다.
- dpi 기록: JPG는 JFIF APP0 밀도(단위 1 = 인치, SOI 뒤에 없으면 18바이트 APP0를 넣음), PNG는 IHDR 뒤 pHYs(미터당 점, 있던 것은 뺌, CRC 계산).
  WebP는 dpi 자리가 EXIF뿐이라 적지 않고 화면에 그렇게 쓴다. dpi 머리는 용량 측정 전에 붙여 제한 비교에 포함된다.

확인: 순수 함수는 임시 Node 스크립트로(지운 뒤 커밋), 화면은 브라우저에서 합성 그림(그라데이션·격자·도형 인물, 색 귀퉁이 사각형)으로만 했다.
실제 사람 사진은 쓰지도 커밋하지도 않았다. 웹 패키지에 단위 시험 도구가 없어 시험 파일은 더하지 않았다.

의존성은 더하지 않았다 (canvas만). 화면은 처음 열 때 따로 받는다 (JS 약 25 KB, gzip 약 10 KB).

한계: HEIC는 브라우저가 디코딩할 때만 열린다(Chrome·Firefox는 보통 못 연다, 변환 라이브러리는 넣지 않음). 결과 미리보기의 1:1은 CSS px라
고해상도 화면에서는 기기 픽셀 2개 이상으로 보인다. 큰 축소는 브라우저의 `imageSmoothingQuality: "high"`에 맡긴다. 얼굴 위치 자동 맞춤은 없다.

## 알려진 한계

- 브라우저 확인은 375px·밝게/어둡게에서 했지만 화면 테스트(자동)는 없다.
- 원본 보기는 검색 결과의 블록 앞뒤 2개만 보여 준다. 쪽 이미지 보기는 없다.
- 지원 칸반은 끌어 옮기기가 없다. 상태는 지원 화면의 버튼으로 바꾼다.
- 개인용 인증은 Bearer 토큰뿐이다. plan §14의 HttpOnly 세션 쿠키가 생기면 `createClient` 의 `getToken` 대신 `credentials: "include"` 를 쓰면 된다.
