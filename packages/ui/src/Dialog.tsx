import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { Button } from "./Button";
import { cn } from "./cn";

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  /** 무엇을 되돌릴 수 없는지 등 짧은 설명 */
  description?: ReactNode;
  children?: ReactNode;
  confirmLabel: ReactNode;
  cancelLabel?: ReactNode;
  onConfirm: () => void;
  /** 진행 중이면 버튼을 막는다 */
  busy?: boolean;
  tone?: "primary" | "danger";
  confirmDisabled?: boolean;
}

/** 되돌릴 수 없는 행동(제출·고정) 전에 묻는 가운데 창. 닫기·취소가 기본 초점이다. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel,
  cancelLabel = "취소",
  onConfirm,
  busy,
  tone = "primary",
  confirmDisabled,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex max-h-[88dvh] w-[min(560px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col",
            "rounded-lg border border-line bg-surface text-fg shadow-xl outline-none",
          )}
        >
          <div className="border-b border-line px-4 py-3">
            <Dialog.Title className="text-[15px] font-semibold">{title}</Dialog.Title>
            <Dialog.Description className={description ? "mt-1 text-[13px] text-muted" : "sr-only"}>
              {description ?? "확인"}
            </Dialog.Description>
          </div>
          {children ? <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div> : null}
          <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
            <Dialog.Close asChild>
              <Button autoFocus disabled={busy}>
                {cancelLabel}
              </Button>
            </Dialog.Close>
            <Button
              variant={tone === "danger" ? "danger" : "primary"}
              onClick={onConfirm}
              disabled={busy || confirmDisabled}
            >
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
