import { cn } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { KeyRound } from "lucide-react";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { tokenDaysLeft, tokenExpiresSoon, tokenExpiryText } from "../lib/tokens";

/**
 * 지금 이 브라우저가 쓰는 API 토큰이 곧 만료되면 알린다 (아니면 아무것도 그리지 않는다).
 * 오늘 화면은 설정으로 가는 링크를, 설정 화면(소유자가 아닐 때)은 소유자에게 부탁하라는 안내를 단다.
 */
export function TokenExpiryNotice({ className, inSettings = false }: { className?: string; inSettings?: boolean }) {
  // 역할과 무관하게 자기 토큰 한 줄만 읽는다. API 토큰이 아니면 404 → 표시 없음
  const q = useQuery({ queryKey: ["api-tokens", "current"], queryFn: api.currentApiToken, retry: false });
  const t = q.data;
  if (!t || !tokenExpiresSoon(t)) return null;
  const n = tokenDaysLeft(t) ?? 0;
  return (
    <div role="status" className={cn("flex items-start gap-2.5 border-y border-line py-2.5 text-[13px]", className)}>
      <KeyRound size={15} className="mt-0.5 shrink-0" aria-hidden />
      <p className="min-w-0 flex-1">
        지금 쓰는 API 토큰 <span className="font-medium">"{t.label}"</span>이 {tokenExpiryText(n)}입니다{" "}
        <span className="tabular-nums">({formatDate(t.expires_at)})</span>.{" "}
        <span className="text-muted">만료되면 이 브라우저와 그 토큰을 쓰는 MCP 연결이 모두 거절됩니다.</span>
        {inSettings ? (
          <span className="text-muted"> 연장은 공간 소유자가 합니다. 소유자에게 연장이나 새 토큰을 부탁하세요.</span>
        ) : null}
      </p>
      {inSettings ? null : (
        <Link to="/settings" className="shrink-0 font-medium underline underline-offset-2 hover:no-underline">
          연장
        </Link>
      )}
    </div>
  );
}
