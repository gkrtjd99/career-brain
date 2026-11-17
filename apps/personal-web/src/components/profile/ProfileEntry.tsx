import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { api } from "../../lib/api";

/** 브레인 아래 "나" 칸의 프로필 입구 (O21). 이름·한 줄 소개가 있으면 그것을, 없으면 무엇을 두는지 보여 준다. */
export function ProfileEntry() {
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const photos = useQuery({ queryKey: ["profile", "photos"], queryFn: api.profilePhotos });
  const p = profile.data;
  const summary = p ? [p.name, p.headline].filter(Boolean).join(" · ") : "";
  const n = photos.data?.length ?? 0;
  return (
    <section className="mt-10">
      <h2 className="text-[15px] font-semibold">나</h2>
      <ul className="mt-2 divide-y divide-line border-y border-line">
        <li>
          <Link
            to="/brain/profile"
            className="-mx-2 flex min-h-9 items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
          >
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">프로필</div>
              <div className="truncate text-[13px] text-muted">
                {summary || "이름·연락처·링크와 증명사진을 둡니다."}
              </div>
            </div>
            {n ? <span className="text-[13px] text-muted tabular-nums">사진 {n}</span> : null}
            <ChevronRight size={16} className="shrink-0 text-muted" />
          </Link>
        </li>
      </ul>
    </section>
  );
}
