import type { ExperienceKind, SearchKind, SourcePurpose } from "@career-brain/client";
import { createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect } from "@tanstack/react-router";
import { AppShell } from "./components/AppShell";
import { getToken } from "./lib/api";
import { BrainPage } from "./pages/Brain";
import { ConnectPage } from "./pages/Connect";
import { ApplicationsPage } from "./pages/Applications";
import { ExperiencePage } from "./pages/Experience";
import { NotFoundPage } from "./pages/NotFound";
import { PostingPage } from "./pages/Posting";
import { PostingsPage } from "./pages/Postings";
import { SearchPage } from "./pages/Search";
import { SourcePage, SourcesPage } from "./pages/Sources";
import type { ManageSearch, ManageTab } from "./pages/Manage";
import type { StudyTab } from "./pages/Study";
import { TodayPage } from "./pages/Today";

const rootRoute = createRootRoute({ component: AppShell, notFoundComponent: NotFoundPage });

/** 토큰이 없으면 첫 연결 화면으로. 연결한 뒤 원래 가려던 곳으로 돌아온다. */
function requireToken({ location }: { location: { href: string } }) {
  if (!getToken())
    throw redirect({ to: "/connect", search: { next: location.href === "/" ? undefined : location.href } });
}

/** 같은 앱 안의 경로만 돌아갈 곳으로 받는다 */
function safeNext(v: unknown): string | undefined {
  return typeof v === "string" && v.startsWith("/") && !v.startsWith("//") ? v : undefined;
}

export const connectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/connect",
  validateSearch: (s: Record<string, unknown>): { reason?: "invalid"; next?: string } => ({
    ...(s.reason === "invalid" ? { reason: "invalid" as const } : {}),
    ...(safeNext(s.next) ? { next: safeNext(s.next) } : {}),
  }),
  component: ConnectPage,
});

const todayRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: requireToken,
  component: TodayPage,
});

/** 브레인 색인의 거름·보기 (링크로 같은 보기를 다시 연다). review = 확인 필요만 */
export interface BrainSearch {
  q?: string;
  view?: "table";
  kind?: ExperienceKind;
  tag?: string[];
  year?: number;
  review?: boolean;
  sort?: "updated" | "title";
}
const BRAIN_KINDS: ExperienceKind[] = ["work", "project", "education", "activity", "other"];

const brainRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/brain",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): BrainSearch => {
    const out: BrainSearch = {};
    if (typeof s.q === "string" && s.q.trim()) out.q = s.q.slice(0, 200);
    if (s.view === "table") out.view = "table";
    if (BRAIN_KINDS.includes(s.kind as ExperienceKind)) out.kind = s.kind as ExperienceKind;
    const tags = (Array.isArray(s.tag) ? s.tag : typeof s.tag === "string" ? [s.tag] : []).filter(
      (t): t is string => typeof t === "string" && t.trim() !== "",
    );
    if (tags.length) out.tag = tags.slice(0, 30);
    const year = Number(s.year);
    if (Number.isInteger(year) && year >= 1900 && year <= 2200) out.year = year;
    if (s.review === true || s.review === "true" || s.review === "1") out.review = true;
    if (s.sort === "updated" || s.sort === "title") out.sort = s.sort;
    return out;
  },
  component: BrainPage,
});

const PURPOSES: SourcePurpose[] = ["experience", "proof", "reference"];

/** 올린 자료 전체 (받은 자료함의 "모든 자료"). purpose = 용도 필터 */
export const sourcesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/brain/sources",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): { purpose?: SourcePurpose } =>
    PURPOSES.includes(s.purpose as SourcePurpose) ? { purpose: s.purpose as SourcePurpose } : {},
  component: SourcesPage,
});

export const sourceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/brain/sources/$sourceId",
  beforeLoad: requireToken,
  component: SourcePage,
});

export type ExperienceTab = "claims" | "wiki" | "interview";
const TABS: ExperienceTab[] = ["claims", "wiki", "interview"];

export const experienceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/brain/$experienceId",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): { tab?: ExperienceTab } =>
    TABS.includes(s.tab as ExperienceTab) && s.tab !== "claims" ? { tab: s.tab as ExperienceTab } : {},
  component: ExperiencePage,
});

const KINDS: SearchKind[] = ["claim", "block", "answer"];
export const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/search",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): { q?: string; kinds?: string; ref?: true } => {
    const q = typeof s.q === "string" && s.q.trim() ? s.q : undefined;
    const ref = s.ref === true || s.ref === "true" || s.ref === "1";
    const kinds =
      typeof s.kinds === "string"
        ? s.kinds
            .split(",")
            .filter((k): k is SearchKind => KINDS.includes(k as SearchKind))
            .join(",") || undefined
        : undefined;
    return { ...(q ? { q } : {}), ...(kinds ? { kinds } : {}), ...(ref ? { ref: true as const } : {}) };
  },
  component: SearchPage,
});

const postingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/postings",
  beforeLoad: requireToken,
  component: PostingsPage,
});

export const postingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/postings/$postingId",
  beforeLoad: requireToken,
  component: PostingPage,
});

// ---------------------------------------------------------------- 연결·관리 (plan §21)

