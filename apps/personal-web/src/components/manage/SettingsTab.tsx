import type { DocumentSettings } from "@career-brain/client";
import { Button, cn, Label, Section, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ErrorText } from "../common";
import { CaptureSection } from "./CaptureSection";
import { WorkspaceSettingsSection } from "../settings/WorkspaceSettingsSection";
import { JudgeCacheSection } from "../settings/JudgeCacheSection";
import { api } from "../../lib/api";
import { useTheme, type ThemePref } from "../../lib/theme";

/** 설정 탭: 테마, 브라우저에서 보내기(북마크), 문서 검사, 작업 공간(표시 시간대·기술명 별칭), 판정 캐시 */
export function SettingsTab({ scopes }: { scopes: string[] }) {
  const [theme, setTheme] = useTheme();
  return (
    <div className="max-w-xl space-y-10">
      <Section title="테마">
        <div className="inline-flex rounded-md border border-line p-0.5" role="radiogroup" aria-label="테마">
          {(
            [
              ["system", "기기 설정"],
              ["light", "밝게"],
              ["dark", "어둡게"],
            ] as [ThemePref, string][]
          ).map(([v, label]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={theme === v}
              onClick={() => setTheme(v)}
              className={cn(
                "h-9 rounded px-3 text-[13px] transition-colors duration-150",
                theme === v ? "bg-accent font-medium text-accent-fg" : "text-muted hover:text-fg",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </Section>
      <CaptureSection />
      <WorkspaceSettingsSection />
      <DocumentSettingsSection />
      {scopes.includes("egress") ? <JudgeCacheSection /> : null}
    </div>
  );
}

const lines = (v: string) =>
  v
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

/** 문서 검사 설정: 상투어 목록(공간 설정)과 본인 연락처(제출 전 민감 정보 검사에서 빼는 값) */
function DocumentSettingsSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["document-settings"], queryFn: api.documentSettings });
  const [cliches, setCliches] = useState("");
  const [emails, setEmails] = useState("");
  const [phones, setPhones] = useState("");
  const fill = (d: DocumentSettings) => {
    setCliches(d.cliches.join("\n"));
    setEmails(d.own_contacts.emails.join("\n"));
    setPhones(d.own_contacts.phones.join("\n"));
  };
  useEffect(() => {
    if (q.data) fill(q.data);
  }, [q.data]);
  const done = (d: DocumentSettings) => {
    qc.setQueryData(["document-settings"], d);
    // 검사 결과가 상투어 목록에 따라 바뀐다
    qc.invalidateQueries({ queryKey: ["validate"] });
    qc.invalidateQueries({ queryKey: ["sensitive-check"] });
  };
  const saveCliches = useMutation({
    mutationFn: (value: string[] | null) => api.saveDocumentSettings({ cliches: value }),
    onSuccess: done,
  });
  const saveContacts = useMutation({
    mutationFn: () => api.saveDocumentSettings({ own_contacts: { emails: lines(emails), phones: lines(phones) } }),
    onSuccess: done,
  });
  const d = q.data;
  return (
    <Section title="문서 검사">
      <div className="space-y-8 border-t border-line pt-4">
        <div className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <Label htmlFor="cliches">상투어 목록 (한 줄에 하나)</Label>
            {d ? <span className="text-[13px] text-muted">{d.customized ? "직접 수정한 목록" : "기본 목록"}</span> : null}
          </div>
          <Textarea
            id="cliches"
            value={cliches}
            className="min-h-40 font-mono text-[13px]"
            onChange={(e) => setCliches(e.target.value)}
            disabled={!d}
          />
          <p className="text-[13px] text-muted">
            문서 검사에서 "참고"로만 알려 줍니다. 300개, 한 줄 40자까지. 검사 요청에 따로 넣는 상투어는 이 목록에
            더해집니다.
          </p>
          <ErrorText error={saveCliches.error} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={!d || saveCliches.isPending}
              onClick={() => saveCliches.mutate(lines(cliches))}
            >
              상투어 저장
            </Button>
            {d?.customized ? (
              <Button size="sm" disabled={saveCliches.isPending} onClick={() => saveCliches.mutate(null)}>
                기본 목록으로 되돌리기
              </Button>
            ) : null}
          </div>
        </div>
        <div className="space-y-2">
          <h3 className="text-sm font-medium">본인 연락처</h3>
          <p className="text-[13px] text-muted">
            제출 전 재검사는 주민등록번호, 본인 것이 아닌 전화번호·이메일, 대외비 표시를 경고합니다. 여기 적은 내
            연락처는 경고하지 않습니다. 이 값은 이 작업 공간의 데이터베이스에만 저장합니다.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="own-emails">이메일 (한 줄에 하나)</Label>
              <Textarea
                id="own-emails"
                value={emails}
                className="min-h-20"
                onChange={(e) => setEmails(e.target.value)}
                disabled={!d}
              />
            </div>
            <div>
              <Label htmlFor="own-phones">전화번호 (한 줄에 하나)</Label>
              <Textarea
                id="own-phones"
                value={phones}
                className="min-h-20"
                onChange={(e) => setPhones(e.target.value)}
                disabled={!d}
              />
            </div>
          </div>
          <ErrorText error={saveContacts.error} />
          <Button
            variant="primary"
            size="sm"
            disabled={!d || saveContacts.isPending}
            onClick={() => saveContacts.mutate()}
          >
            연락처 저장
          </Button>
        </div>
      </div>
    </Section>
  );
}
