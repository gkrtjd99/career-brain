// 회사 조사 API (O13, plan §19.1). 참고 지식: 회사 항목은 내 경험이 아니고 문서 근거로 쓰지 않는다.
// createClient 안의 요청 함수를 받아 쓴다 (인증·오류 처리는 그쪽과 같다).
import type { IsoDateTime, Uuid } from "./types";

/** talent 인재상 · values 핵심 가치 · business 주요 사업 · product · finance · culture · news · hiring 채용 절차 · other */
export type CompanyFactKind =
  | "talent"
  | "values"
  | "business"
  | "product"
  | "finance"
  | "culture"
  | "news"
  | "hiring"
  | "other";
export const COMPANY_FACT_KINDS: CompanyFactKind[] = [
  "talent",
  "values",
  "business",
  "product",
  "finance",
  "culture",
  "news",
  "hiring",
  "other",
];

export interface CompanySummary {
  id: Uuid;
  name: string;
  aliases: string[];
  homepage: string | null;
  facts_confirmed: number;
  facts_candidate: number;
  facts_stale: number;
  postings: number;
  updated_at: IsoDateTime;
}

/** 항목의 인용: 원문(page_id) 안의 위치와 그대로 복사한 글 */
export interface CompanyCitation {
  page_id: Uuid;
  quote: string;
  start: number;
  end: number;
  source_url: string | null;
  /** 글을 받은 시각 (가져온 날·붙여넣은 날) */
  fetched_at: IsoDateTime;
  origin: "url" | "paste";
}

export interface CompanyFact {
  id: Uuid;
  company_id: Uuid;
  kind: CompanyFactKind;
  body: string;
  /** 기준일: "2024" | "2024-06" | "2024-06-30" (원문에 적힌 만큼). 모르면 null */
  as_of: string | null;
  status: "candidate" | "confirmed";
  origin: "model" | "user";
  /** 기준일(그 기간의 끝)이나 가져온 날이 약 12개월보다 오래됨 */
  stale: boolean;
  citation: CompanyCitation;
  confirmed_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface CompanyPage {
  id: Uuid;
  origin: "url" | "paste";
  url: string | null;
  final_url: string | null;
  title: string | null;
  status: "pending" | "done" | "failed";
  /** 공고 가져오기와 같은 코드 (source_unavailable, login_required, bot_blocked …) */
  error_code: string | null;
  fetched_at: IsoDateTime | null;
  candidates: number;
  /** no_model · llm_denied:… · llm_dropped:n … */
  notes: string[];
  text_chars: number;
  created_at: IsoDateTime;
  /** 원문 하나를 열 때만 (GET …/pages/{id}) */
  text?: string | null;
}

export interface CompanyLinkedPosting {
  id: Uuid;
  title: string | null;
  company: string | null;
  status: string;
  deadline: string | null;
}

export interface CompanyLinkedApplication {
  id: Uuid;
  posting_id: Uuid;
  title: string;
  status: string;
  submitted_at: IsoDateTime | null;
}

export interface Company {
  id: Uuid;
  name: string;
  aliases: string[];
  homepage: string | null;
  corp_code: string | null;
  business_no: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  facts: CompanyFact[];
  pages: CompanyPage[];
  postings: CompanyLinkedPosting[];
  applications: CompanyLinkedApplication[];
}

export interface CompanyIn {
  name: string;
  aliases?: string[];
  homepage?: string | null;
  corp_code?: string | null;
  business_no?: string | null;
}

/** 준 칸만 바꾼다. null = 비우기 (이름은 비울 수 없다) */
export type CompanyPatch = Partial<CompanyIn>;

/** quote는 원문(page_id) 안에 있어야 한다 (없으면 422 quote_not_found). 재무는 as_of 필수 */
export interface CompanyFactIn {
  page_id: Uuid;
  kind: CompanyFactKind;
  body: string;
  quote: string;
  as_of?: string | null;
}

export type CompanyFactPatch = Partial<Omit<CompanyFactIn, "page_id">>;

export interface CompanyLinkSuggestion {
  posting_id: Uuid;
  title: string | null;
  /** 공고에 적힌 회사 이름 그대로 */
  company: string | null;
  /** 같다고 본 회사 이름 또는 별칭 */
  matched: string;
}

interface CompanyDeps {
  request: (method: string, path: string, opts?: { json?: unknown }) => Promise<Response>;
  get: <R>(path: string, query?: Record<string, string | undefined>) => Promise<R>;
  post: <R>(path: string, body?: unknown) => Promise<R>;
  patch: <R>(path: string, body: unknown) => Promise<R>;
}

export function companyEndpoints({ request, get, post, patch }: CompanyDeps) {
  const id = encodeURIComponent;
  const base = (companyId: string) => `/core/companies/${id(companyId)}`;
  return {
    companies: (q?: string) => get<CompanySummary[]>("/core/companies", { q: q || undefined }),
    company: (companyId: string) => get<Company>(base(companyId)),
    createCompany: (body: CompanyIn) => post<{ id: Uuid }>("/core/companies", body),
    updateCompany: (companyId: string, body: CompanyPatch) => patch<{ id: Uuid }>(base(companyId), body),
    deleteCompany: async (companyId: string) => {
      await request("DELETE", base(companyId));
    },
    addCompanyFact: (companyId: string, body: CompanyFactIn) =>
      post<{ id: Uuid }>(`${base(companyId)}/facts`, body),
    updateCompanyFact: (companyId: string, factId: string, body: CompanyFactPatch) =>
      patch<{ id: Uuid }>(`${base(companyId)}/facts/${id(factId)}`, body),
    confirmCompanyFact: (companyId: string, factId: string) =>
      post<{ id: Uuid; status: string }>(`${base(companyId)}/facts/${id(factId)}/confirm`),
    deleteCompanyFact: async (companyId: string, factId: string) => {
      await request("DELETE", `${base(companyId)}/facts/${id(factId)}`);
    },
    /** 페이지 한 건을 가져오는 작업을 넣는다 (202). 끝나면 회사의 pages에 상태가 보인다 */
    importCompanyUrl: (companyId: string, url: string) =>
      post<{ id: Uuid; status: "pending" }>(`${base(companyId)}/import-url`, { url }),
    importCompanyPaste: (companyId: string, text: string, title?: string) =>
      post<CompanyPage>(`${base(companyId)}/import-paste`, { text, title: title || null }),
    companyPage: (companyId: string, pageId: string) =>
      get<CompanyPage>(`${base(companyId)}/pages/${id(pageId)}`),
    companyPostingSuggestions: (companyId: string) =>
      get<CompanyLinkSuggestion[]>(`${base(companyId)}/posting-suggestions`),
    linkCompanyPostings: (companyId: string, postingIds: string[]) =>
      post<{ linked: number }>(`${base(companyId)}/link-postings`, { posting_ids: postingIds }),
    unlinkCompanyPostings: (companyId: string, postingIds: string[]) =>
      post<{ linked: number }>(`${base(companyId)}/unlink-postings`, { posting_ids: postingIds }),
  };
}
