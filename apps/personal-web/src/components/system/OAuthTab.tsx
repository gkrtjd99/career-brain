import type { OAuthLink, OAuthToken } from "@career-brain/client";
import { Badge, Button, Card, ConfirmDialog, EmptyState, Input, Label, Section, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorText, List, Loaded } from "../common";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/dates";
import { int } from "../../lib/format";

// 원격 MCP OAuth 연결·토큰 관리 (소유자만). 연결 = 외부 인가 서버의 사용자(issuer, sub) → 이 공간의 나.
// 토큰 목록은 MCP가 받은 액세스 토큰의 jti(없으면 sha256)이고 원문은 없다.

const OAUTH_ERROR: Record<string, string> = {
  forbidden: "공간 소유자만 할 수 있습니다.",
  subject_already_linked: "그 사용자는 다른 공간에 이미 연결돼 있습니다.",
  issuer_required: "인가 서버(issuer)를 적어 주세요. 서버에 CAREER_OAUTH_ISSUER가 없습니다.",
  invalid_subject: "사용자 ID(sub)가 비었거나 너무 깁니다.",
  invalid_issuer: "issuer가 비었거나 너무 깁니다.",
};

function oauthError(error: unknown) {
  const code = (error as { code?: string } | null)?.code;
  return code && OAUTH_ERROR[code] ? new Error(OAUTH_ERROR[code]) : error;
}

type Pending = { kind: "link"; item: OAuthLink } | { kind: "token"; item: OAuthToken } | null;

