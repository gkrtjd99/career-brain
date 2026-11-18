import { createClient, type Me } from "@career-brain/client";
import { Button, Input, Label, Section } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { ErrorText } from "../common";
import { TokenExpiryNotice } from "../TokenExpiryNotice";
import { TokensSection } from "../TokensSection";
import { api, setToken, useToken } from "../../lib/api";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 text-sm">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right break-words">{children}</dd>
    </div>
  );
}

/** 토큰 탭: 이 브라우저가 쓰는 토큰(바꾸기·지우기)과 공간의 API 토큰 목록(소유자만) */
export function TokensTab({ me }: { me: Me | undefined }) {
  const admin = !!me?.scopes.includes("workspace-admin");
  return (
    <div className="max-w-xl space-y-10">
      <BrowserTokenSection me={me} />
      {admin ? <TokensSection /> : null}
      {me && !admin ? <TokenExpiryNotice inSettings /> : null}
    </div>
  );
}

function BrowserTokenSection({ me }: { me: Me | undefined }) {
  const token = useToken();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const health = useQuery({ queryKey: ["health"], queryFn: api.coreHealth, retry: false });

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
    <Section title="이 브라우저의 토큰">
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
          {me ? (
            <>
              사용 중 <span className="text-muted">· {me.workspace.name}</span>
            </>
          ) : token ? (
            <span className="text-muted">확인 중</span>
          ) : (
            <span className="font-medium">없음</span>
          )}
        </Row>
        {me ? (
          <Row label="권한">
            <span className="text-[13px] text-muted">{me.scopes.join(", ")}</span>
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
        <Label htmlFor="token">토큰 바꾸기</Label>
        <Input
          id="token"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="cbp_로 시작하는 토큰"
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
                void navigate({ to: "/connect" });
              }}
            >
              이 브라우저에서 삭제
            </Button>
          ) : null}
        </div>
      </form>
      <p className="text-[13px] leading-5 text-muted">
        토큰은 이 브라우저의 로컬 저장소에만 두고, 요청마다 <code>Authorization</code> 헤더로 보냅니다. 공용 기기에서는
        쓰고 나서 지워 주세요.
      </p>
    </Section>
  );
}
