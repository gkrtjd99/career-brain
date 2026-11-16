// API 토큰 관리 (소유자만). 토큰 원문은 발급 응답의 `secret`에만 한 번 온다.
// createClient 안의 요청 함수를 받아 쓴다 (system.ts와 같은 방식).
import type { IsoDateTime, Uuid } from "./types";

export type ApiTokenStatus = "active" | "expired" | "revoked";

export interface ApiToken {
  id: Uuid;
  label: string;
  actor_id: Uuid;
  created_at: IsoDateTime;
  expires_at: IsoDateTime | null;
  /** 인증에 쓰인 마지막 시각 (최대 5분 늦게 갱신) */
  last_used_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
  status: ApiTokenStatus;
  /** 지금 이 브라우저가 쓰는 토큰 (폐기할 수 없다) */
  current: boolean;
}

export interface IssuedApiToken {
  token: ApiToken;
  /** 새 토큰 원문. 이때 한 번만 보인다. */
  secret: string;
}

export interface TokenDeps {
  get: <R>(path: string) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
}

export function tokenEndpoints({ get, post }: TokenDeps) {
  const id = encodeURIComponent;
  return {
    apiTokens: () => get<ApiToken[]>("/core/tokens"),
    issueApiToken: (body: { label: string; expires_days?: number | null }) =>
      post<IssuedApiToken>("/core/tokens", body),
    revokeApiToken: (tokenId: string) => post<ApiToken>(`/core/tokens/${id(tokenId)}/revoke`),
  };
}
