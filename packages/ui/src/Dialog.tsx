import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import { type ComponentProps, type ReactNode } from "react";
import { Button } from "./Button";
import { cn } from "./cn";

// shadcn/ui alert-dialog. 바깥을 눌러도 닫히지 않고, 열리면 취소에 초점이 간다.

export const AlertDialog = AlertDialogPrimitive.Root;
export const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
export const AlertDialogCancel = AlertDialogPrimitive.Cancel;
export const AlertDialogAction = AlertDialogPrimitive.Action;

export function AlertDialogContent({ className, children, ...props }: ComponentProps<typeof AlertDialogPrimitive.Content>) {
  return (
    <AlertDialogPrimitive.Portal>
      <AlertDialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 motion-safe:animate-fade-in" />
      <AlertDialogPrimitive.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 flex max-h-[88dvh] w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col",
          "rounded-lg border border-line bg-surface text-fg shadow-xl outline-none motion-safe:animate-pop-in",
          className,
        )}
        {...props}
      >
        {children}
      </AlertDialogPrimitive.Content>
    </AlertDialogPrimitive.Portal>
  );
}

export const AlertDialogTitle = AlertDialogPrimitive.Title;
export const AlertDialogDescription = AlertDialogPrimitive.Description;

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

/** 되돌릴 수 없는 행동(제출·고정·삭제) 전에 묻는 가운데 창. AlertDialog 위에 있고 취소가 기본 초점이다. */
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
    <AlertDialog open={open} onOpenChange={(o) => (busy && !o ? null : onOpenChange(o))}>
      <AlertDialogContent>
        <div className="px-5 pt-5 pb-1">
          <AlertDialogTitle className="text-[15px] font-semibold">{title}</AlertDialogTitle>
          <AlertDialogDescription className={description ? "mt-1.5 text-[13px] leading-5 text-muted" : "sr-only"}>
            {description ?? "확인"}
          </AlertDialogDescription>
        </div>
        {children ? <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3 text-[13px]">{children}</div> : null}
        <div className="flex justify-end gap-2 px-5 pt-3 pb-5">
          <AlertDialogCancel asChild>
            <Button disabled={busy}>{cancelLabel}</Button>
          </AlertDialogCancel>
          <Button variant={tone === "danger" ? "danger" : "primary"} onClick={onConfirm} disabled={busy || confirmDisabled}>
            {confirmLabel}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
