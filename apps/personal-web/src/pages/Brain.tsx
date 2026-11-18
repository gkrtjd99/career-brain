import { Button, cn } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, FileUp, Upload } from "lucide-react";
import { useIngest } from "../components/GlobalActions";
import { OrganizeLine } from "../components/brain/OrganizeLine";
import {
  AnswersSection,
  CareerSection,
  CredentialSection,
  DetailsSections,
  EducationSection,
  PersonalSection,
  PortfolioSection,
  ProjectSection,
  SkillSection,
  TrainingSection,
} from "../components/brain/ResumeSections";
import { PageHeader } from "../components/common";
import { IngestDropTarget } from "../components/ingest/DropTarget";
import { api } from "../lib/api";

const route = getRouteApi("/brain");

/**
 * 브레인 첫 화면 (2026-11-18 다시 짬): 이력서 한 장처럼 읽는다.
 *
 * - 맨 위: "브레인에 넣기" 하나와 정리 상태 한 줄. 넣은 것은 서버가 알아서 정리하고(c0052), 사람은 그 줄에서
 *   확인할 것으로 들어간다. 화면 어디에 파일을 놓아도 넣는다.
 * - 가운데: 사람인·잡코리아 이력서 항목 순서의 칸 (ResumeSections).
 * - 아래 "더 보기": 받은 자료·증빙·이력서 판본·기본 문서·지난 제출본·회사·참고 글·찾기.
 * 경험 타임라인·표·필터는 경력·프로젝트 칸의 "모두 보기"(/brain/experiences)로.
 */
export function BrainPage() {
  const ingest = useIngest();
  const { check } = route.useSearch();
  const navigate = useNavigate();
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });
  const credentials = useQuery({ queryKey: ["credentials", "all"], queryFn: () => api.credentials() });
  const setCheck = (open: boolean) =>
    navigate({ to: "/brain", search: open ? { check: true } : {}, replace: true });
  const empty = experiences.data?.length === 0 && credentials.data?.length === 0;

  return (
    <IngestDropTarget onFiles={(f) => ingest.open(undefined, f)}>
      <div className="max-w-3xl">
        <PageHeader
          title="브레인"
          actions={
            <Button variant="primary" onClick={() => ingest.open()}>
              <Upload size={15} /> 브레인에 넣기
            </Button>
          }
        />
        <div className="-mt-5 mb-6">
          <OrganizeLine open={!!check} onOpenChange={setCheck} />
        </div>

        {empty ? <FirstDrop onPick={() => ingest.open()} /> : null}

        <div className="border-b border-line">
          <PersonalSection />
          <CareerSection experiences={experiences.data} />
          <EducationSection />
          <ProjectSection experiences={experiences.data} />
          <TrainingSection experiences={experiences.data} credentials={credentials.data} />
          <CredentialSection credentials={credentials.data} />
          <SkillSection />
          <PortfolioSection />
          <DetailsSections />
          <AnswersSection />
        </div>

        <More />
      </div>
    </IngestDropTarget>
  );
}

/** 아직 아무것도 없을 때만: 무엇을 넣으면 되는지 한 번 보여 준다 */
function FirstDrop({ onPick }: { onPick: () => void }) {
  return (
    <button
      type="button"
      onClick={onPick}
      className={cn(
        "mb-8 flex w-full flex-col items-center gap-2 rounded-lg border border-dashed border-line px-4 py-8 text-center",
        "transition-colors duration-150 hover:border-line-strong hover:bg-subtle/40",
      )}
    >
      <FileUp size={20} aria-hidden className="text-muted" />
      <span className="text-sm font-medium">이력서·경력기술서·자격증 사본을 여기에 놓으세요</span>
      <span className="text-[13px] text-muted">읽고 경력·학력·자격·기술로 나눠 둡니다. 모르는 것만 묻습니다.</span>
    </button>
  );
}

const MORE_ROW =
  "-mx-2 flex min-h-11 items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors duration-150 hover:bg-subtle/60";

/** 자주 보지 않는 것: 조용한 목록. 모든 기능은 여기서 닿는다 */
function More() {
  const inbox = useQuery({ queryKey: ["sources", "inbox"], queryFn: () => api.sources({ attention: true, limit: 50 }) });
  const proofs = useQuery({ queryKey: ["sources", "proofs"], queryFn: () => api.proofs() });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: () => api.resumes() });
  const shared = useQuery({ queryKey: ["shared-documents"], queryFn: api.sharedDocuments });
  const submitted = useQuery({ queryKey: ["submitted-documents"], queryFn: api.submittedDocuments });
  const companies = useQuery({ queryKey: ["companies", ""], queryFn: () => api.companies("") });
  const references = useQuery({ queryKey: ["references", {}], queryFn: () => api.references() });
  const flagged = (proofs.data ?? []).filter((x) => x.resident_number).length;
  const unsupported = (resumes.data ?? []).reduce((n, r) => n + r.unsupported_lines, 0);
  const rows: { to: string; label: string; count?: number; mark?: string | null }[] = [
    {
      to: "/brain/sources",
      label: "받은 자료",
      mark: (inbox.data ?? []).some((s) => s.attention === "purpose_unconfirmed") ? "확인 필요" : null,
    },
    { to: "/brain/proofs", label: "증빙 서류", count: proofs.data?.length, mark: flagged ? `주민등록번호 보임 ${flagged}` : null },
    { to: "/brain/resumes", label: "이력서 판본", count: resumes.data?.length, mark: unsupported ? `근거 없는 줄 ${unsupported}` : null },
    { to: "/documents", label: "기본 문서", count: shared.data?.length },
    { to: "/brain/submissions", label: "지난 제출본", count: submitted.data?.length },
    { to: "/brain/companies", label: "회사", count: companies.data?.length },
    { to: "/brain/references", label: "참고 글", count: references.data?.length },
    { to: "/brain/experiences", label: "경험 찾기·타임라인" },
    { to: "/search", label: "근거 전체 검색" },
  ];
  return (
    <section aria-labelledby="brain-more" className="mt-12">
      <h2 id="brain-more" className="text-[15px] font-semibold">
        더 보기
      </h2>
      <ul className="mt-2 grid border-t border-line sm:grid-cols-2 sm:gap-x-8">
        {rows.map((r) => (
          <li key={r.to} className="border-b border-line">
            <Link to={r.to} className={MORE_ROW}>
              <span className="min-w-0 flex-1 truncate">{r.label}</span>
              {r.mark ? <span className="shrink-0 text-[13px] font-semibold">{r.mark}</span> : null}
              {r.count !== undefined ? (
                <span className="shrink-0 text-[13px] text-muted tabular-nums">{r.count}</span>
              ) : null}
              <ChevronRight size={16} className="shrink-0 text-muted" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
