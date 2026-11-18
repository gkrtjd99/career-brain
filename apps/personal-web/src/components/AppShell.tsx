import { cn, Sheet, Tooltip, TooltipContent, TooltipTrigger } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  Brain,
  Briefcase,
  FileSearch,
  GraduationCap,
  House,
  ImageUp,
  KanbanSquare,
  Menu,
  Search,
  Settings,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, useToken } from "../lib/api";
import type { ManageTab } from "../pages/Manage";
import { setDisplayTimeZone, useDisplayTimeZone } from "../lib/dates";
import { IngestProvider } from "./GlobalActions";
import { CommandPalette } from "./palette/CommandPalette";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 연결·관리의 탭 하나로 바로 (설정) */
  tab?: ManageTab;
}

// 주 메뉴는 하루 일의 순서대로: 오늘 → 공고 → 지원 → 브레인 → 학습 (plan §13)
const MAIN: NavItem[] = [
  { to: "/", label: "오늘", icon: House },
  { to: "/postings", label: "공고", icon: Briefcase },
  { to: "/applications", label: "지원", icon: KanbanSquare },
  { to: "/brain", label: "브레인", icon: Brain },
  { to: "/study", label: "학습", icon: GraduationCap },
];
// 가끔 쓰는 도구: 사이드바 아래 작은 무리
const TOOLS: NavItem[] = [
  { to: "/tools/photo", label: "사진 변환", icon: ImageUp },
  { to: "/tools/hidden-text", label: "숨김 글 검사", icon: FileSearch },
];
// 관리 일(모델 연결·사용량·에이전트·작업·토큰·백업·설정)은 한 화면의 탭으로 (plan §21)
const MANAGE: NavItem = { to: "/manage", label: "연결·관리", icon: SlidersHorizontal };
// 설정(테마·시간대·넣은 자료 바로 정리·문서 검사 목록)은 연결·관리의 한 탭이지만 자주 찾으니 따로 둔다
const SETTINGS: NavItem = { to: "/manage", label: "설정", icon: Settings, tab: "settings" };
const MOBILE_TABS = MAIN.slice(0, 4);
const MORE = [...MAIN.slice(4), ...TOOLS, MANAGE, SETTINGS];

function isActive(path: string, to: string) {
  return to === "/" ? path === "/" : path === to || path.startsWith(`${to}/`);
}

/** 연결·관리와 설정은 같은 화면의 다른 탭: 탭으로 나눠 켠다 */
function navActive(path: string, tab: string | undefined, item: NavItem) {
  if (item.to !== "/manage") return isActive(path, item.to);
  if (path !== "/manage") return false;
  return item.tab ? tab === item.tab : tab !== "settings";
}

/** 단축키 표시: 맥은 ⌘K, 그 밖은 Ctrl K */
function shortcutLabel() {
  if (typeof navigator === "undefined") return "Ctrl K";
  const p = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform;
  return /mac|iphone|ipad/i.test(p) ? "⌘K" : "Ctrl K";
}

function SideLink({
  item,
  path,
  tab,
  onClick,
  quiet = false,
}: {
  item: NavItem;
  path: string;
  tab?: string;
  onClick?: () => void;
  /** 도구 무리: 한 단계 작게 */
  quiet?: boolean;
}) {
  const active = navActive(path, tab, item);
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      search={item.tab ? { tab: item.tab } : undefined}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2.5",
        quiet ? "h-9 text-[13px] md:h-8" : "h-9 text-sm",
        active
          ? "bg-subtle font-medium text-fg"
          : "text-muted transition-colors duration-150 hover:bg-subtle/60 hover:text-fg",
      )}
    >
      <Icon size={quiet ? 15 : 17} strokeWidth={active ? 2.2 : 1.8} />
      <span className="flex-1">{item.label}</span>
    </Link>
  );
}

