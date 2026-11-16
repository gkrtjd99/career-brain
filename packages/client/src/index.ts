import type * as T from "./types";

export * from "./types";

export interface ClientOptions {
  /** 예: "/api" (개인용) 또는 서비스의 API prefix */
  baseUrl: string;
  /**
   * 요청마다 부르는 토큰 공급자. 값이 있으면 `Authorization: Bearer` 로 보낸다.
   * 쿠키 세션을 쓰는 설치(서비스)는 생략하고 `credentials`만 쓴다.
   */
  getToken?: () => string | null | undefined;
  /** 쿠키 세션을 쓰면 "include". 기본값은 같은 출처에만 쿠키를 보내는 "same-origin". */
  credentials?: RequestCredentials;
  fetch?: typeof fetch;
}

/** HTTP 오류. `code`는 FastAPI `detail`이 문자열일 때 그 값(예: "contribution_required"). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
    readonly detail: unknown,
  ) {
    super(code ? `${status} ${code}` : `HTTP ${status}`);
    this.name = "ApiError";
  }
}

type Query = Record<string, string | number | undefined | null>;

export function createClient({
  baseUrl,
  getToken,
  credentials = "same-origin",
  fetch: f = globalThis.fetch.bind(globalThis),
}: ClientOptions) {
  async function request(
    method: string,
    path: string,
    opts: { query?: Query; json?: unknown; form?: FormData; signal?: AbortSignal } = {},
  ): Promise<Response> {
    let url = `${baseUrl}${path}`;
    if (opts.query) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query)) {
        if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += `?${s}`;
    }
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = getToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    let body: BodyInit | undefined;
    if (opts.form) body = opts.form;
    else if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    const res = await f(url, { method, headers, body, credentials, signal: opts.signal });
    if (!res.ok) {
      let detail: unknown = null;
      try {
        detail = ((await res.json()) as { detail?: unknown }).detail ?? null;
      } catch {
        // 본문이 JSON이 아니면 상태 코드만 쓴다.
      }
      throw new ApiError(res.status, typeof detail === "string" ? detail : null, detail);
    }
    return res;
  }

  async function json<R>(method: string, path: string, opts?: Parameters<typeof request>[2]) {
    return (await (await request(method, path, opts)).json()) as R;
  }
  const get = <R>(path: string, query?: Query, signal?: AbortSignal) =>
    json<R>("GET", path, { query, signal });
  const post = <R>(path: string, body: unknown = {}) => json<R>("POST", path, { json: body });
  const patch = <R>(path: string, body: unknown) => json<R>("PATCH", path, { json: body });
  const id = encodeURIComponent;

  return {
    coreHealth: () => get<T.CoreHealth>("/core/health"),
    me: () => get<T.Me>("/core/me"),

    // 원본
    uploadSource: (file: File, opts: { title?: string; dataClass?: string } = {}) => {
      const form = new FormData();
      form.set("file", file);
      if (opts.title) form.set("title", opts.title);
      if (opts.dataClass) form.set("data_class", opts.dataClass);
      return json<T.UploadOut>("POST", "/core/sources", { form });
    },
    source: (sourceId: string) => get<T.Source>(`/core/sources/${id(sourceId)}`),
    blocks: (parseRunId: string) => get<T.Block[]>(`/core/parse-runs/${id(parseRunId)}/blocks`),
    requestExtraction: (parseRunId: string) =>
      post<T.StatusOut>(`/core/parse-runs/${id(parseRunId)}/extractions`),
    extraction: (runId: string) => get<T.ExtractionRun>(`/core/extraction-runs/${id(runId)}`),

    // 경험·주장
    experiences: () => get<T.ExperienceSummary[]>("/core/experiences"),
    experience: (experienceId: string) =>
      get<T.Experience>(`/core/experiences/${id(experienceId)}`),
    createExperience: (body: T.ExperienceIn) => post<T.StatusOut>("/core/experiences", body),
    addClaim: (experienceId: string, body: T.ClaimIn) =>
      post<T.StatusOut>(`/core/experiences/${id(experienceId)}/claims`, body),
    editClaim: (claimId: string, body: T.ClaimPatch) =>
      patch<T.StatusOut>(`/core/claims/${id(claimId)}`, body),
    confirmClaim: (claimId: string, body: T.ConfirmIn = {}) =>
      post<T.StatusOut>(`/core/claims/${id(claimId)}/confirm`, body),
    disputeClaim: (claimId: string) => post<T.StatusOut>(`/core/claims/${id(claimId)}/dispute`),
    retireClaim: (claimId: string) => post<T.StatusOut>(`/core/claims/${id(claimId)}/retire`),
    attachSpan: (claimId: string, spanId: string) =>
      post<T.StatusOut>(`/core/claims/${id(claimId)}/evidence`, { span_id: spanId }),
    createSpan: (blockId: string, startChar: number, endChar: number) =>
      post<T.StatusOut>("/core/source-spans", {
        block_id: blockId,
        start_char: startChar,
        end_char: endChar,
      }),
    span: (spanId: string) => get<T.Span>(`/core/source-spans/${id(spanId)}`),

    // 인터뷰
    startInterview: (experienceId: string | null) =>
      post<T.Interview>("/core/interviews", { experience_id: experienceId }),
    interview: (sessionId: string) => get<T.Interview>(`/core/interviews/${id(sessionId)}`),
    aiQuestions: (sessionId: string, count = 3) =>
      post<T.Interview>(`/core/interviews/${id(sessionId)}/ai-questions`, { count }),
    answer: (questionId: string, body: T.AnswerIn) =>
      post<T.AnswerOut>(`/core/interview-questions/${id(questionId)}/answers`, body),

    // 위키
    wiki: (experienceId: string) => get<T.WikiPage>(`/core/wiki/experiences/${id(experienceId)}`),
    wikiMarkdown: async (experienceId: string) =>
      (await request("GET", `/core/wiki/experiences/${id(experienceId)}/markdown`)).text(),

    // 검색
    search: (
      q: string,
      opts: { kinds?: T.SearchKind[]; limit?: number } = {},
      signal?: AbortSignal,
    ) =>
      get<T.SearchResult>(
        "/core/search",
        { q, kinds: opts.kinds?.join(","), limit: opts.limit },
        signal,
      ),
    reindex: () => post<{ status: string }>("/core/search/reindex"),

    // 공고
    savePostings: (urls: string[]) => post<T.SaveUrlResult[]>("/core/job-postings", { urls }),
    postings: () => get<T.PostingSummary[]>("/core/job-postings"),
    posting: (postingId: string) => get<T.Posting>(`/core/job-postings/${id(postingId)}`),
    postingVersion: (postingId: string, versionId: string) =>
      get<T.PostingVersion>(`/core/job-postings/${id(postingId)}/versions/${id(versionId)}`),
    refetchPosting: (postingId: string) =>
      post<T.StatusOut>(`/core/job-postings/${id(postingId)}/refetch`),
    manualPostingText: (postingId: string, text: string) =>
      post<T.StatusOut>(`/core/job-postings/${id(postingId)}/manual-text`, { text }),
    confirmPosting: (postingId: string, body: T.ConfirmPostingIn) =>
      post<T.StatusOut>(`/core/job-postings/${id(postingId)}/confirm`, body),
    archivePosting: (postingId: string) =>
      post<T.StatusOut>(`/core/job-postings/${id(postingId)}/archive`),

    // 사용량
    usage: (opts: { days?: number; groupBy?: T.UsageGroup } = {}) =>
      get<T.Usage>("/core/usage", { days: opts.days, group_by: opts.groupBy }),
  };
}

export type CareerClient = ReturnType<typeof createClient>;
