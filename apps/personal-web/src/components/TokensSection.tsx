import type { ApiToken, ApiTokenStatus, IssuedApiToken } from "@career-brain/client";
import { Badge, Button, Card, ConfirmDialog, Input, Label, Section, Select } from "@career-brain/ui";
import type { BadgeTone } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatDate, formatDateTime } from "../lib/format";
import { TOKEN_SOON_DAYS, tokenDaysLeft, tokenExpiresSoon, tokenExpiryText } from "../lib/tokens";
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

// 연장: 지금부터 N일 뒤. 지금 만료일보다 뒤인 것만 고를 수 있다 (서버도 연장만 받는다)
const EXTEND_CHOICES: [number, string][] = [
  [30, "지금부터 30일"],
  [90, "지금부터 90일"],
  [365, "지금부터 1년"],
];
const DAY_MS = 86_400_000;

function updateError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === "not_an_extension") return "지금 만료일보다 뒤로만 연장할 수 있습니다.";
    if (error.code === "token_expired") return "이미 만료된 토큰은 되살리지 않습니다. 새 토큰을 만드세요.";
    if (error.code === "token_revoked") return "폐기한 토큰은 고칠 수 없습니다.";
  }
  return error;
}

/** 토큰 한 줄의 이름 바꾸기·만료 연장 칸 (사용 가능한 토큰만). */
function TokenEditor({ token, onDone }: { token: ApiToken; onDone: () => void }) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(token.label);
  const expiresAt = token.expires_at ? new Date(token.expires_at).getTime() : null;
  const choices = EXTEND_CHOICES.filter(([days]) => expiresAt !== null && Date.now() + days * DAY_MS > expiresAt);
  const [extend, setExtend] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.updateApiToken(token.id, {
        ...(label.trim() && label.trim() !== token.label ? { label: label.trim() } : {}),
        ...(extend ? { expires_days: Number(extend) } : {}),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["api-tokens"] });
      onDone();
    },
  });
  const changed = (label.trim() && label.trim() !== token.label) || extend;
  return (
    <form
      className="mt-2 grid w-full gap-2 sm:grid-cols-[1fr_11rem_auto]"
      onSubmit={(e) => {
        e.preventDefault();
        if (changed) save.mutate();
      }}
    >
      <div>
        <Label htmlFor={`token-label-${token.id}`}>이름</Label>
        <Input
          id={`token-label-${token.id}`}
          value={label}
          maxLength={100}
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor={`token-extend-${token.id}`}>만료 연장</Label>
        <Select
          id={`token-extend-${token.id}`}
          value={extend}
          disabled={!choices.length}
          onChange={(e) => setExtend(e.target.value)}
        >
          <option value="">{expiresAt === null ? "만료 없음 (연장할 것 없음)" : "그대로"}</option>
          {choices.map(([v, text]) => (
            <option key={v} value={String(v)}>
              {text}
            </option>
          ))}
        </Select>
      </div>
      <div className="flex items-end gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={!changed || save.isPending}>
          저장
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          취소
        </Button>
      </div>
      <ErrorText error={updateError(save.error)} className="sm:col-span-3" />
    </form>
  );
}

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
  const [editing, setEditing] = useState<string | null>(null);
  const soon = (tokens.data ?? []).filter((t) => tokenExpiresSoon(t));

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
        {soon.length ? (
          <div role="status" className="rounded-lg border border-warn/40 bg-surface px-3 py-2 text-[13px]">
            <p className="font-medium">곧 만료되는 토큰 ({TOKEN_SOON_DAYS}일 안)</p>
            <ul className="mt-1 list-disc pl-5 text-muted">
              {soon.map((t) => (
                <li key={t.id}>
                  {t.label}
                  {t.current ? " (지금 이 브라우저가 쓰는 토큰)" : ""} — {tokenExpiryText(tokenDaysLeft(t) ?? 0)} (
                  {formatDate(t.expires_at)})
                </li>
              ))}
            </ul>
            <p className="mt-1 text-xs text-muted">
              "고치기"로 만료를 연장하거나, 새 토큰을 만들어 바꾼 뒤 옛 토큰을 폐기하세요.
            </p>
          </div>
        ) : null}
        <Loaded query={tokens}>
          {(rows) => (
            <ul className="divide-y divide-line rounded-lg border border-line">
              {rows.map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-[13px]">
                  <span className="font-medium">{t.label}</span>
                  <Badge tone={STATUS[t.status].tone}>{STATUS[t.status].label}</Badge>
                  {t.current ? <Badge tone="accent">지금 사용 중</Badge> : null}
                  {tokenExpiresSoon(t) ? <Badge tone="warn">곧 만료</Badge> : null}
                  <span className="text-xs text-muted">
                    만든 날 {formatDate(t.created_at)}
                    {t.expires_at ? ` · 만료 ${formatDate(t.expires_at)}` : " · 만료 없음"}
                    {` · 마지막 사용 ${t.last_used_at ? formatDateTime(t.last_used_at) : "없음"}`}
                  </span>
                  <span className="ml-auto flex gap-2">
                    {t.status === "active" ? (
                      <Button size="sm" variant="ghost" onClick={() => setEditing(editing === t.id ? null : t.id)}>
                        고치기
                      </Button>
                    ) : null}
                    {t.status === "active" && !t.current ? (
                      <Button size="sm" variant="danger" onClick={() => setTarget(t)}>
                        폐기
                      </Button>
                    ) : null}
                  </span>
                  {editing === t.id ? <TokenEditor token={t} onDone={() => setEditing(null)} /> : null}
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
