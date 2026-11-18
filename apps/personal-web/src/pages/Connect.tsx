import { createClient } from "@career-brain/client";
import { Button, Input, Label } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ErrorText } from "../components/common";
import { api, setToken, useToken } from "../lib/api";

const route = getRouteApi("/connect");
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** 이 컴퓨터에서 띄운 서버면 토큰 없이 바로 연다 (서버가 이 컴퓨터·같은 화면의 요청만 받는다).
 *  부를 때마다 토큰이 하나 생기므로 한 화면에서 한 번만 부른다 (개발 모드의 effect 두 번 포함). */
let pendingLocal: Promise<string | null> | null = null;
function localSession(): Promise<string | null> {
  pendingLocal ??= requestLocalSession().finally(() => {
    pendingLocal = null;
  });
  return pendingLocal;
}

async function requestLocalSession(): Promise<string | null> {
  try {
    const r = await fetch(`${BASE}/local/session`, {
      method: "POST",
      headers: { "X-Career-Local": "1" },
      credentials: "same-origin",
    });
    if (!r.ok) return null;
    const body = (await r.json()) as { token?: string };
    return body.token ?? null;
  } catch {
    return null;
  }
}

/** 토큰이 없거나 거절됐을 때 여는 첫 연결 화면. 이 컴퓨터면 바로 열고, 다른 기기만 토큰을 넣는다. */
export function ConnectPage() {
  const { reason, next } = route.useSearch();
  const token = useToken();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  // 이미 토큰이 있고 거절되지도 않았으면 새로 받지 않는다
  const [local, setLocal] = useState<"trying" | "no">(token && reason !== "invalid" ? "no" : "trying");
  const health = useQuery({ queryKey: ["health"], queryFn: api.coreHealth, retry: false });

  useEffect(() => {
    if (local !== "trying") return;
    let live = true;
    void localSession().then((t) => {
      if (!live) return;
      if (!t) return setLocal("no");
      setToken(t);
      qc.clear();
      void navigate({ to: next ?? "/", replace: true });
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 화면을 열 때 한 번
  }, []);

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

  if (local === "trying")
    return <p className="mx-auto max-w-md pt-[12vh] text-sm text-muted">여는 중…</p>;

  return (
    <div className="mx-auto max-w-md pt-[12vh]">
      <h1 className="text-xl leading-8 font-semibold">Career Brain에 연결</h1>
      <p className="mt-1 text-sm text-muted">
        {reason === "invalid"
          ? "저장된 토큰이 거절되었습니다. 새 토큰을 넣어 주세요."
          : "다른 기기에서 열 때는 토큰을 한 번 넣습니다. 토큰은 이 브라우저에만 저장됩니다."}
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
          placeholder="cbp_로 시작하는 토큰"
        />
        <ErrorText error={check.error} />
        <Button type="submit" variant="primary" className="w-full" disabled={!draft.trim() || check.isPending}>
          {check.isPending ? "확인 중" : "연결"}
        </Button>
      </form>

      <div className="mt-10 space-y-3 border-t border-line pt-4 text-[13px] leading-6 text-muted">
        <p>
          서버를 띄운 컴퓨터에서 열면 토큰 없이 바로 열립니다. 다른 기기용 토큰은 그 컴퓨터의 설정 → 연결·관리 →
          토큰에서 만듭니다.
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
