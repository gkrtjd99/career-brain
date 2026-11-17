import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, MoreHorizontal } from "lucide-react";
import { forwardRef, useRef, useState, type ComponentProps, type ReactNode } from "react";
import { cn } from "./cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "./Tooltip";

// shadcn/ui dropdown-menu. 화살표·Home·End·글자 찾기·Esc, 닫으면 단추로 초점이 돌아간다.

export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export const DropdownMenuGroup = MenuPrimitive.Group;

export function DropdownMenuContent({
  className,
  sideOffset = 4,
  align = "end",
  ...props
}: ComponentProps<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        sideOffset={sideOffset}
        align={align}
        collisionPadding={8}
        className={cn(
          "z-50 max-h-(--radix-dropdown-menu-content-available-height) min-w-40 overflow-y-auto rounded-md border border-line bg-surface p-1 text-fg shadow-md",
          "origin-(--radix-dropdown-menu-content-transform-origin) motion-safe:animate-pop-in",
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

const ITEM =
  "relative flex min-h-9 cursor-default items-center gap-2 rounded-sm px-2 text-[13px] whitespace-nowrap outline-none select-none sm:min-h-8 " +
  "data-[highlighted]:bg-subtle data-[disabled]:pointer-events-none data-[disabled]:opacity-40 [&_svg]:shrink-0 [&_svg]:text-muted";

export function DropdownMenuItem({
  className,
  danger,
  ...props
}: ComponentProps<typeof MenuPrimitive.Item> & { danger?: boolean }) {
  return <MenuPrimitive.Item className={cn(ITEM, danger && "text-danger [&_svg]:text-danger", className)} {...props} />;
}

export function DropdownMenuCheckboxItem({
  className,
  children,
  ...props
}: ComponentProps<typeof MenuPrimitive.CheckboxItem>) {
  return (
    <MenuPrimitive.CheckboxItem className={cn(ITEM, "pr-8", className)} {...props}>
      {children}
      <span className="absolute right-2 flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <Check size={14} />
        </MenuPrimitive.ItemIndicator>
      </span>
    </MenuPrimitive.CheckboxItem>
  );
}

export function DropdownMenuLabel({ className, ...props }: ComponentProps<typeof MenuPrimitive.Label>) {
  return <MenuPrimitive.Label className={cn("px-2 pt-2 pb-1 text-xs text-muted", className)} {...props} />;
}

export function DropdownMenuSeparator({ className, ...props }: ComponentProps<typeof MenuPrimitive.Separator>) {
  return <MenuPrimitive.Separator className={cn("-mx-1 my-1 h-px bg-line", className)} {...props} />;
}

export interface MoreMenuItem {
  label: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** 앞에 구분선 */
  separated?: boolean;
}

/** 자주 쓰지 않는 일을 주 버튼 옆에서 치우는 ⋯ 메뉴. */
export const MoreMenu = forwardRef<
  HTMLButtonElement,
  { items: (MoreMenuItem | false | null | undefined)[]; label?: string; className?: string; children?: ReactNode }
>(function MoreMenu({ items, label = "더보기", className, children }, ref) {
  const shown = items.filter((it): it is MoreMenuItem => !!it);
  // 메뉴를 닫으면 초점이 단추로 돌아온다. 그때 툴팁이 다시 뜨지 않게 잠깐 막는다.
  const [tip, setTip] = useState(false);
  const quiet = useRef(false);
  return (
    <DropdownMenu
      modal={false}
      onOpenChange={(open) => {
        setTip(false);
        quiet.current = true;
        if (!open) window.setTimeout(() => (quiet.current = false), 300);
      }}
    >
      <Tooltip open={tip} onOpenChange={(o) => setTip(o && !quiet.current)}>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger
            ref={ref}
            aria-label={label}
            className={cn(
              "grid h-9 w-9 shrink-0 place-items-center rounded-md border border-line text-muted transition-colors duration-150",
              "hover:bg-subtle hover:text-fg data-[state=open]:bg-subtle data-[state=open]:text-fg sm:h-8 sm:w-8",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              className,
            )}
          >
            <MoreHorizontal size={16} />
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent>
        {children}
        {shown.map((it, i) => (
          <MoreMenuRow key={i} item={it} first={i === 0 && !children} />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
});

function MoreMenuRow({ item, first }: { item: MoreMenuItem; first: boolean }) {
  return (
    <>
      {item.separated && !first ? <DropdownMenuSeparator /> : null}
      <DropdownMenuItem danger={item.danger} disabled={item.disabled} onSelect={item.onSelect}>
        {item.label}
      </DropdownMenuItem>
    </>
  );
}
