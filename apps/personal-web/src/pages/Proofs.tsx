import type { ProofItem } from "@career-brain/client";
import { Button, cn } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Download, Upload } from "lucide-react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import {
  canExtract,
  useExtractCredentials,
  useOriginalDownload,
} from "../components/credentials/ProofPanel";
import { useIngest } from "../components/GlobalActions";
import { api } from "../lib/api";
import { CREDENTIAL_STATUS_LABEL, extractResultText } from "../lib/credentials";
import { formatDate } from "../lib/dates";
import { fileSize } from "./Sources";

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const BUSY = new Set(["pending", "waiting_provider"]);

/** 브레인 › 나 › 증빙 서류: 사본 파일 보관함. 원본 다운로드, 이어진 기록, 민감 정보 표시, 항목 뽑기. */
export function ProofsPage() {
  const ingest = useIngest();
  const q = useQuery({
    queryKey: ["sources", "proofs"],
    queryFn: () => api.proofs(),
    refetchInterval: (query) =>
      query.state.data?.some((p) => !p.parse_status || BUSY.has(p.parse_status)) ? 3000 : false,
  });
  const download = useOriginalDownload();
  const extract = useExtractCredentials();
  return (
    <div>
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="증빙 서류"
        subtitle="자격증·성적표·증명서 사본입니다. 원본을 그대로 보관하고 외부 모델에 보내지 않습니다."
        actions={
          <Button variant="primary" onClick={() => ingest.open("proof")}>
            <Upload size={15} /> 증빙 넣기
          </Button>
        }
      />
      <ErrorText error={download.error ?? extract.error} className="mb-3" />
      {extract.data ? (
        <p className="mb-3 text-[13px] text-muted" role="status">
          {extractResultText(extract.data)}{" "}
          {extract.data.credential_id ? (
            <Link to="/brain/credentials" className="text-fg underline underline-offset-2">
              자격·이수에서 확인
            </Link>
          ) : null}
        </p>
      ) : null}
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((p) => (
                <ProofRow
                  key={p.id}
                  p={p}
                  onDownload={() => download.mutate({ id: p.id, filename: p.original_filename })}
                  onExtract={() => extract.mutate(p.id)}
                  busy={download.isPending || extract.isPending}
                />
              ))}
            </ul>
          ) : (
            <p className="border-t border-line pt-3 text-sm text-muted">
              증빙 서류가 없습니다. 자격증·성적표·증명서 사본을 넣으면 항목을 뽑아 자격·이수 기록 후보로 만듭니다.{" "}
              <button
                type="button"
                onClick={() => ingest.open("proof")}
                className="inline-flex min-h-9 items-center text-fg underline underline-offset-2 sm:min-h-6"
              >
                증빙 넣기
              </button>
            </p>
          )
        }
      </Loaded>
    </div>
  );
}

function ProofRow({
  p,
  onDownload,
  onExtract,
  busy,
}: {
  p: ProofItem;
  onDownload: () => void;
  onExtract: () => void;
  busy: boolean;
}) {
  const reading = !p.parse_status || BUSY.has(p.parse_status);
  const linked = p.credentials.map((c) =>
    c.status === "extracted" ? `${c.name} (${CREDENTIAL_STATUS_LABEL[c.status]})` : c.name,
  );
  // 날짜를 앞에: 좁은 화면에서 긴 파일 이름이 잘려도 날짜는 보이게
  const meta = [
    formatDate(p.created_at),
    fileSize(p.size_bytes),
    p.original_filename !== p.title ? p.original_filename : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="flex items-center gap-2">
      <Link to="/brain/sources/$sourceId" params={{ sourceId: p.id }} className={cn(ROW, "min-w-0 flex-1")}>
        <div className="truncate text-sm font-medium">{p.title}</div>
        <div className="line-clamp-2 text-[13px] text-muted tabular-nums">{meta}</div>
        <div className="truncate text-[13px]">
          {p.resident_number ? <span className="font-semibold">주민등록번호가 보입니다 · </span> : null}
          <span className={p.candidates ? "font-semibold" : "text-muted"}>
            {reading ? "읽는 중" : linked.length ? linked.join(", ") : "이어진 기록 없음"}
          </span>
        </div>
      </Link>
      {!reading && !p.credentials.length && canExtract(p) ? (
        <Button size="sm" className="shrink-0 max-sm:h-9" onClick={onExtract} disabled={busy}>
          항목 뽑기
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        className="shrink-0 max-sm:h-9 max-sm:w-9 max-sm:px-0"
        onClick={onDownload}
        disabled={busy}
        aria-label={`${p.title} 다운로드`}
      >
        <Download size={15} />
        <span className="max-sm:sr-only">다운로드</span>
      </Button>
    </li>
  );
}
