import type { Education } from "@career-brain/client";
import { Button, cn } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { Loaded, PageHeader } from "../components/common";
import { EducationSheet } from "../components/education/EducationSheet";
import { api } from "../lib/api";
import { DEGREE_LABEL, EDUCATION_STATUS_LABEL, gpaText, majorText, periodText } from "../lib/brainMe";

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const COLS = "sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_4.5rem_9.5rem_5.5rem]";

/** 브레인 › 나 › 학력: 최근 학교부터 표 하나. 좁은 화면은 한 줄 요약. */
export function EducationPage() {
  const q = useQuery({ queryKey: ["education"], queryFn: api.education });
  const [sheet, setSheet] = useState<{ education: Education | null } | null>(null);
  return (
    <div>
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="학력"
        actions={
          <Button variant="primary" onClick={() => setSheet({ education: null })}>
            <Plus size={15} /> 학력 추가
          </Button>
        }
      />
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <div>
              <div className={cn("hidden gap-4 border-b border-line pb-2 text-xs text-muted sm:grid", COLS)}>
                <span>학교</span>
                <span>전공</span>
                <span>학위</span>
                <span>기간</span>
                <span className="text-right">학점</span>
              </div>
              <ul className="divide-y divide-line border-b border-line max-sm:border-t">
                {rows.map((e) => (
                  <EducationRow key={e.id} e={e} onOpen={() => setSheet({ education: e })} />
                ))}
              </ul>
            </div>
          ) : (
            <p className="border-t border-line pt-3 text-sm text-muted">
              아직 학력이 없습니다. 학교를 직접 추가하세요. 졸업증명서는{" "}
              <Link to="/brain/proofs" className="text-fg underline underline-offset-2">
                증빙 서류
              </Link>
              로 올려 이을 수 있습니다.
            </p>
          )
        }
      </Loaded>
      <EducationSheet
        open={sheet !== null}
        education={sheet?.education ?? null}
        onClose={() => setSheet(null)}
      />
    </div>
  );
}

function EducationRow({ e, onOpen }: { e: Education; onOpen: () => void }) {
  const major = majorText(e);
  const period = periodText(e);
  const gpa = gpaText(e);
  const school = [e.school, e.campus].filter(Boolean).join(" ");
  const status = `${EDUCATION_STATUS_LABEL[e.status]}${e.transferred ? " · 편입" : ""}`;
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className={cn(ROW, "grid w-full gap-0.5 text-left text-[13px] sm:items-center sm:gap-4", COLS)}
      >
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{school}</span>
          <span className="hidden truncate text-muted sm:block">{status}</span>
        </span>
        {/* 좁은 화면: 전공·학위·기간·학점을 한 줄로 */}
        <span className="truncate text-muted tabular-nums sm:hidden">
          {[major, DEGREE_LABEL[e.degree], status, period, gpa].filter(Boolean).join(" · ")}
        </span>
        <span className="hidden truncate sm:block">{major ?? <Dash />}</span>
        <span className="hidden text-muted sm:block">{DEGREE_LABEL[e.degree]}</span>
        <span className="hidden text-muted tabular-nums sm:block">{period ?? <Dash />}</span>
        <span className="hidden text-right tabular-nums sm:block">{gpa ?? <Dash />}</span>
      </button>
    </li>
  );
}

function Dash() {
  return (
    <span className="text-muted" aria-label="없음">
      –
    </span>
  );
}
