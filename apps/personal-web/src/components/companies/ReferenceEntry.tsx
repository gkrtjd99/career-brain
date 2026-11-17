import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { api } from "../../lib/api";

/** 브레인 아래의 참고 자료 입구. 내 경험과 섞지 않도록 한 줄만 둔다. */
export function ReferenceEntry() {
  const q = useQuery({ queryKey: ["companies", ""], queryFn: () => api.companies("") });
  const n = q.data?.length ?? 0;
  return (
    <section className="mt-10">
      <h2 className="text-[15px] font-semibold">참고</h2>
      <ul className="mt-2 divide-y divide-line border-y border-line">
        <li>
          <Link
            to="/brain/companies"
            className="-mx-2 flex min-h-9 items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
          >
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">회사</div>
              <div className="truncate text-[13px] text-muted">
                지원할 회사의 공개 정보입니다. 문서 근거로 쓰지 않습니다.
              </div>
            </div>
            {n ? <span className="text-[13px] text-muted tabular-nums">{n}</span> : null}
            <ChevronRight size={16} className="shrink-0 text-muted" />
          </Link>
        </li>
      </ul>
    </section>
  );
}
