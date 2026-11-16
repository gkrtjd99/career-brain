import { Badge, Button, Card, Input, Label, Section } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { createClient } from "@career-brain/client";
import { ErrorText, PageHeader } from "../components/common";
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
