import { ApiError, createClient } from "@career-brain/client";
import { QueryCache, QueryClient, MutationCache } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

// 개인용 API 토큰. 이 브라우저의 localStorage 에만 둔다 (서버로는 Authorization 헤더로만 보낸다).
const TOKEN_KEY = "career-brain:token";
const listeners = new Set<() => void>();

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

let token = readToken();

export function getToken() {
  return token;
}

export function setToken(value: string | null) {
  token = value?.trim() || null;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 저장소를 못 쓰면 이 탭에서만 유지한다.
  }
  listeners.forEach((l) => l());
}

export function useToken() {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getToken,
    getToken,
  );
}

export const api = createClient({
  baseUrl: import.meta.env.VITE_API_BASE ?? "/api",
  getToken,
});

/** 401이면 토큰 설정 화면으로 보낸다. 라우터가 만들어진 뒤 main.tsx 에서 연결한다. */
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

function handle(error: unknown) {
  if (error instanceof ApiError && error.status === 401) onUnauthorized();
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: handle }),
  mutationCache: new MutationCache({ onError: handle }),
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (count, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

export { ApiError };
