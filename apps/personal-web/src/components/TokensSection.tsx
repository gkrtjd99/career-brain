import type { ApiToken, ApiTokenStatus, IssuedApiToken } from "@career-brain/client";
import { Button, ConfirmDialog, Input, Label, Section, Select } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ApiError, api } from "../lib/api";
import { formatDate, formatDateTime } from "../lib/dates";
import { TOKEN_SOON_DAYS, tokenDaysLeft, tokenExpiresSoon, tokenExpiryText } from "../lib/tokens";
import { ErrorText, Loaded } from "./common";

const STATUS: Record<ApiTokenStatus, string> = {
  active: "사용 가능",
  expired: "만료",
  revoked: "폐기",
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
  const [target, setTarget] = useState<ApiToken | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const soon = (tokens.data ?? []).filter((t) => tokenExpiresSoon(t));
  const revoke = useMutation({
    mutationFn: (tokenId: string) => api.revokeApiToken(tokenId),
    onSuccess: () => {
      setTarget(null);
      void qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });

  return (
    <Section title="토큰 관리">
      <div className="space-y-6">
        <p className="text-[13px] leading-5 text-muted">
          이 작업 공간의 API 토큰입니다. 서버에는 토큰 원문이 없고(해시만), 이 목록에도 나오지 않습니다. 마지막 사용
          시각은 최대 5분 늦게 바뀝니다.
        </p>
        {soon.length ? (
          <div role="status" className="space-y-1 text-[13px]">
            <p className="font-medium">곧 만료되는 토큰 ({TOKEN_SOON_DAYS}일 안)</p>
            <ul className="text-muted">
              {soon.map((t) => (
                <li key={t.id} className="tabular-nums">
                  {t.label}
                  {t.current ? " (지금 이 브라우저가 쓰는 토큰)" : ""} — {tokenExpiryText(tokenDaysLeft(t) ?? 0)} (
                  {formatDate(t.expires_at)})
                </li>
              ))}
            </ul>
            <p className="text-muted">"고치기"로 만료를 연장하거나, 새 토큰을 만들어 바꾼 뒤 옛 토큰을 폐기하세요.</p>
          </div>
        ) : null}
        <Loaded query={tokens}>
          {(rows) => (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((t) => (
                <li key={t.id} className="py-3 text-[13px]">
                  <div className="flex items-center gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {t.label}
                        {t.current ? <span className="font-normal text-muted"> · 이 브라우저</span> : null}
                      </div>
                      <div className="text-muted tabular-nums">
                        {t.status === "active" ? null : (
                          <span className="font-medium text-fg">{STATUS[t.status]} · </span>
                        )}
                        {tokenExpiresSoon(t) ? <span className="font-medium text-fg">곧 만료 · </span> : null}
                        만든 날 {formatDate(t.created_at)}
                        {t.expires_at ? ` · 만료 ${formatDate(t.expires_at)}` : " · 만료 없음"}
                        {` · 마지막 사용 ${t.last_used_at ? formatDateTime(t.last_used_at) : "없음"}`}
                      </div>
                    </div>
                    {t.status === "active" ? (
                      <div className="flex shrink-0 gap-1.5">
                        <Button size="sm" variant="ghost" onClick={() => setEditing(editing === t.id ? null : t.id)}>
                          고치기
                        </Button>
                        {!t.current ? (
                          <Button size="sm" variant="danger" onClick={() => setTarget(t)}>
                            폐기
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  {editing === t.id ? <TokenEditor token={t} onDone={() => setEditing(null)} /> : null}
                </li>
              ))}
            </ul>
          )}
        </Loaded>

        <NewTokenForm />
      </div>

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

/**
 * 새 토큰 만들기 칸. 토큰 원문은 만든 직후 여기 한 번만 보이고 다시 볼 수 없다.
 * MCP·에이전트 탭은 이름 "MCP"·만료 90일로 미리 채워 같은 칸을 쓴다.
 */
export function NewTokenForm({
  defaultLabel = "",
  defaultExpires = "",
  heading = "새 토큰",
}: {
  defaultLabel?: string;
  /** "" = 만료 없음, 아니면 일 수 */
  defaultExpires?: "" | "30" | "90" | "365";
  heading?: string;
}) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(defaultLabel);
  const [expires, setExpires] = useState<string>(defaultExpires);
  const [issued, setIssued] = useState<IssuedApiToken | null>(null);
  const [copied, setCopied] = useState(false);
  const fid = useId();
  const issue = useMutation({
    mutationFn: () => api.issueApiToken({ label: label.trim(), expires_days: expires ? Number(expires) : null }),
    onSuccess: (res) => {
      setIssued(res);
      setCopied(false);
      setLabel(defaultLabel);
      void qc.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });
  return (
    <div className="space-y-6">
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (label.trim()) issue.mutate();
        }}
      >
        <h3 className="text-sm font-medium">{heading}</h3>
        <div className="grid gap-2 sm:grid-cols-[1fr_10rem]">
          <div>
            <Label htmlFor={`${fid}-label`}>이름 (어디서 쓰는지)</Label>
            <Input
              id={`${fid}-label`}
              value={label}
              maxLength={100}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="예: 노트북, MCP"
            />
          </div>
          <div>
            <Label htmlFor={`${fid}-expiry`}>만료</Label>
            <Select id={`${fid}-expiry`} value={expires} onChange={(e) => setExpires(e.target.value)}>
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
        <div className="space-y-2 rounded-md border border-fg p-3" role="status">
          <p className="text-[13px] font-medium">
            새 토큰 "{issued.token.label}" — 지금 한 번만 보입니다. 안전한 곳에 보관하세요.
          </p>
          <code className="block rounded bg-subtle px-2 py-1.5 text-xs break-all">{issued.secret}</code>
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
    </div>
  );
}
