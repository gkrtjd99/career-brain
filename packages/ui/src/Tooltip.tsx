import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { createContext, useContext, type ComponentProps } from "react";
import { cn } from "./cn";

// shadcn/ui tooltip. 앱은 TooltipProvider 를 한 번 놓는다 (툴팁 사이를 옮길 때 기다림 없이 바로 보인다).
// Provider 가 없는 곳에서 써도 깨지지 않게 그때만 따로 감싼다.
const HasProvider = createContext(false);

export function TooltipProvider({ delayDuration = 300, ...props }: ComponentProps<typeof TooltipPrimitive.Provider>) {
  return (
    <HasProvider.Provider value={true}>
      <TooltipPrimitive.Provider delayDuration={delayDuration} {...props} />
    </HasProvider.Provider>
  );
}

export function Tooltip(props: ComponentProps<typeof TooltipPrimitive.Root>) {
  const root = <TooltipPrimitive.Root {...props} />;
  return useContext(HasProvider) ? root : <TooltipProvider>{root}</TooltipProvider>;
}

export const TooltipTrigger = TooltipPrimitive.Trigger;

export function TooltipContent({
  className,
  sideOffset = 6,
  children,
  ...props
}: ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        sideOffset={sideOffset}
        collisionPadding={8}
        className={cn(
          "z-50 max-w-72 rounded-md bg-fg px-2 py-1 text-xs leading-5 text-bg break-words",
          "origin-(--radix-tooltip-content-transform-origin) motion-safe:animate-pop-in",
          className,
        )}
        {...props}
      >
        {children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
}
