export type VerificationStatus =
  | "extracted"
  | "self_confirmed"
  | "evidence_supported"
  | "disputed"
  | "retired";

const LABEL: Record<VerificationStatus, string> = {
  extracted: "미확인",
  self_confirmed: "본인 확인",
  evidence_supported: "자료 확인",
  disputed: "이견 있음",
  retired: "보관됨",
};

export interface EvidenceCardProps {
  title: string;
  status: VerificationStatus;
  sourceLabel?: string;
}

export function EvidenceCard({ title, status, sourceLabel }: EvidenceCardProps) {
  return (
    <article data-status={status} style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12 }}>
      <strong>{title}</strong>
      <div style={{ fontSize: 12, opacity: 0.7 }}>
        {LABEL[status]}
        {sourceLabel ? ` · ${sourceLabel}` : null}
      </div>
    </article>
  );
}
