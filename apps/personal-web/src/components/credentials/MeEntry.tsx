import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../../lib/api";

/** 브레인 색인의 "나" 칸 입구: 자격·이수, 증빙 서류. 손이 갈 것(확인할 후보·곧 만료·민감 정보)만 진하게. */
export function MeEntry() {
  const creds = useQuery({ queryKey: ["credentials", "all"], queryFn: () => api.credentials() });
  const proofs = useQuery({ queryKey: ["sources", "proofs"], queryFn: () => api.proofs() });
  const c = creds.data ?? [];
  const p = proofs.data ?? [];
  const candidates = c.filter((x) => x.status === "extracted").length;
  const soon = c.filter((x) => x.expiry === "soon" && x.status !== "extracted").length;
  const flagged = p.filter((x) => x.resident_number).length;
  return (
    <section className="mt-10">
      <h2 className="text-[15px] font-semibold">나</h2>
      <ul className="mt-2 divide-y divide-line border-y border-line">
        <Entry
          to="/brain/credentials"
          title="자격·이수"
          text="자격증·어학 성적·수료·수상·학력입니다."
          count={c.length - candidates}
          strong={[candidates ? `확인할 것 ${candidates}` : null, soon ? `곧 만료 ${soon}` : null]}
        />
        <Entry
          to="/brain/proofs"
          title="증빙 서류"
          text="사본 파일을 그대로 보관합니다. 외부 모델에 보내지 않습니다."
          count={p.length}
          strong={[flagged ? `주민등록번호 보임 ${flagged}` : null]}
        />
      </ul>
    </section>
  );
}

function Entry({
  to,
  title,
  text,
  count,
  strong,
}: {
  to: string;
  title: string;
  text: ReactNode;
  count: number;
  strong: (string | null)[];
}) {
  const marks = strong.filter(Boolean).join(" · ");
  return (
    <li>
      <Link
        to={to}
        className="-mx-2 flex min-h-9 items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{title}</div>
          <div className="truncate text-[13px] text-muted">{text}</div>
        </div>
        {marks ? <span className="shrink-0 text-[13px] font-semibold tabular-nums">{marks}</span> : null}
        {count ? <span className="shrink-0 text-[13px] text-muted tabular-nums">{count}</span> : null}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}
