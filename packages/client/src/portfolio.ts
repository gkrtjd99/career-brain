// 포트폴리오 API (c0049, O18, plan §19.6). createClient 안의 요청 함수를 받아 쓴다.
import type { IsoDateTime, Uuid } from "./types";

/** public = 공개 가능 · application = 지원서에만 (공고별 묶음에서만 나감) · private = 나만 (어떤 출력에도 없음) */
export type PortfolioVisibility = "public" | "application" | "private";
export const PORTFOLIO_VISIBILITIES: PortfolioVisibility[] = ["public", "application", "private"];
export type PortfolioBundleKind = "all" | "role" | "posting";
export type PortfolioLinkKind = "repo" | "demo" | "article" | "video";
export const PORTFOLIO_LINK_KINDS: PortfolioLinkKind[] = ["repo", "demo", "article", "video"];
export type PortfolioImageKind = "screenshot" | "diagram";
export type PortfolioFormat = "html" | "zip";
/** none = 근거 없음 · ok · stale = 사실이 그 뒤에 바뀜 · broken = 확인이 풀림 */
export type PortfolioLineState = "none" | "ok" | "stale" | "broken";
export type PortfolioSection = "actions" | "results";

export interface PortfolioRepoInfo {
  full_name?: string;
  language?: string | null;
  created_at?: string | null;
  pushed_at?: string | null;
  description?: string | null;
  private?: boolean;
  stars?: number;
  forks?: number;
}

export interface PortfolioLink {
  id: string;
  kind: PortfolioLinkKind;
  url: string;
  label: string | null;
  /** GitHub에서 읽은 저장소 정보 (참고, 근거가 아님) */
  info?: PortfolioRepoInfo;
}

