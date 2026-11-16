# 개인 대시보드 웹 (O10)

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
                  Sheet(옆 패널, Radix Dialog), Highlight(원문 위치 강조), EvidenceCard/StatusBadge,
                  MarkdownView(위키용 최소 Markdown, HTML 해석 안 함)
apps/personal-web/src/
  router.tsx      TanStack Router (코드 기반 route, 파일 생성기 없음)
  lib/api.ts      토큰 저장, client, QueryClient (401 → 설정)
  lib/labels.ts   상태·오류 코드 → 한국어 문구
  components/     AppShell(메뉴), ClaimItem, InterviewPanel, SpanSheet, UploadSheet, DdayBadge
  pages/          Today, Brain, Experience, Search, Postings, Posting, Usage, Settings, Coming
```

`packages/ui` 의 클래스는 앱의 색 토큰(`bg-surface`, `text-muted`, `border-line`, `text-accent`, `bg-ok-soft` …)을 쓴다. 토큰은 `apps/personal-web/src/index.css` 의 CSS 변수로 정의하고, 다른 앱(서비스)은 같은 이름으로 자기 값을 주면 된다.

## 화면

| 경로 | 내용 |
|---|---|
| `/` 오늘 | 마감 임박(저장 공고, 14일 이내 D-day) → 확인 대기(추출 경험, 공고 변경, 미확정 공고, 가져오기 실패·목록 페이지, 진행 중) → 최근 공고. 모델 비용은 예산 80% 이상·오류가 있을 때만 |
| `/brain` | 경험 목록(확인 필요/확인됨 수), 경험 추가, 자료 올리기(업로드 → 읽기 상태 → 경험 추출 요청 → 결과) |
| `/brain/:id` | 주장 탭: 확인 대기/확인함/이견/보관, 맞아요(기여 미상이면 본인·팀 선택)·고치기·사실과 달라요·보관, 원문 근거 패널. 위키 탭: Markdown 보기·복사·내려받기. 인터뷰 탭: 질문·답변·AI 보충 질문 |
| `/search` | 검색어, 종류(주장·원문·인터뷰 답변) 필터, 키워드/의미 모드와 이유 |
| `/postings` | URL 여러 개 붙여넣기(최대 20), 요청 결과, 상태별 목록, 가져오는 중이면 자동 새로고침 |
| `/postings/:id` | 항목별 추출값과 근거(구조화 데이터 경로 / 원문 인용 → 원문 패널에서 강조 / 직접 입력), 고쳐서 저장(바뀐 값은 `overrides`, 비운 값은 `clear`), 다시 가져오기, 보관, 실패 사유, 목록 후보 고르기, 본문 직접 붙여넣기, 버전·가져오기 기록 |
| `/usage` | 기간·묶음별 호출·토큰·청구·환산 비용, 월 예산 |
| `/settings` | 토큰, 테마 |
| `/applications`, `/study` | 준비 중 (O07, O09) |

## 의존성

런타임: `@tanstack/react-query`, `@tanstack/react-router`, `@radix-ui/react-dialog`, `lucide-react`, `clsx`, `tailwind-merge`, `pretendard`. 빌드: `tailwindcss` v4 + `@tailwindcss/vite`. Tiptap 은 문서 편집(O07)에서 넣는다.

빌드 크기(대략): JS 약 500 KB (gzip 약 156 KB), CSS 약 75 KB (대부분 Pretendard `@font-face`). 글꼴은 Pretendard 동적 subset woff2(92개, 합계 약 3 MB)를 함께 내보내지만 브라우저는 화면에 나온 글자 범위의 파일만 받는다. CDN을 쓰지 않는다.

## 외부 자료 라이선스

- Pretendard — SIL Open Font License 1.1 (글꼴 CSS 머리말에 고지 포함)
- React, TanStack Query/Router, Radix UI, lucide-react, clsx, tailwind-merge, Tailwind CSS — MIT (lucide-react 는 ISC)

## 알려진 한계 (백엔드 쪽)

- 인터뷰 목록 API가 없다. 웹은 경험별 마지막 세션 ID를 브라우저에 기억한다.
- 원본 목록·최근 활동 API가 없다. 오늘 화면의 "최근"은 공고 목록 순서로 대신한다.
- 공고 목록 응답에 시각(생성·저장)이 없고, 공고 상세의 버전은 최신 1개 + 가져오기 기록(최근 10건)의 `version_id` 로만 찾을 수 있다.
- 개인용 인증은 Bearer 토큰뿐이다. plan §14의 HttpOnly 세션 쿠키가 생기면 `createClient` 의 `getToken` 대신 `credentials: "include"` 를 쓰면 된다.
