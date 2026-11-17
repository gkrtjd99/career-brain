import { cn, Sheet } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  Brain,
  Briefcase,
  Files,
  Gauge,
  GraduationCap,
  House,
  ImageUp,
  KanbanSquare,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, useToken } from "../lib/api";
import { setDisplayTimeZone, useDisplayTimeZone } from "../lib/dates";

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
  { to: "/tools/photo", label: "사진 변환", icon: ImageUp },
];
const SYSTEM: NavItem[] = [
  { to: "/system", label: "시스템", icon: ShieldCheck },
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
        active
          ? "bg-subtle font-medium text-fg"
          : "text-muted transition-colors duration-150 hover:bg-subtle/60 hover:text-fg",
      )}
    >
      <Icon size={17} strokeWidth={active ? 2.2 : 1.8} />
      <span className="flex-1">{item.label}</span>
      {item.coming ? <span className="text-xs text-muted">준비 중</span> : null}
    </Link>
  );
}

export function AppShell() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const token = useToken();
  const [more, setMore] = useState(false);
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

  // 첫 연결 화면은 메뉴 없이 그 일만 보여 준다.
  if (path === "/connect")
    return (
      <main className="min-h-dvh px-4 pb-12">
        <Outlet />
      </main>
    );

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
        <nav className="mt-auto space-y-0.5 border-t border-line pt-3" aria-label="연결·관리">
          <p className="px-2.5 pb-1 text-xs text-muted">연결·관리</p>
          {SYSTEM.map((it) => (
            <SideLink key={it.to} item={it} path={path} />
          ))}
          {me.data ? <p className="truncate px-2.5 pt-2 text-xs text-muted">공간: {me.data.workspace.name}</p> : null}
        </nav>
      </aside>

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
          onClick={() => setMore(true)}
          className={cn(
            "flex h-14 flex-col items-center justify-center gap-0.5 text-[11px]",
            [...MAIN.slice(4), ...SYSTEM].some((it) => isActive(path, it.to)) ? "font-medium text-fg" : "text-muted",
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
        <p className="mt-4 border-t border-line px-2.5 pt-3 pb-1 text-xs text-muted">연결·관리</p>
        <div className="space-y-0.5">
          {SYSTEM.map((it) => (
            <SideLink key={it.to} item={it} path={path} onClick={() => setMore(false)} />
          ))}
        </div>
        {me.data ? <p className="mt-4 px-2.5 text-xs text-muted">공간: {me.data.workspace.name}</p> : null}
      </Sheet>
    </div>
  );
}
