// 프로필·증명사진 API (O21, plan §20 "나 › 프로필"). 공간마다 프로필 하나, 사진은 변환본만.
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

/** 사진 변환 도구의 미리 정한 크기 id (apps/personal-web/src/lib/photo.ts SIZE_PRESETS) + custom */
export type PhotoSpec = "cm-3x4" | "cm-3.5x4.5" | "cm-2.5x3" | "px-150x200" | "custom";
export const PHOTO_SPECS: PhotoSpec[] = ["cm-3x4", "cm-3.5x4.5", "cm-2.5x3", "px-150x200", "custom"];

export interface ProfileLink {
  label: string;
  url: string;
}

export interface Profile {
  name: string | null;
  name_en: string | null;
  /** YYYY-MM-DD */
  birth_date: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  links: ProfileLink[];
  headline: string | null;
  default_photo_id: Uuid | null;
  /** 저장한 적이 없으면 null */
  updated_at: IsoDateTime | null;
}

/** 프로필 전체. 빠진 칸·빈 칸은 비운다 */
export type ProfileIn = Partial<Omit<Profile, "default_photo_id" | "updated_at">>;

export interface ProfilePhoto {
  id: Uuid;
  spec: PhotoSpec;
  width_px: number;
  height_px: number;
  bytes: number;
  mime: "image/jpeg" | "image/png";
  sha256: string;
  is_default: boolean;
  created_at: IsoDateTime;
}

export interface ProfilePhotoUpload extends ProfilePhoto {
  deduplicated: boolean;
  /** 서버에서 지운 메타데이터 조각 (APP1 = EXIF 등) */
  removed_metadata: string[];
}

interface ProfileDeps {
  request: (method: string, path: string, opts?: { json?: unknown; form?: FormData }) => Promise<Response>;
  get: <R>(path: string) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

export function profileEndpoints({ request, get, patch }: ProfileDeps) {
  const id = encodeURIComponent;
  const photo = (photoId: string) => `/core/profile/photos/${id(photoId)}`;
  return {
    profile: () => get<Profile>("/core/profile"),
    saveProfile: async (body: ProfileIn) =>
      (await (await request("PUT", "/core/profile", { json: body })).json()) as Profile,
    profilePhotos: () => get<ProfilePhoto[]>("/core/profile/photos"),
    /** 변환한 사진 한 장 (JPEG·PNG, 2MB까지). 413 too_large · 415 unsupported_type · 422 spec_mismatch */
    uploadProfilePhoto: async (file: Blob, spec: PhotoSpec, filename = "photo") => {
      const form = new FormData();
      form.set("file", file, filename);
      form.set("spec", spec);
      return (await (await request("POST", "/core/profile/photos", { form })).json()) as ProfilePhotoUpload;
    },
    /** 사진 바이트 (인증이 필요해 <img src>로 바로 못 쓴다: object URL로 바꿔 쓴다) */
    profilePhotoFile: async (photoId: string) => (await request("GET", `${photo(photoId)}/file`)).blob(),
    setDefaultProfilePhoto: (photoId: string) => patch<ProfilePhoto>(photo(photoId), { is_default: true }),
    deleteProfilePhoto: async (photoId: string) =>
      (await (await request("DELETE", photo(photoId))).json()) as { default_photo_id: Uuid | null },
  };
}
