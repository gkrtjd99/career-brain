import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "./cn";

export interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** 넓은 화면에서 패널 너비 */
  width?: "md" | "lg";
}

/** 옆 패널. 넓은 화면에선 오른쪽, 좁은 화면에선 아래에서 올라온다. */
export function Sheet({ open, onOpenChange, title, description, children, width = "md" }: SheetProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          className={cn(
            "fixed z-50 flex flex-col bg-surface text-fg shadow-xl outline-none",
            "inset-x-0 bottom-0 max-h-[88dvh] rounded-t-xl border-t border-line",
            "md:inset-y-0 md:right-0 md:left-auto md:max-h-none md:rounded-none md:border-t-0 md:border-l",
            width === "lg" ? "md:w-[min(720px,92vw)]" : "md:w-[min(480px,92vw)]",
          )}
        >
          <div className="flex items-start gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-[15px] font-semibold">{title}</Dialog.Title>
              <Dialog.Description className={description ? "mt-0.5 text-xs text-muted" : "sr-only"}>
                {description ?? "상세 패널"}
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="-mr-1 rounded-md p-1.5 text-muted hover:bg-subtle hover:text-fg"
              aria-label="닫기"
            >
              <X size={18} />
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
            {children}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
