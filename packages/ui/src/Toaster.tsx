import type { CSSProperties } from "react";
import { Toaster as Sonner, toast, type ToasterProps } from "sonner";

// shadcn/ui sonner: 앱에 한 번 놓는 알림 자리. 색은 앱 토큰으로 맞춰 밝게·어둡게 모두 따라간다.
// 잠깐 알리는 일(저장함·삭제함·복사함)에만 쓴다. 계속 남아야 하는 상태는 화면 안 글로 둔다.

export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      position="bottom-center"
      duration={3000}
      gap={8}
      offset={16}
      mobileOffset={{ bottom: "calc(72px + env(safe-area-inset-bottom))" }}
      style={
        {
          "--normal-bg": "var(--app-fg)",
          "--normal-text": "var(--app-bg)",
          "--normal-border": "var(--app-fg)",
          "--border-radius": "0.375rem",
          "--width": "min(360px, calc(100vw - 32px))",
          fontFamily: "inherit",
        } as CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "py-2.5! px-3.5! text-[13px]! shadow-md! min-h-10!",
          description: "text-bg/70!",
          actionButton: "bg-bg! text-fg! font-medium! h-7! px-2.5! rounded-sm!",
        },
      }}
      {...props}
    />
  );
}

export { toast };