export function OAuthTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["oauth"], queryFn: () => api.oauth(100) });
  const [subject, setSubject] = useState("");
  const [label, setLabel] = useState("");
  const [issuer, setIssuer] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["oauth"] });
  const link = useMutation({
    mutationFn: () =>
      api.linkOAuth({ subject: subject.trim(), label: label.trim() || undefined, issuer: issuer.trim() || undefined }),
    onSuccess: () => {
      setSubject("");
      setLabel("");
      refresh();
    },
  });
  const revoke = useMutation({
    mutationFn: (p: NonNullable<Pending>) =>
      p.kind === "link" ? api.revokeOAuthLink(p.item.id) : api.revokeOAuthToken(p.item.id),
    onSuccess: () => {
      setPending(null);
      refresh();
    },
  });
  return (
    <Loaded query={q}>
      {(o) => {
        const subjects = new Map(o.links.map((l) => [l.id, l.label || l.subject]));
        return (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Section title="설정">
              <Card className="space-y-1 px-3 py-2 text-[13px]">
                {o.config.enabled ? (
                  <>
                    <p>
                      <span className="text-muted">인가 서버: </span>
                      <span className="break-all">{o.config.issuer}</span>
                    </p>
                    <p>
                      <span className="text-muted">자원(MCP 주소): </span>
                      <span className="break-all">{o.config.resource}</span>
                    </p>
                    <p className="text-muted">
                      확인 방법: {[o.config.jwks ? "JWT 서명(JWKS)" : null, o.config.introspection ? "introspection" : null].filter(Boolean).join(" · ")}
                      {o.config.require_jti ? " · jti 필수" : ""}
                    </p>
                  </>
                ) : (
                  <p className="text-muted">
                    외부 OAuth가 꺼져 있습니다 (CAREER_OAUTH_ISSUER 없음). 원격 MCP는 API 토큰으로만 접속합니다. 연결은 미리 만들어 둘 수
                    있습니다.
                  </p>
                )}
              </Card>
            </Section>

            <Section title="연결 추가">
              <div className="space-y-2">
                <Label htmlFor="oauth-subject">인가 서버의 사용자 ID (sub)</Label>
                <Input
                  id="oauth-subject"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  maxLength={500}
                  spellCheck={false}
                  autoComplete="off"
                  className="font-mono text-xs"
                />
                <Label htmlFor="oauth-label">이름 (선택)</Label>
                <Input id="oauth-label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={100} placeholder="예: 노트북 Claude" />
                {!o.config.enabled ? (
                  <>
                    <Label htmlFor="oauth-issuer">인가 서버 (issuer)</Label>
                    <Input
                      id="oauth-issuer"
                      value={issuer}
                      onChange={(e) => setIssuer(e.target.value)}
                      maxLength={500}
                      placeholder="https://auth.example.com/realms/career"
                      spellCheck={false}
                      className="font-mono text-xs"
                    />
                  </>
                ) : null}
                <Button variant="primary" disabled={!subject.trim() || link.isPending} onClick={() => link.mutate()}>
                  {link.isPending ? <Spinner /> : null}
                  연결
                </Button>
                <ErrorText error={link.error ? oauthError(link.error) : null} />
                <p className="text-xs text-muted">
                  연결한 사용자의 토큰은 이 공간에서 내 역할 범위와 토큰 범위의 교집합으로만 쓰이고, 승인·관리 권한은 받지 않습니다.
                </p>
              </div>
            </Section>

            <Section title="연결">
              {o.links.length ? (
                <List>
                  {o.links.map((l) => (
                    <li key={l.id} className="flex items-center gap-3 px-3 py-2 text-[13px]">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{l.label}</div>
                        <div className="truncate font-mono text-xs text-muted">{l.subject}</div>
                        <div className="truncate text-xs text-muted">
                          {formatDateTime(l.created_at)}
                          {l.revoked_at ? ` · 끊음 ${formatDateTime(l.revoked_at)}` : ""}
                        </div>
                      </div>
                      {l.revoked_at ? (
                        <Badge tone="none">끊음</Badge>
                      ) : (
                        <Button size="sm" variant="danger" onClick={() => setPending({ kind: "link", item: l })}>
                          끊기
                        </Button>
                      )}
                    </li>
                  ))}
                </List>
              ) : (
                <EmptyState>연결이 없습니다.</EmptyState>
              )}
            </Section>

            <Section title="최근 토큰">
              {o.tokens.length ? (
                <List>
                  {o.tokens.map((t) => (
                    <li key={t.id} className="flex items-center gap-3 px-3 py-2 text-[13px]">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-xs">
                          {t.ref_kind === "jti" ? t.token_ref : `sha256 ${t.token_ref.slice(0, 12)}…`}
                        </div>
                        <div className="truncate text-xs text-muted tabular-nums">
                          {subjects.get(t.identity_id) ?? t.subject} · {t.via === "jwt" ? "JWT" : "introspection"} · {int(t.uses)}회 ·
                          마지막 {formatDateTime(t.last_seen_at)}
                          {t.expires_at ? ` · 만료 ${formatDateTime(t.expires_at)}` : ""}
                        </div>
                      </div>
                      {t.revoked_at ? (
                        <Badge tone="danger">폐기함</Badge>
                      ) : (
                        <Button size="sm" variant="danger" onClick={() => setPending({ kind: "token", item: t })}>
                          폐기
                        </Button>
                      )}
                    </li>
                  ))}
                </List>
              ) : (
                <EmptyState>아직 받은 토큰이 없습니다. 원격 MCP 클라이언트가 OAuth 토큰으로 접속하면 여기에 보입니다.</EmptyState>
              )}
            </Section>

            <ConfirmDialog
              open={pending !== null}
              onOpenChange={(open) => (open ? null : setPending(null))}
              title={pending?.kind === "link" ? "연결을 끊을까요?" : "토큰을 폐기할까요?"}
              description={
                pending?.kind === "link"
                  ? "이 사용자의 모든 토큰이 다음 요청부터 거부됩니다. 다시 쓰려면 새로 연결해야 하고, 그 전에 받은 토큰은 계속 거부됩니다."
                  : "이 토큰은 만료 전이라도 다음 요청부터 거부됩니다. 같은 사용자의 다른 토큰은 그대로 쓸 수 있습니다."
              }
              confirmLabel={pending?.kind === "link" ? "끊기" : "폐기"}
              cancelLabel="닫기"
              tone="danger"
              busy={revoke.isPending}
              onConfirm={() => pending && revoke.mutate(pending)}
            >
              <ErrorText error={revoke.error ? oauthError(revoke.error) : null} />
            </ConfirmDialog>
          </div>
        );
      }}
    </Loaded>
  );
}