export function AppShell() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const tab = useRouterState({
    select: (s) => {
      const t = (s.location.search as Record<string, unknown>).tab;
      return typeof t === "string" ? t : undefined;
    },
  });
  const token = useToken();
  const [more, setMore] = useState(false);
  const [palette, setPalette] = useState(false);
  const me = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, staleTime: 300_000 });
  // 공간 표시 시간대 (설정 화면). 모든 화면의 날짜 표시가 이 값을 쓴다 (lib/dates).
  const settings = useQuery({
    queryKey: ["workspace-settings", token],
    queryFn: api.workspaceSettings,
    enabled: !!token,
    staleTime: 300_000,
  });
  useEffect(() => setDisplayTimeZone(settings.data?.timezone), [settings.data?.timezone]);
  const zone = useDisplayTimeZone();
  const connected = !!token && path !== "/connect";

  // ⌘K / Ctrl+K: 어디서나 명령 팔레트 (입력 중이어도 연다. 다시 누르면 닫는다)
  useEffect(() => {
    if (!connected) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setMore(false);
        setPalette((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [connected]);

  // 첫 연결 화면과 페이지 보내기 창(c0043)은 메뉴 없이 그 일만 보여 준다.
  if (path === "/connect" || path === "/capture")
    return (
      <main className="min-h-dvh px-4 pb-12">
        <Outlet />
      </main>
    );

  const kbd = shortcutLabel();
  return (
    <IngestProvider>
      <div className="min-h-dvh md:flex">
        {/* 넓은 화면: 왼쪽 메뉴 */}
        <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-line px-3 py-4 md:flex">
          <Link to="/" className="mb-4 flex items-center gap-2 px-2.5">
            <span className="grid h-6 w-6 place-items-center rounded-md bg-accent text-[13px] font-bold text-accent-fg">
              C
            </span>
            <span className="font-semibold">Career Brain</span>
          </Link>
          {connected ? (
            <button
              type="button"
              onClick={() => setPalette(true)}
              aria-keyshortcuts="Meta+K Control+K"
              className="mb-4 flex h-9 items-center gap-2 rounded-md border border-line bg-surface px-2.5 text-[13px] text-muted transition-colors duration-150 hover:border-line-strong hover:text-fg"
            >
              <Search size={15} aria-hidden />
              <span className="flex-1 text-left">검색</span>
              <kbd className="font-sans text-[11px] tabular-nums">{kbd}</kbd>
            </button>
          ) : null}
          <nav className="space-y-0.5" aria-label="주 메뉴">
            {MAIN.map((it) => (
              <SideLink key={it.to} item={it} path={path} />
            ))}
          </nav>
          <div className="mt-auto">
            <nav aria-labelledby="nav-tools" className="mb-3 space-y-0.5">
              <h2 id="nav-tools" className="px-2.5 pb-1 text-xs text-muted">
                도구
              </h2>
              {TOOLS.map((it) => (
                <SideLink key={it.to} item={it} path={path} quiet />
              ))}
            </nav>
            <nav className="space-y-0.5 border-t border-line pt-3" aria-label="연결·관리">
              <SideLink item={MANAGE} path={path} tab={tab} />
              <SideLink item={SETTINGS} path={path} tab={tab} />
              {me.data ? <p className="truncate px-2.5 pt-2 text-xs text-muted">공간: {me.data.workspace.name}</p> : null}
            </nav>
          </div>
        </aside>

        {/* 좁은 화면: 위 막대 (이름 + 검색) */}
        <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-line bg-bg px-4 md:hidden">
          <Link to="/" className="flex min-h-9 items-center gap-2" aria-label="Career Brain 오늘">
            <span className="grid h-6 w-6 place-items-center rounded-md bg-accent text-[13px] font-bold text-accent-fg">
              C
            </span>
            <span className="text-[15px] font-semibold">Career Brain</span>
          </Link>
          {connected ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => setPalette(true)}
                  aria-label="검색과 이동"
                  className="-mr-2 ml-auto inline-flex size-10 items-center justify-center rounded-md text-fg hover:bg-subtle"
                >
                  <Search size={20} />
                </button>
              </TooltipTrigger>
              <TooltipContent>검색과 이동</TooltipContent>
            </Tooltip>
          ) : null}
        </header>

        <main className="min-w-0 flex-1 px-4 pt-5 pb-[calc(4.5rem+env(safe-area-inset-bottom))] md:px-8 md:pt-8 md:pb-12">
          <div className="mx-auto max-w-5xl">
            {/* 시간대가 바뀌면 화면을 다시 그려 날짜 표시를 맞춘다 */}
            <Outlet key={zone} />
          </div>
        </main>

        {/* 좁은 화면: 아래 탭 + 더보기 */}
        <nav
          aria-label="주 메뉴"
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-bg pb-[env(safe-area-inset-bottom)] md:hidden"
        >
          {MOBILE_TABS.map((it) => {
            const active = isActive(path, it.to);
            const Icon = it.icon;
            return (
              <Link
                key={it.to}
                to={it.to}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-14 flex-col items-center justify-center gap-0.5 text-[11px]",
                  active ? "font-medium text-fg" : "text-muted",
                )}
              >
                <Icon size={20} strokeWidth={active ? 2.2 : 1.8} />
                {it.label}
              </Link>
            );
          })}
          <button
            type="button"
            onClick={() => setMore(true)}
            aria-haspopup="dialog"
            className={cn(
              "flex h-14 flex-col items-center justify-center gap-0.5 text-[11px]",
              MORE.some((it) => navActive(path, tab, it)) ? "font-medium text-fg" : "text-muted",
            )}
          >
            <Menu size={20} />
            더보기
          </button>
        </nav>
        <Sheet open={more} onOpenChange={setMore} title="더보기">
          <div className="space-y-0.5">
            {MAIN.slice(4).map((it) => (
              <SideLink key={it.to} item={it} path={path} onClick={() => setMore(false)} />
            ))}
          </div>
          <div className="mt-4 space-y-0.5">
            <h2 className="px-2.5 pb-1 text-xs text-muted">도구</h2>
            {TOOLS.map((it) => (
              <SideLink key={it.to} item={it} path={path} onClick={() => setMore(false)} />
            ))}
          </div>
          <div className="mt-4 space-y-0.5 border-t border-line pt-3">
            <SideLink item={MANAGE} path={path} tab={tab} onClick={() => setMore(false)} />
            <SideLink item={SETTINGS} path={path} tab={tab} onClick={() => setMore(false)} />
          </div>
          {me.data ? <p className="mt-4 px-2.5 text-xs text-muted">공간: {me.data.workspace.name}</p> : null}
        </Sheet>
        {connected ? <CommandPalette open={palette} onOpenChange={setPalette} /> : null}
      </div>
    </IngestProvider>
  );
}
