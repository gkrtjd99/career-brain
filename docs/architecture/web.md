# 개인 대시보드 웹 (O10, O07·O09 화면)

`apps/personal-web` — React 19 + Vite + TypeScript. 데스크톱은 왼쪽 메뉴, 375px 폭 모바일은 아래 탭(오늘·브레인·공고·검색·더보기). 밝게/어둡게/기기 설정.

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
  lib/dates.ts    날짜 표시 도우미: 공간 표시 시간대(설정 "작업 공간")로 날짜·시각·D-day·datetime-local 변환 (AppShell이 설정을 읽어 정한다)
  components/     AppShell(메뉴), ClaimItem, ClaimPicker(검색으로 주장 고르기), InterviewPanel, SpanSheet,
                  SourceSheet(검색 결과 → 원본 블록), UploadSheet, DdayBadge
  components/doc/ BlockEditor(문단 편집), ChecksPanel(품질 검사), SuggestionsPanel(AI 수정안), BatchSuggestSheet(문단별 일괄 수정안, c0023)
  components/runs/ RunParts(예상 비용·실행 진행·멱등 키), InterviewBatchSheet(경험별 일괄 인터뷰 질문, c0023)
  components/study/ parts(응시·오답 기록, 복습 카드)
  components/settings/ WorkspaceSettingsSection(표시 시간대, 기술명 별칭 사전)
  pages/          Today, Brain, Experience, Search, Postings, Posting, Applications, Application, Document,
                  Study, StudyItem, Usage, Settings
```

`packages/ui` 의 클래스는 앱의 색 토큰(`bg-surface`, `text-muted`, `border-line`, `text-accent`, `bg-ok-soft` …)을 쓴다. 토큰은 `apps/personal-web/src/index.css` 의 CSS 변수로 정의하고, 다른 앱(서비스)은 같은 이름으로 자기 값을 주면 된다.

## 화면

| 경로 | 내용 |
|---|---|
| `/` 오늘 | 마감 임박(저장 공고, 14일 이내 D-day)과 지원별 안 쓴 문항 → 오늘 복습(오답) → 확인 대기(추출 경험, 공고 변경, 미확정 공고, 가져오기 실패·목록 페이지, 진행 중) → 최근 활동(주장·공고·가져오기·문서 저장·응시, 시간순). 모델 비용은 예산 80% 이상·오류가 있을 때만 |
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

## 알려진 한계

- 브라우저 확인은 375px·밝게/어둡게에서 했지만 화면 테스트(자동)는 없다.
- 원본 보기는 검색 결과의 블록 앞뒤 2개만 보여 준다. 쪽 이미지 보기는 없다.
- 지원 칸반은 끌어 옮기기가 없다. 상태는 지원 화면의 버튼으로 바꾼다.
- 개인용 인증은 Bearer 토큰뿐이다. plan §14의 HttpOnly 세션 쿠키가 생기면 `createClient` 의 `getToken` 대신 `credentials: "include"` 를 쓰면 된다.
