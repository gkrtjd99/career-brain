import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../../lib/api";

const ROW =
  "-mx-2 flex min-h-10 items-center gap-3 rounded-md px-2 py-2 transition-colors duration-150 hover:bg-subtle/60";

/**
 * 브레인 첫머리의 색인 (plan §20). 세 칸: "나 · 사실"은 문서 근거가 되는 내 기록, "나 · 내 글"은 내가 쓴 글,
 * "참고"는 남의 지식(근거 아님). 줄마다 수만, 손이 갈 것(확인할 후보·곧 만료·민감 정보·옮길 기록)만 진하게.
 * 넓은 화면은 세 칸, 중간은 두 칸 + 한 칸, 좁은 화면은 위아래로.
 */
export function BrainIndex({ experiences, pendingExperiences }: { experiences: number; pendingExperiences: number }) {
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const photos = useQuery({ queryKey: ["profile", "photos"], queryFn: api.profilePhotos });
  const creds = useQuery({ queryKey: ["credentials", "all"], queryFn: () => api.credentials() });
  const proofs = useQuery({ queryKey: ["sources", "proofs"], queryFn: () => api.proofs() });
  const education = useQuery({ queryKey: ["education"], queryFn: api.education });
  const skills = useQuery({ queryKey: ["skills"], queryFn: api.skills });
  const shared = useQuery({ queryKey: ["shared-documents"], queryFn: api.sharedDocuments });
  const answers = useQuery({ queryKey: ["answer-assets"], queryFn: api.answerAssets });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: () => api.resumes() });
  const submitted = useQuery({ queryKey: ["submitted-documents"], queryFn: api.submittedDocuments });
  const companies = useQuery({ queryKey: ["companies", ""], queryFn: () => api.companies("") });
  const references = useQuery({ queryKey: ["references", {}], queryFn: () => api.references() });

  const p = profile.data;
  const nPhotos = photos.data?.length ?? 0;
  const c = creds.data ?? [];
  // 옛 학력 기록 (자격·이수에 남은 degree): 확인 전 후보까지 학력으로 옮길 것으로 센다
  const degrees = c.filter((x) => x.kind === "degree").length;
  const credCandidates = c.filter((x) => x.status === "extracted" && x.kind !== "degree").length;
  const soon = c.filter((x) => x.expiry === "soon" && x.status !== "extracted").length;
  const pr = proofs.data ?? [];
  const flagged = pr.filter((x) => x.resident_number).length;
  const refs = references.data ?? [];
  const notesToConfirm = refs.reduce((n, r) => n + r.notes_candidate, 0);

  return (
    <section
      aria-label="브레인 색인"
      className="mt-8 grid gap-8 sm:grid-cols-2 sm:gap-x-10 lg:grid-cols-3 lg:gap-x-8"
    >
      <Compartment title="나 · 사실" note="근거로 씁니다.">
        <Row
          to="/brain/profile"
          title="프로필"
          meta={p?.name ? [p.name, nPhotos ? `사진 ${nPhotos}` : null] : ["비어 있음"]}
        />
        <li>
          <a href="#experiences" className={ROW}>
            <Body title="경험" />
            <Marks strong={pendingExperiences ? [`확인 필요 ${pendingExperiences}`] : []} count={experiences} />
          </a>
        </li>
        <Row
          to="/brain/education"
          title="학력"
          count={education.data?.length ?? 0}
          strong={[degrees ? `옮길 기록 ${degrees}` : null]}
        />
        <Row
          to="/brain/credentials"
          title="자격·어학·수상"
          count={c.length - credCandidates - degrees}
          strong={[credCandidates ? `확인 필요 ${credCandidates}` : null, soon ? `곧 만료 ${soon}` : null]}
        />
        <Row to="/brain/skills" title="기술" count={skills.data?.length ?? 0} />
        <Row
          to="/brain/proofs"
          title="증빙 서류"
          count={pr.length}
          strong={[flagged ? `주민등록번호 보임 ${flagged}` : null]}
        />
      </Compartment>
      <Compartment title="나 · 내 글" note="내가 쓴 글입니다.">
        <Row
          to="/brain/resumes"
          title="이력서"
          sub="마스터 · 직무별 · 공고별"
          count={resumes.data?.length ?? 0}
          strong={[
            (resumes.data ?? []).some((r) => r.unsupported_lines)
              ? `근거 없는 줄 ${(resumes.data ?? []).reduce((n, r) => n + r.unsupported_lines, 0)}`
              : null,
          ]}
        />
        <Row to="/documents" title="기본 문서" sub="포트폴리오·경력기술서" count={shared.data?.length ?? 0} />
        <Row to="/brain/answers" title="공통 답변" count={answers.data?.length ?? 0} />
        <Row to="/brain/submissions" title="지난 제출본" count={submitted.data?.length ?? 0} />
      </Compartment>
      <Compartment title="참고" note="근거로 쓰지 않습니다.">
        <Row to="/brain/companies" title="회사" count={companies.data?.length ?? 0} />
        <Row
          to="/brain/references"
          title="참고 글"
          sub="남의 자기소개서·면접 후기"
          count={refs.length}
          strong={[notesToConfirm ? `확인 필요 ${notesToConfirm}` : null]}
        />
      </Compartment>
    </section>
  );
}

function Compartment({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <p className="text-[13px] text-muted">{note}</p>
      </div>
      <ul className="mt-2 divide-y divide-line border-y border-line">{children}</ul>
    </div>
  );
}

function Row({
  to,
  title,
  sub,
  meta,
  count,
  strong = [],
}: {
  to: string;
  title: string;
  sub?: string;
  meta?: (string | null)[];
  count?: number;
  strong?: (string | null)[];
}) {
  return (
    <li>
      <Link to={to} className={ROW}>
        <Body title={title} sub={sub} />
        {meta ? (
          <span className="min-w-0 truncate text-[13px] text-muted">{meta.filter(Boolean).join(" · ")}</span>
        ) : null}
        <Marks strong={strong} count={count} />
      </Link>
    </li>
  );
}

function Body({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="min-w-0 flex-1 truncate">
      <span className="text-sm font-medium">{title}</span>
      {sub ? <span className="ml-2 text-[13px] text-muted max-sm:hidden">{sub}</span> : null}
    </div>
  );
}

function Marks({ strong, count }: { strong: (string | null)[]; count?: number }) {
  const marks = strong.filter(Boolean).join(" · ");
  return (
    <>
      {marks ? <span className="shrink-0 text-[13px] font-semibold tabular-nums">{marks}</span> : null}
      {count !== undefined ? (
        <span className="w-6 shrink-0 text-right text-[13px] text-muted tabular-nums">{count}</span>
      ) : null}
      <ChevronRight size={16} className="shrink-0 text-muted" />
    </>
  );
}
