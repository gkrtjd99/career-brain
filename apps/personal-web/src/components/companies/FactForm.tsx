import { COMPANY_FACT_KINDS, type CompanyFactKind } from "@career-brain/client";
import { Button, Input, Label, Select, SelectItem, Textarea } from "@career-brain/ui";
import { useEffect, useState, type FormEvent } from "react";
import { FACT_KIND_LABEL } from "../../lib/companies";
import { ErrorText } from "../common";

export interface FactFormValues {
  kind: CompanyFactKind;
  quote: string;
  body: string;
  as_of: string;
}

export const EMPTY_FACT: FactFormValues = { kind: "talent", quote: "", body: "", as_of: "" };

/** 항목 하나: 원문에서 그대로 복사한 인용과 그 내용을 줄인 한 줄. 기준일은 원문에 적힌 것만. */
export function FactForm({
  initial,
  quote,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial: FactFormValues;
  /** 원문에서 고른 글 (바뀌면 인용 칸을 채운다) */
  quote?: string;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (v: FactFormValues) => void;
  onCancel?: () => void;
}) {
  const [v, setV] = useState(initial);
  useEffect(() => {
    if (quote) setV((prev) => ({ ...prev, quote, body: prev.body || quote }));
  }, [quote]);
  const finance = v.kind === "finance";
  const ready = v.quote.trim() && v.body.trim() && (!finance || v.as_of.trim());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (ready) onSubmit(v);
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="fact-kind">종류</Label>
        <Select
          id="fact-kind"
          value={v.kind}
          onValueChange={(next) => setV({ ...v, kind: next as CompanyFactKind })}
        >
          {COMPANY_FACT_KINDS.map((k) => (
            <SelectItem key={k} value={k}>
              {FACT_KIND_LABEL[k]}
            </SelectItem>
          ))}
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="fact-quote">원문 인용</Label>
        <Textarea
          id="fact-quote"
          value={v.quote}
          onChange={(e) => setV({ ...v, quote: e.target.value })}
          maxLength={2000}
          rows={3}
          required
        />
        <p className="text-[13px] text-muted">원문에 있는 글만 저장합니다. 원문에서 그대로 복사해 넣습니다.</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="fact-body">내용</Label>
        <Textarea
          id="fact-body"
          value={v.body}
          onChange={(e) => setV({ ...v, body: e.target.value })}
          maxLength={2000}
          rows={2}
          required
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="fact-as-of">기준일{finance ? "" : " (선택)"}</Label>
        <Input
          id="fact-as-of"
          value={v.as_of}
          onChange={(e) => setV({ ...v, as_of: e.target.value })}
          placeholder="예: 2024 또는 2024-06-30"
          inputMode="numeric"
          className="tabular-nums"
          maxLength={10}
          pattern="\d{4}(-\d{1,2}(-\d{1,2})?)?"
          required={finance}
        />
        <p className="text-[13px] text-muted">
          {finance
            ? "재무 항목은 기준 연도나 날짜가 있어야 합니다. 원문에 적힌 대로 적습니다."
            : "원문에 적힌 날짜만 적습니다. 모르면 비워 둡니다."}
        </p>
      </div>
      <ErrorText error={error} />
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={pending || !ready}>
          {submitLabel}
        </Button>
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel}>
            취소
          </Button>
        ) : null}
      </div>
    </form>
  );
}
