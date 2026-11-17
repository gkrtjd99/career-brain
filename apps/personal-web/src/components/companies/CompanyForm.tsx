import type { Company, CompanyIn } from "@career-brain/client";
import { Button, Disclosure, Input, Label } from "@career-brain/ui";
import { useState, type FormEvent } from "react";
import { ErrorText } from "../common";

export interface CompanyFormValues {
  name: string;
  aliases: string;
  homepage: string;
  corp_code: string;
  business_no: string;
}

export const EMPTY_COMPANY: CompanyFormValues = { name: "", aliases: "", homepage: "", corp_code: "", business_no: "" };

export function companyValues(c: Company): CompanyFormValues {
  return {
    name: c.name,
    aliases: c.aliases.join(", "),
    homepage: c.homepage ?? "",
    corp_code: c.corp_code ?? "",
    business_no: c.business_no ?? "",
  };
}

export function toCompanyBody(v: CompanyFormValues): CompanyIn {
  return {
    name: v.name.trim(),
    aliases: v.aliases
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean),
    homepage: v.homepage.trim() || null,
    corp_code: v.corp_code.trim() || null,
    business_no: v.business_no.trim() || null,
  };
}

/** 회사 추가·수정. 식별자는 선택이라 접어 둔다. */
export function CompanyForm({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial: CompanyFormValues;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (v: CompanyFormValues) => void;
  onCancel: () => void;
}) {
  const [v, setV] = useState(initial);
  const set = (k: keyof CompanyFormValues) => (e: { target: { value: string } }) =>
    setV((prev) => ({ ...prev, [k]: e.target.value }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (v.name.trim()) onSubmit(v);
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="company-name">회사 이름</Label>
        <Input id="company-name" value={v.name} onChange={set("name")} maxLength={200} required autoFocus />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="company-aliases">다른 이름</Label>
        <Input
          id="company-aliases"
          value={v.aliases}
          onChange={set("aliases")}
          placeholder="예: 영문 이름, 줄임말"
          maxLength={2000}
        />
        <p className="text-[13px] text-muted">쉼표로 나눕니다. 공고의 회사 이름이 이 중 하나와 같으면 연결을 제안합니다.</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="company-homepage">홈페이지</Label>
        <Input
          id="company-homepage"
          value={v.homepage}
          onChange={set("homepage")}
          inputMode="url"
          placeholder="https://"
          maxLength={500}
        />
      </div>
      <Disclosure title="식별 번호 (선택)">
        <div className="mt-2 grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="company-corp">DART 고유번호</Label>
            <Input
              id="company-corp"
              value={v.corp_code}
              onChange={set("corp_code")}
              inputMode="numeric"
              placeholder="숫자 8자리"
              className="tabular-nums"
              maxLength={20}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="company-bizno">사업자등록번호</Label>
            <Input
              id="company-bizno"
              value={v.business_no}
              onChange={set("business_no")}
              inputMode="numeric"
              placeholder="숫자 10자리"
              className="tabular-nums"
              maxLength={20}
            />
          </div>
        </div>
      </Disclosure>
      <ErrorText error={error} />
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={pending || !v.name.trim()}>
          {submitLabel}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </div>
    </form>
  );
}
