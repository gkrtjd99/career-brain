import * as TabsPrimitive from "@radix-ui/react-tabs";
import { type ComponentProps, type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "./cn";

// shadcn/ui tabs 를 밑줄 탭 모양으로. 보기 바꾸기는 언제나 이 모양 하나다.

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "flex gap-0.5 overflow-x-auto overflow-y-hidden border-b border-line [scrollbar-width:none] sm:gap-1",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "-mb-px inline-flex h-10 shrink-0 items-center border-b-2 border-transparent px-2 text-sm whitespace-nowrap text-muted tabular-nums sm:px-3",
        "transition-colors duration-150 hover:text-fg",
        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
        "data-[state=active]:border-fg data-[state=active]:font-medium data-[state=active]:text-fg",
        "disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("outline-none", className)} {...props} />;
}

export interface ViewTabsProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  items: { value: T; label: ReactNode }[];
  /** 탭 무리 이름 */
  label?: string;
  className?: string;
}

/**
 * 탭 줄만 그린다 (내용은 화면이 value 로 바꿔 그린다). Radix Tabs 위에서 화살표·Home·End 로 옮기면 바로 바뀐다.
 * 내용 패널이 따로 없어 aria-controls 는 달지 않는다.
 */
export function ViewTabs<T extends string>({ value, onChange, items, label, className }: ViewTabsProps<T>) {
  // 좁은 화면에서 탭 줄이 넘치면: 고른 탭을 보이게 옮기고, 더 있는 쪽 끝을 흐리게 해 밀어 볼 수 있음을 보인다
  const list = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState({ left: false, right: false });
  useEffect(() => {
    const el = list.current;
    if (!el) return;
    const update = () =>
      setMore({ left: el.scrollLeft > 1, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1 });
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [items.length]);
  useEffect(() => {
    const el = list.current?.querySelector<HTMLElement>('[data-state="active"]');
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [value]);
  const fade =
    more.left && more.right
      ? "[mask-image:linear-gradient(to_right,transparent,black_2rem,black_calc(100%-2rem),transparent)]"
      : more.right
        ? "[mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)]"
        : more.left
          ? "[mask-image:linear-gradient(to_right,transparent,black_2rem)]"
          : "";
  return (
    <Tabs value={value} onValueChange={(v) => onChange(v as T)} activationMode="automatic">
      <TabsList ref={list} aria-label={label} className={cn(fade, className)}>
        {items.map((it) => (
          <TabsTrigger key={it.value} value={it.value} aria-controls={undefined}>
            {it.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
