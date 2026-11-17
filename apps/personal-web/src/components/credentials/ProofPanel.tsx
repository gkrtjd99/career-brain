import type { ProofItem } from "@career-brain/client";
import { Button, cn } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Download } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { CREDENTIAL_KIND_LABEL, CREDENTIAL_STATUS_LABEL, extractResultText } from "../../lib/credentials";
import { ErrorText } from "../common";
import { saveBlob } from "../doc/ExportSheet";
import { CredentialSheet } from "./CredentialSheet";

const READ_DONE = new Set(["complete", "partial"]);

/** 원본 내려받기 (서버가 저장한 해시와 같은 바이트만 보낸다) */
export function useOriginalDownload() {
  return useMutation({
    mutationFn: (p: { id: string; filename: string | null }) => api.downloadOriginal(p.id, p.filename ?? "original"),
    onSuccess: (f) => saveBlob(f.blob, f.filename),
  });
}

/** 증빙 "항목 뽑기" (로컬 규칙, 모델 호출 없음) */
export function useExtractCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => api.extractCredentials(sourceId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["credentials"] });
      void qc.invalidateQueries({ queryKey: ["sources", "proofs"] });
    },
  });
}

export function canExtract(p: Pick<ProofItem, "parse_status">) {
  return !!p.parse_status && READ_DONE.has(p.parse_status);
}

/** 주민등록번호 표시 한 줄 */
export function ResidentNumberNotice({ className }: { className?: string }) {
  return (
    <p className={cn("text-[13px]", className)}>
      <span className="font-semibold">주민등록번호가 보입니다.</span>{" "}
      <span className="text-muted">기록에는 옮기지 않습니다. 제출할 사본이면 가린 파일을 따로 올리세요.</span>
    </p>
  );
}

/** 자료 화면(용도 = 증빙)의 증빙 구역: 민감 정보 표시, 이어진 기록, 다운로드·항목 뽑기 */
export function ProofPanel({ sourceId }: { sourceId: string }) {
  const q = useQuery({
    queryKey: ["sources", "proofs", sourceId],
    queryFn: () => api.proofs(sourceId),
    select: (rows) => rows[0] ?? null,
    refetchInterval: (query) => {
      const st = query.state.data?.[0]?.parse_status;
      return !st || st === "pending" || st === "waiting_provider" ? 3000 : false;
    },
  });
  const download = useOriginalDownload();
  const extract = useExtractCredentials();
  const [adding, setAdding] = useState(false);
  const p = q.data;
  if (!p) return <ErrorText error={q.error} />;
  return (
    <section className="space-y-3">
      <h2 className="text-[15px] font-semibold">증빙</h2>
      {p.resident_number ? <ResidentNumberNotice /> : null}
      {p.credentials.length ? (
        <ul className="divide-y divide-line border-y border-line">
          {p.credentials.map((c) => (
            <li key={c.id}>
              <Link
                to="/brain/credentials"
                className="-mx-2 flex min-h-9 items-center gap-4 rounded-md px-2 py-3 text-[13px] transition-colors duration-150 hover:bg-subtle/60"
              >
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{c.name}</span>
                <span className="shrink-0 text-muted">{CREDENTIAL_KIND_LABEL[c.kind]}</span>
                <span className={cn("shrink-0", c.status === "extracted" ? "font-semibold" : "text-muted")}>
                  {CREDENTIAL_STATUS_LABEL[c.status]}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="border-t border-line pt-3 text-sm text-muted">
          {canExtract(p)
            ? "이어진 기록이 없습니다. 항목을 뽑거나 직접 추가합니다."
            : "읽은 글이 없어 항목을 뽑을 수 없습니다. 사진에서는 글자를 읽지 않습니다. 항목을 직접 추가합니다."}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => download.mutate({ id: p.id, filename: p.original_filename })} disabled={download.isPending}>
          <Download size={15} /> 다운로드
        </Button>
        {canExtract(p) ? (
          <Button onClick={() => extract.mutate(p.id)} disabled={extract.isPending}>
            항목 뽑기
          </Button>
        ) : null}
        <Button variant="ghost" onClick={() => setAdding(true)}>
          직접 추가
        </Button>
      </div>
      {extract.data ? (
        <p className="text-[13px] text-muted" role="status">
          {extractResultText(extract.data)}
        </p>
      ) : null}
      <p className="text-[13px] text-muted">항목 뽑기는 외부로 보내지 않고 정해진 규칙으로만 읽습니다. 모델 비용이 들지 않습니다.</p>
      <ErrorText error={download.error ?? extract.error} />
      <CredentialSheet open={adding} credential={null} defaultSourceId={p.id} onClose={() => setAdding(false)} />
    </section>
  );
}
