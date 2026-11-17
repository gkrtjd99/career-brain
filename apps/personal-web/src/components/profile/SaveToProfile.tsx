import type { PhotoSpec } from "@career-brain/client";
import { Button } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { UserRound } from "lucide-react";
import type { ReactNode } from "react";
import { errorText } from "../common";
import { api, useToken } from "../../lib/api";
import { PHOTO_SPEC_LABEL, profileErrorText } from "../../lib/profile";

/**
 * 사진 변환 결과를 프로필 증명사진으로 저장한다 (O21). 변환한 파일만 보낸다 — 원본은 보내지 않는다.
 * 토큰이 없으면(이 화면은 토큰 없이도 열린다) 연결하라고만 알린다.
 */
export function SaveToProfile({
  blob,
  filename,
  spec,
  disabled,
  download,
}: {
  blob: Blob;
  filename: string;
  spec: PhotoSpec;
  /** 만드는 중·오류 */
  disabled: boolean;
  /** 같은 줄 앞에 두는 내려받기 버튼 */
  download: ReactNode;
}) {
  const token = useToken();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: () => api.uploadProfilePhoto(blob, spec, filename),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profile"] }),
  });

  if (!token)
    return (
      <div className="space-y-1.5">
        {download}
        <p className="text-[13px] text-muted">
          <Link to="/connect" search={{ next: "/tools/photo" }} className="text-fg underline underline-offset-2">
            연결
          </Link>
          하면 이 사진을 프로필 증명사진으로 저장할 수 있습니다.
        </p>
      </div>
    );

  const isWebp = blob.type === "image/webp";
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {download}
        <Button
          onClick={() => save.mutate()}
          disabled={disabled || isWebp || save.isPending || save.isSuccess}
          data-testid="photo-save-profile"
        >
          <UserRound size={16} /> {save.isPending ? "저장하는 중…" : "프로필에 저장"}
        </Button>
      </div>
      {isWebp ? (
        <p className="text-[13px] text-muted">프로필에는 JPG·PNG만 저장합니다.</p>
      ) : save.isSuccess ? (
        <p role="status" className="text-[13px] text-muted">
          {save.data.deduplicated ? "이미 저장한 사진입니다." : `${PHOTO_SPEC_LABEL[spec]} 사진으로 저장했습니다.`}{" "}
          <Link to="/brain/profile" className="text-fg underline underline-offset-2">
            프로필에서 보기
          </Link>
        </p>
      ) : save.error ? (
        <p role="alert" className="text-[13px] text-danger">
          {profileErrorText(save.error) ?? errorText(save.error)}
        </p>
      ) : (
        <p className="text-[13px] text-muted">
          {PHOTO_SPEC_LABEL[spec]} 사진으로 저장합니다. 변환한 파일만 보냅니다.
        </p>
      )}
    </div>
  );
}
