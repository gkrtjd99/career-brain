import { Badge, cn, Sheet } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  Brain,
  Briefcase,
  Files,
  Gauge,
  GraduationCap,
  House,
  KanbanSquare,
  Menu,
  Search,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { api, useToken } from "../lib/api";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  coming?: boolean;
}

const MAIN: NavItem[] = [
  { to: "/", label: "오늘", icon: House },
  { to: "/brain", label: "브레인", icon: Brain },
  { to: "/postings", label: "공고", icon: Briefcase },
  { to: "/search", label: "검색", icon: Search },
  { to: "/applications", label: "지원 현황", icon: KanbanSquare },
  { to: "/documents", label: "공용 문서", icon: Files },
  { to: "/study", label: "학습", icon: GraduationCap },
];
const SYSTEM: NavItem[] = [
  { to: "/usage", label: "사용량", icon: Gauge },
  { to: "/settings", label: "설정", icon: Settings },
];
const MOBILE_TABS = MAIN.slice(0, 4);

function isActive(path: string, to: string) {
  return to === "/" ? path === "/" : path === to || path.startsWith(`${to}/`);
}

function SideLink({ item, path, onClick }: { item: NavItem; path: string; onClick?: () => void }) {
  const active = isActive(path, item.to);
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      onClick={onClick}
      className={cn(
        "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm",
        active ? "bg-surface font-medium text-fg shadow-[0_0_0_1px_var(--app-line)]" : "text-muted hover:bg-subtle hover:text-fg",
      )}
    >
      <Icon size={17} strokeWidth={active ? 2.2 : 1.8} className={active ? "text-accent" : undefined} />
      <span className="flex-1">{item.label}</span>
      {item.coming ? <Badge tone="none">준비 중</Badge> : null}
    </Link>
  );
}

export function AppShell() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const token = useToken();
  const [more, setMore] = useState(false);
  const me = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, staleTime: 300_000 });

  return (
    <div className="min-h-dvh md:flex">
      {/* 넓은 화면: 왼쪽 메뉴 */}
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-line px-3 py-4 md:flex">
        <Link to="/" className="mb-5 flex items-center gap-2 px-2.5">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-accent text-[13px] font-bold text-accent-fg">
            C
          </span>
          <span className="font-semibold">Career Brain</span>
        </Link>
        <nav className="space-y-0.5" aria-label="주 메뉴">
          {MAIN.map((it) => (
            <SideLink key={it.to} item={it} path={path} />
          ))}
        </nav>
        <nav className="mt-auto space-y-0.5 border-t border-line pt-3" aria-label="시스템">
          {SYSTEM.map((it) => (
            <SideLink key={it.to} item={it} path={path} />
          ))}
          {me.data ? (
            <p className="truncate px-2.5 pt-2 text-xs text-muted">공간: {me.data.workspace.name}</p>
          ) : null}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 px-4 pt-5 pb-[calc(4.5rem+env(safe-area-inset-bottom))] md:px-8 md:pt-8 md:pb-12">
        <div className="mx-auto max-w-5xl">
          <Outlet />
        </div>
      </main>

      {/* 좁은 화면: 아래 탭 + 더보기 */}
      <nav
        aria-label="주 메뉴"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        {MOBILE_TABS.map((it) => {
          const active = isActive(path, it.to);
          const Icon = it.icon;
          return (
            <Link
              key={it.to}
              to={it.to}
              className={cn(
                "flex h-14 flex-col items-center justify-center gap-0.5 text-[11px]",
                active ? "font-medium text-accent" : "text-muted",
              )}
            >
              <Icon size={20} strokeWidth={active ? 2.2 : 1.8} />
              {it.label}
            </Link>
          );
        })}
        <button
          onClick={() => setMore(true)}
          className={cn(
            "flex h-14 flex-col items-center justify-center gap-0.5 text-[11px]",
            [...MAIN.slice(4), ...SYSTEM].some((it) => isActive(path, it.to))
              ? "font-medium text-accent"
              : "text-muted",
          )}
        >
          <Menu size={20} />
          더보기
        </button>
      </nav>
      <Sheet open={more} onOpenChange={setMore} title="더보기">
        <div className="space-y-0.5">
          {[...MAIN.slice(4), ...SYSTEM].map((it) => (
            <SideLink key={it.to} item={it} path={path} onClick={() => setMore(false)} />
          ))}
        </div>
        {me.data ? <p className="mt-4 px-2.5 text-xs text-muted">공간: {me.data.workspace.name}</p> : null}
      </Sheet>
    </div>
  );
}
