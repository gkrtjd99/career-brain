import type { ApiToken, ApiTokenStatus, IssuedApiToken } from "@career-brain/client";
import { Badge, Button, Card, ConfirmDialog, Input, Label, Section, Select } from "@career-brain/ui";
import type { BadgeTone } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatDate, formatDateTime } from "../lib/dates";
import { ErrorText, Loaded } from "./common";

const STATUS: Record<ApiTokenStatus, { label: string; tone: BadgeTone }> = {
  active: { label: "사용 가능", tone: "ok" },
  expired: { label: "만료", tone: "warn" },
  revoked: { label: "폐기", tone: "none" },
};

const EXPIRY_CHOICES: [string, string][] = [
  ["", "만료 없음"],
  ["30", "30일"],
  ["90", "90일"],
  ["365", "1년"],
];

function revokeError(error: unknown) {
  if (error instanceof ApiError && error.code === "token_in_use")
    return "지금 이 브라우저가 쓰는 토큰은 폐기할 수 없습니다. 새 토큰으로 바꾼 뒤 폐기하세요.";
  return error;
}

/** 설정 화면의 API 토큰 목록·발급·폐기 (소유자만). 새 토큰 원문은 발급 직후 한 번만 보인다. */
export function TokensSection() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["api-tokens"], queryFn: api.apiTokens });
  const [label, setLabel] = useState("");
  const [expires, setExpires] = useState("");
  const [issued, setIssued] = useState<IssuedApiToken | null>(null);
  const [copied, setCopied] = useState(false);
  const [target, setTarget] = useState<ApiToken | null>(null);

  const issue = useMutation({
    mutationFn: () =>
      api.issueApiToken({ label: label.trim(), expires_days: expires ? Number(expires) : null }),
    onSuccess: (res) => {
      setIssued(res);
      setCopied(false);
      setLabel("");
      void qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (tokenId: string) => api.revokeApiToken(tokenId),
    onSuccess: () => {
      setTarget(null);
      void qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });

  return (
    <Section title="토큰 관리">
      <Card className="space-y-4 p-4">
        <p className="text-xs leading-5 text-muted">
          이 작업 공간의 API 토큰입니다. 서버에는 토큰 원문이 없고(해시만), 이 목록에도 나오지 않습니다. 마지막 사용
          시각은 최대 5분 늦게 바뀝니다.
        </p>
        <Loaded query={tokens}>
          {(rows) => (
            <ul className="divide-y divide-line rounded-lg border border-line">
              {rows.map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-[13px]">
                  <span className="font-medium">{t.label}</span>
                  <Badge tone={STATUS[t.status].tone}>{STATUS[t.status].label}</Badge>
                  {t.current ? <Badge tone="accent">지금 사용 중</Badge> : null}
                  <span className="text-xs text-muted">
                    만든 날 {formatDate(t.created_at)}
                    {t.expires_at ? ` · 만료 ${formatDate(t.expires_at)}` : " · 만료 없음"}
                    {` · 마지막 사용 ${t.last_used_at ? formatDateTime(t.last_used_at) : "없음"}`}
                  </span>
                  {t.status === "active" && !t.current ? (
                    <Button size="sm" variant="danger" className="ml-auto" onClick={() => setTarget(t)}>
                      폐기
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Loaded>

        <form
          className="space-y-2 border-t border-line pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (label.trim()) issue.mutate();
          }}
        >
          <p className="text-[13px] font-medium">새 토큰</p>
          <div className="grid gap-2 sm:grid-cols-[1fr_10rem]">
            <div>
              <Label htmlFor="token-label">이름 (어디서 쓰는지)</Label>
              <Input
                id="token-label"
                value={label}
                maxLength={100}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="예: 노트북, MCP"
              />
            </div>
            <div>
              <Label htmlFor="token-expiry">만료</Label>
              <Select id="token-expiry" value={expires} onChange={(e) => setExpires(e.target.value)}>
                {EXPIRY_CHOICES.map(([v, text]) => (
                  <option key={v} value={v}>
                    {text}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <ErrorText error={issue.error} />
          <Button type="submit" variant="primary" size="sm" disabled={!label.trim() || issue.isPending}>
            토큰 만들기
          </Button>
        </form>

        {issued ? (
          <div className="space-y-2 rounded-lg border border-line bg-accent-soft p-3" role="status">
            <p className="text-[13px] font-medium">
              새 토큰 "{issued.token.label}" — 지금 한 번만 보입니다. 안전한 곳에 보관하세요.
            </p>
            <code className="block break-all rounded bg-surface px-2 py-1.5 text-[12px]">{issued.secret}</code>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => {
                  void navigator.clipboard?.writeText(issued.secret).then(() => setCopied(true));
                }}
              >
                {copied ? "복사함" : "복사"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setIssued(null)}>
                닫기 (다시 볼 수 없음)
              </Button>
            </div>
          </div>
        ) : null}
      </Card>

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) {
            setTarget(null);
            revoke.reset();
          }
        }}
        title={`토큰 "${target?.label ?? ""}" 폐기`}
        description="폐기하면 다음 요청부터 이 토큰을 쓰는 API·MCP 연결이 모두 거부됩니다. 되돌릴 수 없습니다."
        confirmLabel="폐기"
        tone="danger"
        busy={revoke.isPending}
        onConfirm={() => target && revoke.mutate(target.id)}
      >
        <ErrorText error={revokeError(revoke.error)} />
      </ConfirmDialog>
    </Section>
  );
}
