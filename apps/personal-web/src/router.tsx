import type { SearchKind } from "@career-brain/client";
import { createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect } from "@tanstack/react-router";
import { AppShell } from "./components/AppShell";
import { getToken } from "./lib/api";
import { BrainPage } from "./pages/Brain";
import { ApplicationsPage } from "./pages/Applications";
import { ExperiencePage } from "./pages/Experience";
import { NotFoundPage } from "./pages/NotFound";
import { PostingPage } from "./pages/Posting";
import { PostingsPage } from "./pages/Postings";
import { SearchPage } from "./pages/Search";
import { SettingsPage } from "./pages/Settings";
import type { SystemTab } from "./pages/System";
import type { StudyTab } from "./pages/Study";
import { TodayPage } from "./pages/Today";
import { UsagePage } from "./pages/Usage";

const rootRoute = createRootRoute({ component: AppShell, notFoundComponent: NotFoundPage });

/** 토큰이 없으면 설정 화면으로 */
function requireToken() {
  if (!getToken()) throw redirect({ to: "/settings", search: { reason: "missing" } });
}

const todayRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: requireToken,
  component: TodayPage,
});

const brainRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/brain",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): { review?: boolean } =>
    s.review === true || s.review === "true" || s.review === "1" ? { review: true } : {},
  component: BrainPage,
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
  validateSearch: (s: Record<string, unknown>): { q?: string; kinds?: string } => {
    const q = typeof s.q === "string" && s.q.trim() ? s.q : undefined;
    const kinds =
      typeof s.kinds === "string"
        ? s.kinds
            .split(",")
            .filter((k): k is SearchKind => KINDS.includes(k as SearchKind))
            .join(",") || undefined
        : undefined;
    return { ...(q ? { q } : {}), ...(kinds ? { kinds } : {}) };
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

const usageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/usage",
  beforeLoad: requireToken,
  component: UsagePage,
});

export const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  validateSearch: (s: Record<string, unknown>): { reason?: "missing" | "invalid" } =>
    s.reason === "missing" || s.reason === "invalid" ? { reason: s.reason } : {},
  component: SettingsPage,
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

const SYSTEM_TABS: SystemTab[] = ["runs", "approvals", "maintenance", "audit", "oauth"];
export const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  beforeLoad: requireToken,
  validateSearch: (s: Record<string, unknown>): { tab?: SystemTab; approval?: string; run?: string } => ({
    ...(SYSTEM_TABS.includes(s.tab as SystemTab) && s.tab !== "runs" ? { tab: s.tab as SystemTab } : {}),
    ...(typeof s.approval === "string" && s.approval ? { approval: s.approval } : {}),
    ...(typeof s.run === "string" && s.run ? { run: s.run } : {}),
  }),
  component: lazyRouteComponent(() => import("./pages/System"), "SystemPage"),
});

const routeTree = rootRoute.addChildren([
  todayRoute,
  brainRoute,
  experienceRoute,
  searchRoute,
  postingsRoute,
  postingRoute,
  usageRoute,
  settingsRoute,
  applicationsRoute,
  applicationRoute,
  documentRoute,
  sharedDocumentsRoute,
  sharedDocumentRoute,
  studyRoute,
  studyItemRoute,
  systemRoute,
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
