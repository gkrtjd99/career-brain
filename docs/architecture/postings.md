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
5. 항목 추출 순서
   1. schema.org `JobPosting` (JSON-LD) — 근거 `structured: ld:JobPosting.<경로>`
   2. 알려진 채용 시스템 어댑터 (ninehire: `__NEXT_DATA__`)
   3. LLM (`job_extract` 목적, 게이트웨이 경유: 전송 정책·단가·예산·장부). 모델은 값과 **원문 인용**을 낸다. 코드가 인용을 원문에서 찾지 못하면 버린다. 날짜는 월·일 숫자가 인용에 있어야 하고, 짧은 값은 인용 안에 있어야 한다. 버린 항목은 실행 기록 `notes`의 `llm_dropped:`.
   - 제목·회사·마감이 비면 `missing` → 사용자 확인.
6. 버전 — 본문 해시가 같으면 새 버전도 LLM 호출도 없다 (`unchanged`). 확정한 공고가 바뀌면 `changed_fields` → `pending_changes`; 확정 값은 사용자가 다시 확인할 때까지 그대로.
7. `POST .../confirm {version_id?, overrides, clear}` — 사용자가 고친 값은 근거 `user`. 마감은 ISO 날짜/시각 또는 `always`.
8. 실패(`login_required`, `rate_limited`, `source_unavailable`, `too_large`, `unsupported_content`…)는 마감이 아니다. `POST .../manual-text`로 본문을 붙여넣을 수 있다.

## 실제 확인 (2026-11-16)

| 페이지 | 결과 |
|---|---|
| ninehire 채용 페이지 | `ninehire` 어댑터: 제목·회사·고용형태·경력·마감·근무지 구조화 근거, 요건·우대·절차 인용 |
| 공고 모음 사이트 목록 | `listing`, 후보 43건 (따라가지 않음) |
| 공고 모음 사이트 글 | `text`+LLM: 제목·회사·고용형태·요건·절차 인용 근거, 마감은 원문에 없어 `missing` |

세 페이지 모두 다시 가져오기 3회에 새 버전 0건.

## 범위 밖 / 백로그

- 이미지·PDF 공고 OCR (지금은 `unsupported_content` → 업로드 경로)
- 다른 채용 시스템 어댑터 (greeting, lever, greenhouse 등)
- 초안 정리(`purge_unconfirmed`)를 주기적으로 돌리는 유지보수 루프
