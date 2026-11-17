// 작은 ⋯ 메뉴. 자주 쓰지 않는 일(철회·내보내기·설정)을 주 버튼 옆에서 치운다.
// role=menu + 화살표·Home·End·Esc. 바깥을 누르거나 고르면 닫고 버튼으로 포커스를 돌린다.
import { cn } from "@career-brain/ui";
import { MoreHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

export interface MoreMenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export function MoreMenu({ items, label = "더보기", className }: { items: MoreMenuItem[]; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    refs.current.find((r) => r && !r.disabled)?.focus();
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  const move = (e: KeyboardEvent) => {
    const enabled = refs.current.filter((r): r is HTMLButtonElement => !!r && !r.disabled);
    const at = enabled.indexOf(document.activeElement as HTMLButtonElement);
    const go = (i: number) => {
      e.preventDefault();
      enabled[(i + enabled.length) % enabled.length]?.focus();
    };
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(enabled.length - 1);
    else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") setOpen(false);
  };

  return (
    <div ref={root} className={cn("relative", className)}>
      <button
        ref={button}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className="grid h-9 w-9 place-items-center rounded-md border border-line text-muted transition-colors duration-150 hover:bg-subtle hover:text-fg sm:h-8 sm:w-8"
      >
        <MoreHorizontal size={16} />
      </button>
      {open ? (
        <div
          id={id}
          role="menu"
          aria-label={label}
          onKeyDown={move}
          className="absolute right-0 z-40 mt-1 min-w-40 rounded-md border border-line bg-surface py-1 shadow-lg"
        >
          {items.map((it, i) => (
            <button
              key={it.label}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={it.disabled}
              onClick={() => {
                close();
                it.onSelect();
              }}
              className={cn(
                "flex min-h-9 w-full items-center px-3 text-left text-[13px] whitespace-nowrap hover:bg-subtle focus:bg-subtle focus:outline-none disabled:opacity-40 sm:min-h-8",
                it.danger && "text-danger",
              )}
            >
              {it.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
