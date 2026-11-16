import type { SharedDocumentKind, SharedDocumentSummary } from "@career-brain/client";
import { Badge, Button, EmptyState, Input, Label, Select, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, FileText, Plus } from "lucide-react";
import { useState } from "react";
import { ErrorText, List, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { DOC_KIND } from "../lib/labels";
import { countLine } from "./Application";

const KINDS: SharedDocumentKind[] = ["resume", "portfolio", "cover_letter", "other"];

/**
 * 공용 문서: 지원에 묶이지 않은 기본 이력서·포트폴리오. 지원 화면에서 "공용 문서에서 복사"로 가져간다.
 * 복사본은 출처(문서·revision)를 기억하고, 이후 원본을 고쳐도 복사본은 바뀌지 않는다.
 */
export function SharedDocumentsPage() {
  const q = useQuery({ queryKey: ["shared-documents"], queryFn: api.sharedDocuments });
  const [adding, setAdding] = useState(false);
  return (
    <div className="max-w-3xl">
      <PageHeader
        title="공용 문서"
        subtitle="지원마다 복사해 쓰는 기본 이력서·포트폴리오. 편집·근거·검사 규칙은 지원 문서와 같습니다."
        actions={
          <Button size="sm" variant="primary" onClick={() => setAdding(true)}>
            <Plus size={14} /> 공용 문서
          </Button>
        }
      />
      <Loaded query={q}>
        {(docs) =>
          docs.length ? (
            <List>
              {docs.map((d) => (
                <SharedRow key={d.id} d={d} />
              ))}
            </List>
          ) : (
            <EmptyState>공용 문서가 없습니다. 기본 이력서나 포트폴리오를 한 번 써 두면 지원마다 복사할 수 있습니다.</EmptyState>
          )
        }
      </Loaded>
      <AddSharedSheet open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

function SharedRow({ d }: { d: SharedDocumentSummary }) {
  return (
    <li>
      <Link
        to="/documents/$documentId"
        params={{ documentId: d.id }}
        className="flex items-start gap-3 px-3 py-3 hover:bg-subtle/60"
      >
        <FileText size={16} className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{d.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted">
            <span>{DOC_KIND[d.kind] ?? d.kind}</span>
            <span>· {countLine(d.counts)}</span>
            {d.current_revision ? <span>· r{d.current_revision}</span> : null}
            <span>· {formatDateTime(d.updated_at)}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {d.current_revision === 0 ? <Badge tone="warn">안 씀</Badge> : null}
            {d.copies ? <Badge tone="neutral">지원 {d.copies}곳에 복사</Badge> : null}
          </div>
        </div>
        <ChevronRight size={16} className="mt-0.5 shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function AddSharedSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ kind: "resume" as SharedDocumentKind, title: "" });
  const add = useMutation({
    mutationFn: () => api.createSharedDocument({ kind: form.kind, title: form.title.trim() || DOC_KIND[form.kind] }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["shared-documents"] });
      setForm({ kind: "resume", title: "" });
      onClose();
      void navigate({ to: "/documents/$documentId", params: { documentId: res.id } });
    },
  });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="공용 문서 추가" description="공고 문항은 지원마다 다르므로 공용 문서로 만들지 않습니다.">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <div>
          <Label htmlFor="sd-kind">종류</Label>
          <Select id="sd-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as SharedDocumentKind })}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {DOC_KIND[k]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="sd-title">제목</Label>
          <Input
            id="sd-title"
            value={form.title}
            maxLength={300}
            placeholder={DOC_KIND[form.kind]}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </div>
        <ErrorText error={add.error} />
        <Button type="submit" variant="primary" disabled={add.isPending}>
          만들기
        </Button>
      </form>
    </Sheet>
  );
}
