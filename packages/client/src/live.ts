// 시스템 화면: 원격 MCP OAuth 연결·토큰 관리, 실행·승인 실시간 알림(SSE, c0019).
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

export interface OAuthConfigInfo {
  /** CAREER_OAUTH_ISSUER가 설정됐는지 */
  enabled: boolean;
  issuer: string | null;
  resource: string | null;
  jwks: boolean;
  introspection: boolean;
  require_jti: boolean;
  /** JWT도 introspection으로 확인 (인가 서버 폐기를 캐시 시간 안에 반영, c0025) */
  introspect_jwt: boolean;
  /** introspection 캐시 시간(초) = 인가 서버 폐기 반영 지연 상한. introspection이 꺼져 있으면 null */
  introspection_cache_seconds: number | null;
}

export interface OAuthLink {
  id: Uuid;
  actor_id: Uuid;
  issuer: string;
  subject: string;
  label: string;
  created_at: IsoDateTime;
  revoked_at: IsoDateTime | null;
}

export interface OAuthToken {
  id: Uuid;
  identity_id: Uuid;
  issuer: string;
  subject: string;
  /** jti, 또는 jti가 없으면 토큰 sha256 (원문 아님) */
  token_ref: string;
  ref_kind: "jti" | "sha256";
  via: "jwt" | "introspection";
  first_seen_at: IsoDateTime;
  last_seen_at: IsoDateTime;
  expires_at: IsoDateTime | null;
  uses: number;
  revoked_at: IsoDateTime | null;
}

export interface OAuthOverview {
  config: OAuthConfigInfo;
  links: OAuthLink[];
  tokens: OAuthToken[];
}

/** 실시간 알림 이벤트. 데이터에는 ID가 없다 — 받으면 목록을 다시 읽는다. */
export type LiveEvent =
  | { type: "ready"; channels: string[]; listening: boolean }
  | { type: "runs" | "approvals"; v: string }
  | { type: "unauthorized" }
  | { type: "bye" };

type Query = Record<string, string | number | undefined | null>;

export interface LiveDeps {
  request: (
    method: string,
    path: string,
    opts?: { query?: Query; signal?: AbortSignal },
  ) => Promise<Response>;
  get: <R>(path: string, query?: Query) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
}

/** `event:`/`data:` 줄 묶음 하나 → 이벤트. 주석(`: ping`)·모르는 이벤트는 null. */
export function parseSseChunk(chunk: string): LiveEvent | null {
  let name: string | null = null;
  const data: string[] = [];
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!name) return null;
  let body: Record<string, unknown> = {};
  try {
    body = data.length ? (JSON.parse(data.join("\n")) as Record<string, unknown>) : {};
  } catch {
    return null;
  }
  if (name === "ready" || name === "runs" || name === "approvals" || name === "unauthorized" || name === "bye") {
    return { type: name, ...body } as LiveEvent;
  }
  return null;
}

export function liveEndpoints({ request, get, post }: LiveDeps) {
  const id = encodeURIComponent;
  return {
    // 원격 MCP OAuth (소유자만). 토큰 원문은 어디에도 없다.
    oauth: (limit?: number) => get<OAuthOverview>("/core/oauth", { limit }),
    linkOAuth: (body: { subject: string; issuer?: string; label?: string }) =>
      post<OAuthLink>("/core/oauth/links", body),
    revokeOAuthLink: (linkId: string) => post<{ revoked: boolean }>(`/core/oauth/links/${id(linkId)}/revoke`),
    revokeOAuthToken: (tokenId: string) =>
      post<{ revoked: boolean }>(`/core/oauth/tokens/${id(tokenId)}/revoke`),

    /**
     * 실행·승인 상태 알림 스트림 (`GET /core/live`, SSE). 토큰은 다른 요청처럼 Authorization 헤더로
     * 보낸다 (EventSource는 헤더를 못 붙여 fetch 스트림으로 읽는다). 서버가 닫거나 끊기면 resolve한다.
     * 401/403이면 ApiError로 reject. 다시 붙는 것은 호출한 쪽이 한다.
     */
    liveStream: async (onEvent: (e: LiveEvent) => void, signal: AbortSignal, maxSeconds?: number) => {
      const res = await request("GET", "/core/live", { query: { max_seconds: maxSeconds }, signal });
      if (!res.body) return;
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value.replace(/\r\n/g, "\n");
          let cut = buf.indexOf("\n\n");
          while (cut >= 0) {
            const e = parseSseChunk(buf.slice(0, cut));
            buf = buf.slice(cut + 2);
            if (e) onEvent(e);
            cut = buf.indexOf("\n\n");
          }
        }
      } finally {
        reader.releaseLock();
      }
    },
  };
}
