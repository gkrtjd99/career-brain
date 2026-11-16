import type { ExportCheck, ExportFile, ExportFormat, FileCheck, Finding } from "@career-brain/client";
import { Badge, Button, Label, Select, Sheet } from "@career-brain/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileSearch } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { int } from "../../lib/format";
import { EXPORT_FORMAT, FINDING, FINDING_GROUP } from "../../lib/labels";
import { ErrorText, Loaded } from "../common";

export const EXPORT_FORMATS: ExportFormat[] = ["md", "docx", "html"];

/** 받은 파일을 저장한다 */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * PDF: 서버가 만든 인쇄용 HTML(스크립트 없음)을 새 창에 열고 인쇄 대화상자를 띄운다.
 * 한글 글꼴을 서버에 싣지 않으려고 브라우저의 "PDF로 저장"을 쓴다 (applications.md "서버 PDF").
 * 창을 못 열면 HTML 파일로 받는다.
 */
export function printHtml(blob: Blob, filename: string) {
  const url = URL.createObjectURL(new Blob([blob], { type: "text/html;charset=utf-8" }));
  const w = window.open(url, "_blank");
  if (!w) {
    URL.revokeObjectURL(url);
    saveBlob(blob, filename);
    return;
  }
  w.addEventListener("load", () => {
    w.focus();
    w.print();
  });
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function FindingItem({ f }: { f: Finding }) {
  const info = FINDING[f.code];
  const tone = info ? FINDING_GROUP[info.group].tone : "neutral";
  const samples = (f.detail?.samples as { page?: number; part?: string; text?: string | null }[] | undefined) ?? [];
  const count = typeof f.detail?.count === "number" ? (f.detail.count as number) : null;
  return (
    <li className="rounded-md border border-line bg-surface px-2.5 py-2 text-[13px]">
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone={tone}>{info?.label ?? f.code}</Badge>
        {count !== null ? <span className="text-xs text-muted">{count}곳</span> : null}
        {f.detail?.in_hidden_text ? <Badge tone="danger">숨긴 글 안</Badge> : null}
      </span>
      <p className="mt-1 leading-5">{f.message}</p>
      {f.sentence ? <p className="mt-1 text-xs break-words text-muted">“{f.sentence}”</p> : null}
      {samples.length ? (
        <ul className="mt-1 space-y-0.5 text-xs text-muted">
          {samples.map((s, i) => (
            <li key={i} className="break-words">
              {s.page ? `${s.page}쪽 · ` : ""}
              {s.text ? `“${s.text}”` : "(글을 읽지 못함)"}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function FindingList({ findings }: { findings: Finding[] }) {
  return (
    <ul className="space-y-1.5">
      {findings.map((f, i) => (
        <FindingItem key={`${f.code}-${i}`} f={f} />
      ))}
    </ul>
  );
}

export function ExportFindings({ check, label }: { check: ExportCheck; label?: string }) {
  const head = label ?? `r${check.revision}`;
  if (!check.findings.length)
    return (
      <p className="rounded-md border border-line bg-surface px-3 py-2 text-[13px]">
        {head} · {check.filename} · {int(check.bytes)} byte · 경고 없음
      </p>
    );
  return (
    <div className="space-y-1.5">
      <p className="text-[13px]">
        {head} · {check.filename} · 경고 {check.findings.length}개
      </p>
      <FindingList findings={check.findings} />
    </div>
  );
}

/**
 * 내보내기 시트: 서버가 만든 파일을 다시 읽어 검사한 결과(숨김 글·조작 문구·민감 정보)를 보이고,
 * 경고가 있으면 확인을 받은 뒤 받는다. 문서 revision 과 제출본이 같이 쓴다.
 */
export function ExportSheet({
  open,
  onClose,
  title,
  description,
  queryKey,
  check,
  download,
  label,
  note,
  enabled = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description: string;
  queryKey: readonly unknown[];
  check: (format: ExportFormat) => Promise<ExportCheck>;
  download: (format: ExportFormat, check: ExportCheck) => Promise<ExportFile>;
  label?: string;
  note?: ReactNode;
  enabled?: boolean;
}) {
  const [format, setFormat] = useState<ExportFormat>("docx");
  const [confirmed, setConfirmed] = useState(false);
  const q = useQuery({
    queryKey: [...queryKey, format],
    queryFn: () => check(format),
    enabled: open && enabled,
    staleTime: Infinity,
  });
  useEffect(() => setConfirmed(false), [format, open, q.data]);
  const get = useMutation({
    mutationFn: () => download(format, q.data!),
    onSuccess: (f) => {
      if (format === "html") printHtml(f.blob, f.filename);
      else saveBlob(f.blob, f.filename);
    },
  });
  const warnings = q.data?.findings.length ?? 0;
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title={title} description={description}>
      <div className="space-y-3">
        <div>
          <Label htmlFor="ex-format">형식</Label>
          <Select id="ex-format" value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)}>
            {EXPORT_FORMATS.map((f) => (
              <option key={f} value={f}>
                {EXPORT_FORMAT[f]}
              </option>
            ))}
          </Select>
          {format === "html" ? (
            <p className="mt-1 text-xs text-muted">인쇄 화면을 새 창에 엽니다. 인쇄 대화상자에서 "PDF로 저장"을 고르세요.</p>
          ) : null}
        </div>
        {note}
        <Loaded query={q}>{(c) => <ExportFindings check={c} label={label} />}</Loaded>
        {warnings ? (
          <label className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-1" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            <span>경고 {warnings}개를 확인했고 이대로 내보냅니다.</span>
          </label>
        ) : null}
        <ErrorText error={get.error} />
        <Button
          variant="primary"
          onClick={() => get.mutate()}
          disabled={!q.data || get.isPending || (warnings > 0 && !confirmed)}
        >
          <Download size={14} /> {format === "html" ? "인쇄 화면 열기" : "파일 받기"}
        </Button>
      </div>
    </Sheet>
  );
}

/**
 * 제출할 DOCX·PDF(다른 도구로 만든 이력서·포트폴리오)의 숨김 글 검사. 파일을 저장하지 않고 경고만 한다.
 */
export function HiddenTextCheck() {
  const [file, setFile] = useState<File | null>(null);
  const run = useMutation({ mutationFn: (f: File) => api.checkHiddenText(f) });
  const res: FileCheck | undefined = run.data;
  return (
    <div className="space-y-2 rounded-lg border border-line bg-surface p-3">
      <p className="flex items-center gap-1.5 text-[13px] font-medium">
        <FileSearch size={14} /> 제출할 파일 숨김 글 검사
      </p>
      <p className="text-xs text-muted">
        다른 도구로 만든 DOCX·PDF에 숨김 서식·흰 글씨·아주 작은 글자·쪽 밖 글·심사 프로그램에 지시하는 문장이 있는지 봅니다.
        파일은 저장하지 않고, 고치지 않습니다.
      </p>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (file) run.mutate(file);
        }}
      >
        <input
          type="file"
          aria-label="검사할 DOCX·PDF 파일"
          accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="max-w-full text-[13px]"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            run.reset();
          }}
        />
        <Button type="submit" size="sm" disabled={!file || run.isPending}>
          {run.isPending ? "검사하는 중" : "검사"}
        </Button>
      </form>
      <ErrorText error={run.error} />
      {res ? (
        res.findings.length ? (
          <div className="space-y-1.5">
            <p className="text-[13px]">
              {res.filename ?? "파일"} · {res.format.toUpperCase()}
              {res.pages ? ` · ${res.pages}쪽` : ""} · 경고 {res.findings.length}개
            </p>
            <FindingList findings={res.findings} />
          </div>
        ) : (
          <p className="rounded-md border border-line px-3 py-2 text-[13px]">
            {res.filename ?? "파일"} · {res.format.toUpperCase()}
            {res.pages ? ` · ${res.pages}쪽` : ""} · 숨김 글 경고 없음
          </p>
        )
      ) : null}
    </div>
  );
}
