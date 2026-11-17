import type { DocumentSettings } from "@career-brain/client";
import { Button, cn, Input, Label, Section, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { createClient } from "@career-brain/client";
import { ErrorText, PageHeader } from "../components/common";
import { TokenExpiryNotice } from "../components/TokenExpiryNotice";
import { TokensSection } from "../components/TokensSection";
import { WorkspaceSettingsSection } from "../components/settings/WorkspaceSettingsSection";
import { JudgeCacheSection } from "../components/settings/JudgeCacheSection";
import { api, setToken, useToken } from "../lib/api";
import { useTheme, type ThemePref } from "../lib/theme";

const route = getRouteApi("/settings");
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** 라벨·값 한 줄 (얇은 선으로 나눈 설정 목록) */
function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 text-sm">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right break-words">{children}</dd>
    </div>
  );
}

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
        <div className="mb-8 space-y-1 text-[13px]">
          <p>
            처음이라면 <code>uv run personal-api bootstrap</code> (Docker:{" "}
            <code>docker compose run --rm app bootstrap</code>) 이 출력한 API 토큰을 넣어 주세요.
          </p>
          <p className="text-muted">
            먼저 둘러보려면 <code>bootstrap</code> 대신 <code>demo</code>를 실행하세요. 가상 자료로 채운 데모 공간과 그
            토큰을 만듭니다 (모델 키 필요 없음).
          </p>
        </div>
      ) : null}
      {reason === "invalid" ? (
        <p role="alert" className="mb-8 rounded-md bg-danger-soft px-3 py-2 text-[13px] text-danger">
          저장된 토큰이 거절되었습니다. 새 토큰을 넣어 주세요.
        </p>
      ) : null}

      <div className="space-y-10">
        <Section title="API 토큰">
          <dl className="divide-y divide-line border-y border-line">
            <Row label="서버">
              {health.isSuccess ? (
                <>
                  연결됨 <span className="text-muted tabular-nums">· core {health.data.core_version}</span>
                </>
              ) : health.isError ? (
                <span className="font-medium text-danger">연결 안 됨</span>
              ) : (
                <span className="text-muted">확인 중</span>
              )}
            </Row>
            <Row label="토큰">
              {me.isSuccess ? (
                <>
                  사용 중 <span className="text-muted">· {me.data.workspace.name}</span>
                </>
              ) : token ? (
                me.isError ? (
                  <span className="font-medium text-danger">거절됨</span>
                ) : (
                  <span className="text-muted">확인 중</span>
                )
              ) : (
                <span className="font-medium">없음</span>
              )}
            </Row>
            {me.data ? (
              <Row label="권한">
                <span className="text-[13px] text-muted">{me.data.scopes.join(", ")}</span>
              </Row>
            ) : null}
          </dl>
          <form
            className="space-y-2 pt-4"
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
              placeholder="personal-api bootstrap (또는 demo) 이 출력한 토큰"
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
          <p className="text-[13px] leading-5 text-muted">
            토큰은 이 브라우저의 로컬 저장소에만 두고, 요청마다 <code>Authorization</code> 헤더로 보냅니다. 공용
            기기에서는 쓰고 나서 지워 주세요.
          </p>
        </Section>

        {me.data?.scopes.includes("workspace-admin") ? <TokensSection /> : null}
        {me.data && !me.data.scopes.includes("workspace-admin") ? <TokenExpiryNotice inSettings /> : null}

        {me.isSuccess ? <DocumentSettingsSection /> : null}
        {me.isSuccess ? <WorkspaceSettingsSection /> : null}
        {me.data?.scopes.includes("egress") ? <JudgeCacheSection /> : null}

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
      <div className="space-y-8 border-t border-line pt-4">
        <div className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <Label htmlFor="cliches">상투어 목록 (한 줄에 하나)</Label>
            {d ? <span className="text-[13px] text-muted">{d.customized ? "직접 고친 목록" : "기본 목록"}</span> : null}
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
