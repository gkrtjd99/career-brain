import { createClient } from "@career-brain/client";
import { Button, Input, Label } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorText } from "../components/common";
import { api, setToken, useToken } from "../lib/api";

const route = getRouteApi("/connect");
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** 토큰이 없거나 거절됐을 때 여는 첫 연결 화면. 토큰 관리 자체는 설정에 있다. */
export function ConnectPage() {
  const { reason, next } = route.useSearch();
  const token = useToken();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const health = useQuery({ queryKey: ["health"], queryFn: api.coreHealth, retry: false });

  // 저장 전에 새 토큰으로 /core/me 를 불러 확인한다.
  const check = useMutation({
    mutationFn: (value: string) => createClient({ baseUrl: BASE, getToken: () => value }).me(),
    onSuccess: () => {
      setToken(draft.trim());
      setDraft("");
      qc.clear();
      void navigate({ to: next ?? "/" });
    },
  });

  return (
    <div className="mx-auto max-w-md pt-[12vh]">
      <h1 className="text-xl leading-8 font-semibold">Career Brain에 연결</h1>
      <p className="mt-1 text-sm text-muted">
        {reason === "invalid"
          ? "저장된 토큰이 거절되었습니다. 새 토큰을 넣어 주세요."
          : "이 브라우저에 API 토큰을 한 번 넣으면 됩니다. 토큰은 이 브라우저에만 저장됩니다."}
      </p>

      <form
        className="mt-8 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) check.mutate(draft.trim());
        }}
      >
        <Label htmlFor="connect-token">API 토큰</Label>
        <Input
          id="connect-token"
          type="password"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="bootstrap 또는 demo가 출력한 토큰"
        />
        <ErrorText error={check.error} />
        <Button type="submit" variant="primary" className="w-full" disabled={!draft.trim() || check.isPending}>
          {check.isPending ? "확인 중" : "연결"}
        </Button>
      </form>

      <div className="mt-10 space-y-3 border-t border-line pt-4 text-[13px] leading-6 text-muted">
        <p>
          토큰이 없다면 서버에서 <code className="text-fg">uv run personal-api bootstrap</code>을 실행하세요. Docker는{" "}
          <code className="text-fg">docker compose run --rm app bootstrap</code>입니다.
        </p>
        <p>
          먼저 둘러보려면 <code className="text-fg">bootstrap</code> 대신 <code className="text-fg">demo</code>를
          실행하세요. 가상 자료로 채운 데모 공간과 그 토큰을 만듭니다. 모델 키는 필요 없습니다.
        </p>
        <p className="tabular-nums">
          서버{" "}
          {health.isSuccess
            ? `연결됨 · core ${health.data.core_version}`
            : health.isError
              ? "연결 안 됨. 서버가 켜져 있는지 확인하세요."
              : "확인 중"}
          {token && reason !== "invalid" ? (
            <>
              {" · "}
              <Link to="/" className="text-fg underline underline-offset-4">
                이미 연결됨, 오늘로
              </Link>
            </>
          ) : null}
        </p>
      </div>
    </div>
  );
}
