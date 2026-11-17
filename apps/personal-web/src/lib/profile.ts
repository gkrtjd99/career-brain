import type { PhotoSpec } from "@career-brain/client";
import { ApiError } from "./api";

/** 증명사진 규격 이름. cm는 사진 변환 도구의 300 dpi 기준 크기이고 어느 기관의 공식 규격도 아니다. */
export const PHOTO_SPEC_LABEL: Record<PhotoSpec, string> = {
  "cm-3x4": "3 × 4 cm",
  "cm-3.5x4.5": "3.5 × 4.5 cm",
  "cm-2.5x3": "2.5 × 3 cm",
  "px-150x200": "150 × 200 px",
  custom: "직접 정한 크기",
};

/** 프로필·사진 오류. 같은 코드가 다른 화면에서 다른 뜻일 수 있어(too_large) 여기서 먼저 본다. */
const PROFILE_ERROR_TEXT: Record<string, string> = {
  too_large: "사진이 2MB를 넘습니다. 사진 변환에서 최대 용량을 정해 다시 만드세요.",
  unsupported_type: "JPG·PNG 사진만 저장할 수 있습니다.",
  invalid_image: "사진 파일을 읽지 못했습니다. 사진 변환에서 다시 만드세요.",
  invalid_dimensions: "가로·세로가 16–4000 px 사이여야 합니다.",
  spec_mismatch: "사진 비율이 고른 규격과 맞지 않습니다.",
  invalid_spec: "알 수 없는 규격입니다.",
  too_many_photos: "증명사진은 30장까지 저장합니다. 쓰지 않는 사진을 삭제하세요.",
  default_required: "기본 사진은 하나 있어야 합니다. 다른 사진을 기본으로 지정하세요.",
  invalid_email: "이메일 형식을 확인하세요.",
  invalid_phone: "전화번호는 숫자와 + - ( ) 만 씁니다.",
  invalid_link: "링크 주소를 확인하세요. http:// 또는 https://로 시작하는 주소를 씁니다.",
  too_many_links: "링크는 10개까지 둡니다.",
  invalid_birth_date: "생년월일을 확인하세요.",
  invalid_name: "이름은 100자까지 씁니다.",
  invalid_address: "주소는 200자까지 씁니다.",
  invalid_headline: "한 줄 소개는 200자까지 씁니다.",
};

export function profileErrorCode(error: unknown): string | null {
  return error instanceof ApiError && error.code && PROFILE_ERROR_TEXT[error.code] ? error.code : null;
}

export function profileErrorText(error: unknown): string | null {
  const code = profileErrorCode(error);
  return code ? PROFILE_ERROR_TEXT[code]! : null;
}