export interface PortfolioItemSummary {
  id: Uuid;
  experience_id: Uuid;
  experience_title: string;
  experience_active: boolean;
  title: string;
  visibility: PortfolioVisibility;
  tags: string[];
  links: PortfolioLink[];
  current_revision: number;
  lines: Record<PortfolioLineState, number>;
  results: number;
  images: number;
  images_private: number;
  bundles: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface PortfolioLine {
  id: string;
  text: string;
  claim_id: Uuid | null;
  basis: string | null;
  origin: "claim" | "user" | "ai";
  state: PortfolioLineState;
}

export interface PortfolioImage {
  id: Uuid;
  item_id: Uuid;
  position: number;
  kind: PortfolioImageKind;
  caption: string | null;
  visibility: PortfolioVisibility;
  mime: string;
  width_px: number;
  height_px: number;
  bytes: number;
  sha256: string;
  created_at: IsoDateTime;
}

export interface PortfolioImageUpload extends PortfolioImage {
  deduplicated: boolean;
  removed_metadata: string[];
}

export interface PortfolioItem {
  summary: PortfolioItemSummary;
  revision_no: number;
  revision_id: Uuid | null;
  content: {
    v: number;
    problem: string | null;
    role: string | null;
    actions: PortfolioLine[];
    results: PortfolioLine[];
  };
  experience: {
    id: Uuid | null;
    title: string;
    organization?: string | null;
    role?: string | null;
    kind?: string;
    period?: string | null;
    tags?: string[];
    active: boolean;
  };
  images: PortfolioImage[];
}

export type PortfolioOperation = { op: string } & Record<string, unknown>;

export interface PortfolioPicker {
  claims: {
    id: Uuid;
    statement: string;
    kind: string;
    experience_title: string;
    suggested: PortfolioSection;
    used: boolean;
  }[];
}

export interface PortfolioBundleSummary {
  id: Uuid;
  kind: PortfolioBundleKind;
  title: string;
  target_role: string | null;
  intro: string | null;
  parent_id: Uuid | null;
  parent_title: string | null;
  source_revision: number | null;
  parent_revision: number | null;
  application_id: Uuid | null;
  application_title: string | null;
  /** 이 묶음의 출력 규칙: public = 공개 가능만, application = 지원서에만도 */
  audience: "public" | "application";
  revision: number;
  items: number;
  shown: number;
  children: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface PortfolioBundle {
  summary: PortfolioBundleSummary;
  lineage: { id: Uuid; kind: PortfolioBundleKind; title: string }[];
  items: {
    item: PortfolioItemSummary;
    shown: boolean;
    hidden_reason: PortfolioVisibility | null;
    images_shown: number;
    images_hidden: number;
  }[];
  prep_order: Uuid[];
}

export interface PortfolioCheck {
  bundle_id: Uuid;
  revision: number;
  audience: "public" | "application";
  items_shown: number;
  checklist: {
    code: string;
    message: string;
    count: number;
    severity: "warning" | "info" | "error";
    targets: string[];
  }[];
}

export interface PortfolioExportCheck {
  filename: string;
  media_type: string;
  format: PortfolioFormat;
  revision: number;
  bytes: number;
  findings: { code: string; severity: string; message: string }[];
  summary: Record<string, number>;
}

export interface PortfolioImport {
  repo: {
    full_name: string;
    html_url: string;
    private: boolean;
    language: string | null;
    created_at: string | null;
    pushed_at: string | null;
    description: string | null;
    stars: number;
    forks: number;
  };
  readme: boolean;
  source_id: Uuid | null;
  parse_run_id: Uuid | null;
  deduplicated: boolean;
  item_id: Uuid | null;
}

export interface GitHubTokenStatus {
  set: boolean;
  last4: string | null;
  available: boolean;
}

interface PortfolioDeps {
  request: (
    method: string,
    path: string,
    opts?: { json?: unknown; form?: FormData; query?: Record<string, string | number | undefined> },
  ) => Promise<Response>;
  get: <R>(path: string, query?: Record<string, string | number | undefined>) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

export function portfolioEndpoints({ request, get, post, patch }: PortfolioDeps) {
  const id = encodeURIComponent;
  const item = (itemId: string) => `/core/portfolio/items/${id(itemId)}`;
  const bundle = (bundleId: string) => `/core/portfolio/bundles/${id(bundleId)}`;
  const image = (imageId: string) => `/core/portfolio/images/${id(imageId)}`;
  const put = async <R>(path: string, body: unknown) =>
    (await (await request("PUT", path, { json: body })).json()) as R;
  return {
    portfolioItems: (experienceId?: string) =>
      get<PortfolioItemSummary[]>("/core/portfolio/items", { experience_id: experienceId }),
    portfolioItem: (itemId: string) => get<PortfolioItem>(item(itemId)),
    createPortfolioItem: (body: {
      experience_id: Uuid;
      title?: string | null;
      visibility?: PortfolioVisibility;
      fill?: boolean;
    }) => post<{ id: Uuid }>("/core/portfolio/items", body),
    patchPortfolioItem: (
      itemId: string,
      body: {
        title?: string;
        visibility?: PortfolioVisibility;
        tags?: string[];
        links?: { id?: string; kind: PortfolioLinkKind; url: string; label?: string | null }[];
      },
    ) => patch<PortfolioItem>(item(itemId), body),
    deletePortfolioItem: async (itemId: string) => {
      await request("DELETE", item(itemId));
    },
    /** 409 {code: revision_conflict, current_revision} */
    savePortfolioItem: (
      itemId: string,
      body: { base_revision: number; operations: PortfolioOperation[]; idempotency_key?: string },
    ) => post<{ id: Uuid; revision_no: number; created: boolean }>(`${item(itemId)}/revisions`, body),
    portfolioPicker: (itemId: string) => get<PortfolioPicker>(`${item(itemId)}/picker`),
    /** 브라우저에서 줄인 이미지 (JPEG·PNG, 긴 변 1600px·1MB까지). 서버가 메타데이터를 한 번 더 지운다 */
    uploadPortfolioImage: async (
      itemId: string,
      file: Blob,
      opts: { kind: PortfolioImageKind; caption?: string; visibility: PortfolioVisibility; filename?: string },
    ) => {
      const form = new FormData();
      form.set("file", file, opts.filename ?? "image");
      form.set("kind", opts.kind);
      form.set("visibility", opts.visibility);
      if (opts.caption) form.set("caption", opts.caption);
      return (await (await request("POST", `${item(itemId)}/images`, { form })).json()) as PortfolioImageUpload;
    },
    patchPortfolioImage: (
      imageId: string,
      body: { caption?: string | null; kind?: PortfolioImageKind; visibility?: PortfolioVisibility; position?: number },
    ) => patch<PortfolioImage>(image(imageId), body),
    deletePortfolioImage: async (imageId: string) => {
      await request("DELETE", image(imageId));
    },
    /** 이미지 바이트 (인증이 필요해 object URL로 바꿔 쓴다) */
    portfolioImageFile: async (imageId: string) => (await request("GET", `${image(imageId)}/file`)).blob(),

    portfolioBundles: (applicationId?: string) =>
      get<PortfolioBundleSummary[]>("/core/portfolio/bundles", { application_id: applicationId }),
    portfolioBundle: (bundleId: string) => get<PortfolioBundle>(bundle(bundleId)),
    createPortfolioBundle: (body: {
      kind: PortfolioBundleKind;
      title?: string | null;
      parent_id?: Uuid | null;
      target_role?: string | null;
      application_id?: Uuid | null;
      intro?: string | null;
      fill?: boolean;
    }) => post<{ id: Uuid }>("/core/portfolio/bundles", body),
    patchPortfolioBundle: (
      bundleId: string,
      body: { title?: string; target_role?: string | null; intro?: string | null; application_id?: Uuid | null },
    ) => patch<PortfolioBundle>(bundle(bundleId), body),
    deletePortfolioBundle: async (bundleId: string) => {
      await request("DELETE", bundle(bundleId));
    },
    /** 항목 목록을 순서 그대로. 409 {code: revision_conflict} */
    setPortfolioBundleItems: (bundleId: string, body: { base_revision: number; item_ids: Uuid[] }) =>
      put<{ revision: number }>(`${bundle(bundleId)}/items`, body),
    applyPortfolioPrepOrder: (bundleId: string, baseRevision: number) =>
      post<{ revision: number }>(`${bundle(bundleId)}/prep-order`, { base_revision: baseRevision }),
    portfolioCheck: (bundleId: string) => get<PortfolioCheck>(`${bundle(bundleId)}/check`),
    /** 미리보기 HTML (내보낸 인쇄용 HTML과 같은 바이트) */
    portfolioPreview: async (bundleId: string): Promise<string> =>
      (await request("GET", `${bundle(bundleId)}/preview`)).text(),
    portfolioExportCheck: (bundleId: string, format: PortfolioFormat, resumeVersionId?: string) =>
      get<PortfolioExportCheck>(`${bundle(bundleId)}/export/check`, {
        format,
        resume_version_id: resumeVersionId,
      }),
    portfolioExportFile: async (bundleId: string, format: PortfolioFormat, resumeVersionId?: string) => {
      const res = await request("GET", `${bundle(bundleId)}/export`, {
        query: { format, resume_version_id: resumeVersionId },
      });
      const disposition = res.headers.get("Content-Disposition") ?? "";
      const m = /filename\*=UTF-8''([^;]+)/.exec(disposition);
      return {
        filename: m?.[1] ? decodeURIComponent(m[1]) : `portfolio.${format}`,
        blob: await res.blob(),
      };
    },

    /** GitHub 저장소 → README를 받은 자료함에 + 저장소 정보를 링크에. 429 {code: rate_limited, reset_at} */
    importGitHub: (body: { url: string; private?: boolean; item_id?: Uuid | null }) =>
      post<PortfolioImport>("/core/portfolio/github/import", body),
    gitHubToken: () => get<GitHubTokenStatus>("/core/portfolio/github/token"),
    setGitHubToken: (token: string) => put<GitHubTokenStatus>("/core/portfolio/github/token", { token }),
    deleteGitHubToken: async () => {
      await request("DELETE", "/core/portfolio/github/token");
    },
  };
}
