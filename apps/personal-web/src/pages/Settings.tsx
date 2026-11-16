import type { DocumentSettings } from "@career-brain/client";
import { Badge, Button, Card, Input, Label, Section, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { createClient } from "@career-brain/client";
import { ErrorText, PageHeader } from "../components/common";
import { TokenExpiryNotice } from "../components/TokenExpiryNotice";
import { TokensSection } from "../components/TokensSection";
import { WorkspaceSettingsSection } from "../components/settings/WorkspaceSettingsSection";
import { api, setToken, useToken } from "../lib/api";
import { useTheme, type ThemePref } from "../lib/theme";

const route = getRouteApi("/settings");
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export function SettingsPage() {
  const { reason } = route.useSearch();
  const token = useToken();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [theme, setTheme] = useTheme();

  const health = useQuery({ queryKey: ["health"], queryFn: api.coreHealth, retry: false });
  const me = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, retry: false });

  // 저장 전에 새 토큰으로 /core/me 를 불러 확인한다.
  const check = useMutation({
    mutationFn: (value: string) => createClient({ baseUrl: BASE, getToken: () => value }).me(),
    onSuccess: () => {
      setToken(draft);
      setDraft("");
      qc.clear();
      void navigate({ to: "/" });
    },
  });

  return (
    <div className="max-w-xl">
      <PageHeader title="설정" subtitle="API 연결과 화면 테마" />

      {reason === "missing" && !token ? (
        <p className="mb-4 rounded-lg border border-line bg-accent-soft px-3 py-2 text-[13px]">
          처음이라면 <code>uv run personal-api bootstrap</code> 이 출력한 API 토큰을 넣어 주세요.
        </p>
      ) : null}
      {reason === "invalid" ? (
        <p className="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          저장된 토큰이 거절되었습니다. 새 토큰을 넣어 주세요.
        </p>
      ) : null}

      <div className="space-y-8">
        <Section title="API 토큰">
          <Card className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-muted">서버</span>
              {health.isSuccess ? (
                <Badge tone="ok">연결됨 · core {health.data.core_version}</Badge>
              ) : health.isError ? (
                <Badge tone="danger">연결 안 됨</Badge>
              ) : (
                <Badge tone="none">확인 중</Badge>
              )}
              <span className="text-muted">토큰</span>
              {me.isSuccess ? (
                <Badge tone="ok">사용 중 · {me.data.workspace.name}</Badge>
              ) : token ? (
                <Badge tone={me.isError ? "danger" : "none"}>{me.isError ? "거절됨" : "확인 중"}</Badge>
              ) : (
                <Badge tone="warn">없음</Badge>
              )}
            </div>
            {me.data ? (
              <p className="text-xs text-muted">권한: {me.data.scopes.join(", ")}</p>
            ) : null}
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (draft.trim()) check.mutate(draft.trim());
              }}
            >
              <Label htmlFor="token">{token ? "토큰 바꾸기" : "토큰"}</Label>
              <Input
                id="token"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="personal-api bootstrap 이 출력한 토큰"
              />
              <ErrorText error={check.error} />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="primary" disabled={!draft.trim() || check.isPending}>
                  확인 후 저장
                </Button>
                {token ? (
                  <Button
                    variant="danger"
                    onClick={() => {
                      setToken(null);
                      qc.clear();
                    }}
                  >
                    이 브라우저에서 지우기
                  </Button>
                ) : null}
              </div>
            </form>
            <p className="text-xs leading-5 text-muted">
              토큰은 이 브라우저의 로컬 저장소에만 두고, 요청마다 <code>Authorization</code> 헤더로
              보냅니다. 공용 기기에서는 쓰고 나서 지워 주세요.
            </p>
          </Card>
        </Section>

        {me.data?.scopes.includes("workspace-admin") ? <TokensSection /> : null}
        {me.data && !me.data.scopes.includes("workspace-admin") ? <TokenExpiryNotice inSettings /> : null}

        {me.isSuccess ? <DocumentSettingsSection /> : null}
        {me.isSuccess ? <WorkspaceSettingsSection /> : null}

        <Section title="테마">
          <div className="inline-flex rounded-lg border border-line bg-surface p-0.5" role="radiogroup">
            {(
              [
                ["system", "기기 설정"],
                ["light", "밝게"],
                ["dark", "어둡게"],
              ] as [ThemePref, string][]
            ).map(([v, label]) => (
              <button
                key={v}
                role="radio"
                aria-checked={theme === v}
                onClick={() => setTheme(v)}
                className={
                  theme === v
                    ? "rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-fg"
                    : "rounded-md px-3 py-1.5 text-[13px] text-muted hover:text-fg"
                }
              >
                {label}
              </button>
            ))}
          </div>
        </Section>
      </div>
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
      <Card className="space-y-4 p-4">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor="cliches">상투어 목록 (한 줄에 하나)</Label>
            {d ? <Badge tone={d.customized ? "accent" : "neutral"}>{d.customized ? "직접 고친 목록" : "기본 목록"}</Badge> : null}
          </div>
          <Textarea
            id="cliches"
            value={cliches}
            className="min-h-40 font-mono text-[13px]"
            onChange={(e) => setCliches(e.target.value)}
            disabled={!d}
          />
          <p className="text-xs text-muted">
            문서 검사에서 "참고"로만 알려 줍니다. 300개, 한 줄 40자까지. 검사 요청에 따로 넣는 상투어는 이 목록에 더해집니다.
          </p>
          <ErrorText error={saveCliches.error} />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" disabled={!d || saveCliches.isPending} onClick={() => saveCliches.mutate(lines(cliches))}>
              상투어 저장
            </Button>
            {d?.customized ? (
              <Button size="sm" disabled={saveCliches.isPending} onClick={() => saveCliches.mutate(null)}>
                기본 목록으로 되돌리기
              </Button>
            ) : null}
          </div>
        </div>
        <div className="space-y-2 border-t border-line pt-4">
          <p className="text-[13px] font-medium">본인 연락처</p>
          <p className="text-xs text-muted">
            제출 전 재검사는 주민등록번호, 본인 것이 아닌 전화번호·이메일, 대외비 표시를 경고합니다. 여기 적은 내 연락처는 경고하지 않습니다.
            이 값은 이 작업 공간의 데이터베이스에만 저장합니다.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="own-emails">이메일 (한 줄에 하나)</Label>
              <Textarea id="own-emails" value={emails} className="min-h-20" onChange={(e) => setEmails(e.target.value)} disabled={!d} />
            </div>
            <div>
              <Label htmlFor="own-phones">전화번호 (한 줄에 하나)</Label>
              <Textarea id="own-phones" value={phones} className="min-h-20" onChange={(e) => setPhones(e.target.value)} disabled={!d} />
            </div>
          </div>
          <ErrorText error={saveContacts.error} />
          <Button variant="primary" size="sm" disabled={!d || saveContacts.isPending} onClick={() => saveContacts.mutate()}>
            연락처 저장
          </Button>
        </div>
      </Card>
    </Section>
  );
}
