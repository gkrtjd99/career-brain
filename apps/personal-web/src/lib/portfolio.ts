// 포트폴리오 화면의 말 (O18). 공개 범위·묶음 종류·링크 종류·줄 상태·오류.
import type {
  PortfolioBundleKind,
  PortfolioImageKind,
  PortfolioLineState,
  PortfolioLinkKind,
  PortfolioVisibility,
} from "@career-brain/client";
import { ApiError } from "./api";

export const VISIBILITY_LABEL: Record<PortfolioVisibility, string> = {
  public: "공개 가능",
  application: "지원서에만",
  private: "나만",
};

export const VISIBILITY_NOTE: Record<PortfolioVisibility, string> = {
  public: "모든 묶음과 Markdown 내보내기에 나갑니다.",
  application: "공고별 묶음에서만 나갑니다.",
  private: "어떤 출력에도 나가지 않습니다.",
};

export const BUNDLE_KIND_LABEL: Record<PortfolioBundleKind, string> = {
  all: "전체",
  role: "직무별",
  posting: "공고별",
};

export const LINK_KIND_LABEL: Record<PortfolioLinkKind, string> = {
  repo: "저장소",
  demo: "데모",
  article: "글",
  video: "영상",
};

export const IMAGE_KIND_LABEL: Record<PortfolioImageKind, string> = {
  screenshot: "스크린샷",
  diagram: "구성도",
};

export const LINE_STATE_LABEL: Record<PortfolioLineState, string | null> = {
  none: "근거 없음",
  ok: null,
  stale: "사실이 바뀜",
  broken: "근거 사라짐",
};

/** 브라우저에서 줄이는 한도 (서버 domain.portfolio와 같다) */
export const MAX_EDGE = 1600;
export const MAX_IMAGE_BYTES = 1024 * 1024;

const ERRORS: Record<string, string> = {
  revision_conflict: "그사이 내용이 바뀌었습니다. 최신 내용을 다시 불러왔으니 한 번 더 해 주세요.",
  experience_not_active: "보관한 경험에는 항목을 만들 수 없습니다.",
  claim_not_confirmed: "확인한 사실만 줄로 넣을 수 있습니다. 브레인에서 먼저 확인합니다.",
  claim_not_in_experience: "이 경험(과 그 아래 경험)의 사실만 고를 수 있습니다.",
  claim_already_used: "이 사실은 이미 줄로 들어가 있습니다.",
  unknown_claim: "사실을 찾지 못했습니다.",
  no_evidence: "근거가 이어진 줄이 아닙니다.",
  title_required: "제목을 적어 주세요.",
  empty_line: "빈 줄은 넣지 않습니다.",
  line_too_long: "줄이 너무 깁니다 (500자까지).",
  too_many_lines: "한 칸의 줄은 20개까지입니다.",
  invalid_url: "주소를 읽지 못했습니다. github.com/이름/저장소 모양으로 넣습니다.",
  invalid_link: "링크 주소를 읽지 못했습니다. https://로 시작하는 주소를 넣습니다.",
  tag_too_long: "기술 태그 하나는 40자까지입니다.",
  text_too_long: "글이 너무 깁니다 (2000자까지).",
  too_many_links: "링크는 10개까지입니다.",
  too_many_tags: "기술 태그는 20개까지입니다.",
  empty: "빈 파일입니다.",
  image_too_large: "이미지가 1MB를 넘습니다. 더 작게 줄여 다시 올립니다.",
  image_too_big: "긴 변이 1600px를 넘습니다. 더 작게 줄여 다시 올립니다.",
  invalid_dimensions: "이미지가 너무 작습니다 (한 변 16px 이상).",
  unsupported_type: "JPG·PNG 이미지만 올릴 수 있습니다.",
  invalid_image: "이미지 파일을 읽지 못했습니다.",
  too_many_images: "한 항목의 이미지는 12장까지입니다.",
  invalid_parent: "직무별은 전체 묶음에서, 공고별은 전체나 직무별 묶음에서 만듭니다.",
  application_needs_posting: "지원에 잇는 것은 공고별 묶음만 됩니다.",
  bundle_has_children: "이 묶음에서 만든 묶음이 있어 삭제할 수 없습니다. 아래 묶음부터 삭제합니다.",
  unknown_item: "항목을 찾지 못했습니다.",
  too_many_items: "한 묶음의 항목은 50개까지입니다.",
  prep_not_chosen: "지원 준비에서 고른 경험이 아직 없습니다.",
  no_application: "이 묶음은 지원에 이어져 있지 않습니다.",
  nothing_to_export: "이 묶음에 나갈 항목이 없습니다. 공개 범위를 확인합니다.",
  resume_empty: "고른 이력서에 아직 내용이 없습니다.",
  not_github: "GitHub 저장소 주소만 가져옵니다 (github.com/이름/저장소).",
  repo_not_found: "저장소를 찾지 못했습니다. 비공개 저장소면 토큰을 넣고 '비공개 저장소'를 켭니다.",
  repo_forbidden: "이 저장소를 읽을 권한이 없습니다.",
  rate_limited: "GitHub 요청 한도에 닿았습니다. 잠시 뒤에 다시 합니다.",
  token_missing: "저장한 GitHub 토큰이 없습니다. 먼저 토큰을 넣습니다.",
  token_needs_reentry: "저장한 토큰을 읽을 수 없습니다. 토큰을 다시 넣습니다.",
  token_invalid: "GitHub가 토큰을 받지 않았습니다. 토큰을 다시 넣습니다.",
  invalid_token: "토큰 모양이 올바르지 않습니다.",
  token_storage_unavailable: "이 설치에는 토큰을 암호화할 열쇠가 없어 저장할 수 없습니다.",
  github_not_configured: "이 설치에서는 GitHub 가져오기를 쓸 수 없습니다.",
  github_unavailable: "GitHub에 닿지 못했습니다. 잠시 뒤에 다시 합니다.",
  blocked_address: "허용한 GitHub 주소 밖으로 이어져 가져오지 않았습니다.",
  readme_too_large: "README가 너무 커서 가져오지 않았습니다.",
  forbidden: "이 작업을 할 권한이 없습니다.",
};

export function portfolioErrorText(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = error.detail as { code?: string; reset_at?: string | null } | null;
  const code = error.code ?? (detail && typeof detail === "object" ? detail.code : undefined);
  if (code === "rate_limited" && detail && typeof detail === "object" && detail.reset_at) {
    const at = new Date(detail.reset_at);
    if (!Number.isNaN(at.getTime()))
      return `GitHub 요청 한도에 닿았습니다. ${at.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 뒤에 다시 합니다.`;
  }
  if (error.status === 403 && !code) return ERRORS.forbidden!;
  return (code && ERRORS[code]) || (error.status === 403 ? ERRORS.forbidden! : null);
}

export function isConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409) return false;
  const detail = error.detail as { code?: string } | null;
  return !!detail && typeof detail === "object" && detail.code === "revision_conflict";
}
