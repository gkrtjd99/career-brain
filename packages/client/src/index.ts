export interface ClientOptions {
  /** 예: "/api" (개인용) 또는 서비스의 API prefix */
  baseUrl: string;
  /** 쿠키 세션을 쓰면 기본값 "include" 그대로 둔다 */
  credentials?: RequestCredentials;
  fetch?: typeof fetch;
}

export interface CoreHealth {
  status: string;
  core_version: string;
}

export function createClient({ baseUrl, credentials = "include", fetch: f = fetch }: ClientOptions) {
  async function get<T>(path: string): Promise<T> {
    const res = await f(`${baseUrl}${path}`, { credentials });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  }
  return {
    coreHealth: () => get<CoreHealth>("/core/health"),
  };
}

export type CareerClient = ReturnType<typeof createClient>;
