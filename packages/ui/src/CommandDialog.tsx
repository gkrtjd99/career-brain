import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { cn } from "./cn";

export interface CommandDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 화면 읽기 프로그램용 제목 (보이지 않는다) */
  title: string;
  description?: string;
  children: ReactNode;
}

/**
 * 명령 팔레트 틀: 위쪽에 붙은 가운데 창. 좁은 화면에서는 화면 너비를 거의 다 쓰고 위에 붙어
 * 키보드가 올라와도 입력 칸이 가려지지 않는다. Esc·바깥 누르기로 닫힌다.
 */
export function CommandDialog({ open, onOpenChange, title, description, children }: CommandDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          className={cn(
            "fixed z-50 flex flex-col overflow-hidden rounded-xl border border-line bg-surface text-fg shadow-xl outline-none",
            "inset-x-2 top-2 max-h-[calc(100dvh-1rem)]",
            "sm:inset-x-auto sm:top-[12vh] sm:left-1/2 sm:max-h-[min(560px,76vh)] sm:w-[min(640px,calc(100vw-32px))] sm:-translate-x-1/2",
          )}
        >
          <Dialog.Title className="sr-only">{title}</Dialog.Title>
          <Dialog.Description className="sr-only">{description ?? title}</Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** 팔레트 안의 닫기 단추 (좁은 화면용) */
export const CommandDialogClose = Dialog.Close;
