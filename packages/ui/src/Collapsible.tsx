import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import { ChevronRight } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "./cn";

// shadcn/ui collapsible + 화면에서 자주 쓰는 "접어 둔 자세히" 한 줄(Disclosure).

export const Collapsible = CollapsiblePrimitive.Root;
export const CollapsibleTrigger = CollapsiblePrimitive.Trigger;
export const CollapsibleContent = CollapsiblePrimitive.Content;

export interface DisclosureProps {
  /** 접힌 줄에 보이는 글 */
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** quiet: 작은 회색 글(기본) · strong: 본문 굵은 글 · row: 목록 줄 높이 */
  tone?: "quiet" | "strong" | "row";
  className?: string;
  triggerClassName?: string;
  contentClassName?: string;
}

const TRIGGER = {
  quiet: "min-h-9 gap-1 text-[13px] text-muted hover:text-fg sm:min-h-7",
  strong: "min-h-9 gap-1 text-[13px] font-medium text-fg sm:min-h-7",
  row: "-mx-2 min-h-11 w-[calc(100%+1rem)] gap-2 rounded-md px-2 text-sm font-medium hover:bg-subtle/60",
};

export function Disclosure({
  title,
  children,
  defaultOpen,
  open,
  onOpenChange,
  tone = "quiet",
  className,
  triggerClassName,
  contentClassName,
}: DisclosureProps) {
  return (
    <Collapsible open={open} defaultOpen={defaultOpen} onOpenChange={onOpenChange} className={className}>
      <CollapsibleTrigger
        className={cn(
          "flex items-center text-left transition-colors duration-150 data-[state=open]:[&>svg:first-child]:rotate-90",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          TRIGGER[tone],
          triggerClassName,
        )}
      >
        <ChevronRight
          size={14}
          aria-hidden
          className="shrink-0 text-muted transition-transform duration-150"
        />
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent className={contentClassName}>{children}</CollapsibleContent>
    </Collapsible>
  );
}
