import type { SearchKind } from "@career-brain/client";
import { createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { AppShell } from "./components/AppShell";
import { getToken } from "./lib/api";
import { BrainPage } from "./pages/Brain";
import { ComingPage } from "./pages/Coming";
import { ExperiencePage } from "./pages/Experience";
import { NotFoundPage } from "./pages/NotFound";
import { PostingPage } from "./pages/Posting";
import { PostingsPage } from "./pages/Postings";
import { SearchPage } from "./pages/Search";
import { SettingsPage } from "./pages/Settings";
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
  component: () => (
    <ComingPage
      title="지원 현황"
      milestone="O07"
      lines={[
        "관심 → 준비 중 → 제출 → 서류 → 면접 → 결과 칸반",
        "카드에는 회사·직무, D-day, 안 쓴 문항, 다음 행동만",
        "자기소개서 편집과 제출본 고정",
      ]}
    />
  ),
});

const studyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/study",
  component: () => (
    <ComingPage
      title="학습"
      milestone="O09"
      lines={["공고 요구 → 학습 항목 연결", "시험별 준비 목록", "오늘 복습할 오답"]}
    />
  ),
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
  studyRoute,
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