const MANAGE_TABS: ManageTab[] = ["models", "routes", "usage", "mcp", "runs", "tokens", "maintenance", "settings"];

/** 승인·실행 상세(?approval=, ?run=)는 작업·승인 탭에서 열린다 */
function manageSearch(s: Record<string, unknown>): ManageSearch {
  const approval = typeof s.approval === "string" && s.approval ? s.approval : undefined;
  const run = typeof s.run === "string" && s.run ? s.run : undefined;
  const tab = approval || run ? "runs" : MANAGE_TABS.includes(s.tab as ManageTab) ? (s.tab as ManageTab) : undefined;
  return {
    ...(tab && tab !== "models" ? { tab } : {}),
    ...(approval ? { approval } : {}),
    ...(run ? { run } : {}),
  };
}

export const manageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/manage",
  beforeLoad: requireToken,
  validateSearch: manageSearch,
  component: lazyRouteComponent(() => import("./pages/Manage"), "ManagePage"),
});

// 옛 주소는 해당 탭으로 보낸다 (북마크·다른 화면의 링크)
const usageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/usage",
  beforeLoad: () => {
    throw redirect({ to: "/manage", search: { tab: "usage" }, replace: true });
  },
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  validateSearch: (s: Record<string, unknown>): { reason?: "missing" | "invalid" } =>
    s.reason === "missing" || s.reason === "invalid" ? { reason: s.reason } : {},
  beforeLoad: ({ search }) => {
    // 토큰이 없거나 거절된 경우는 첫 연결 화면이 맡는다
    if (search.reason || !getToken())
      throw redirect({ to: "/connect", search: search.reason === "invalid" ? { reason: "invalid" } : {}, replace: true });
    throw redirect({ to: "/manage", search: { tab: "settings" }, replace: true });
  },
});

const applicationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/applications",
  beforeLoad: requireToken,
  component: ApplicationsPage,
});

export const applicationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/applications/$applicationId",
  beforeLoad: requireToken,
  component: lazyRouteComponent(() => import("./pages/Application"), "ApplicationPage"),
});

export const documentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/applications/$applicationId/documents/$documentId",
  beforeLoad: requireToken,
  component: lazyRouteComponent(() => import("./pages/Document"), "DocumentPage"),
});

const sharedDocumentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents",
  beforeLoad: requireToken,
  component: lazyRouteComponent(() => import("./pages/SharedDocuments"), "SharedDocumentsPage"),
});

export const sharedDocumentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents/$documentId",
  beforeLoad: requireToken,
  component: lazyRouteComponent(() => import("./pages/Document"), "SharedDocumentPage"),
});

const STUDY_TABS: StudyTab[] = ["reviews", "coverage", "frequency", "items", "assessments", "mistakes"];
export const studyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/study",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): { tab?: StudyTab; posting?: string } => ({
    ...(STUDY_TABS.includes(s.tab as StudyTab) && s.tab !== "reviews" ? { tab: s.tab as StudyTab } : {}),
    ...(typeof s.posting === "string" && s.posting ? { posting: s.posting } : {}),
  }),
  component: lazyRouteComponent(() => import("./pages/Study"), "StudyPage"),
});

export const studyItemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/study/items/$itemId",
  beforeLoad: requireToken,
  component: lazyRouteComponent(() => import("./pages/StudyItem"), "StudyItemPage"),
});

type SystemTab = "runs" | "approvals" | "maintenance" | "audit" | "oauth";
const SYSTEM_TO_MANAGE: Record<SystemTab, ManageTab> = {
  runs: "runs",
  approvals: "runs",
  maintenance: "maintenance",
  audit: "mcp",
  oauth: "mcp",
};
export const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  validateSearch: (s: Record<string, unknown>): { tab?: SystemTab; approval?: string; run?: string } => ({
    ...(typeof s.tab === "string" && s.tab in SYSTEM_TO_MANAGE ? { tab: s.tab as SystemTab } : {}),
    ...(typeof s.approval === "string" && s.approval ? { approval: s.approval } : {}),
    ...(typeof s.run === "string" && s.run ? { run: s.run } : {}),
  }),
  beforeLoad: ({ search }) => {
    throw redirect({
      to: "/manage",
      search: manageSearch({ ...search, tab: SYSTEM_TO_MANAGE[search.tab ?? "runs"] }),
      replace: true,
    });
  },
});

// 사진 변환: 브라우저 안에서만 처리하고 API를 부르지 않아 토큰 없이도 연다
const photoToolRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tools/photo",
  component: lazyRouteComponent(() => import("./pages/PhotoTool"), "PhotoToolPage"),
});

const routeTree = rootRoute.addChildren([
  todayRoute,
  brainRoute,
  sourcesRoute,
  sourceRoute,
  experienceRoute,
  searchRoute,
  postingsRoute,
  postingRoute,
  manageRoute,
  usageRoute,
  settingsRoute,
  connectRoute,
  applicationsRoute,
  applicationRoute,
  documentRoute,
  sharedDocumentsRoute,
  sharedDocumentRoute,
  studyRoute,
  studyItemRoute,
  systemRoute,
  photoToolRoute,
]);

export const router = createRouter({
  routeTree,
  defaultPreload: false,
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
