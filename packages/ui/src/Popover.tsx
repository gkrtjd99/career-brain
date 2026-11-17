import * as PopoverPrimitive from "@radix-ui/react-popover";
import { type ComponentProps } from "react";
import { cn } from "./cn";

// shadcn/ui popover. 떠 있는 층이라 얇은 선 + 작은 그림자를 쓴다.

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;
export const PopoverClose = PopoverPrimitive.Close;

export function PopoverContent({
  className,
  align = "start",
  sideOffset = 4,
  ...props
}: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        collisionPadding={8}
        className={cn(
          "z-50 w-72 max-w-[calc(100vw-16px)] rounded-md border border-line bg-surface p-3 text-fg shadow-md outline-none",
          "origin-(--radix-popover-content-transform-origin) motion-safe:animate-pop-in",
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
