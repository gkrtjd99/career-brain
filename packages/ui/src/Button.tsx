import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "./cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "./Tooltip";

// shadcn/ui 의 button 과 같은 cva 구성. 색은 앱 토큰만 쓴다.
export const buttonVariants = cva(
  [
    "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    "disabled:pointer-events-none disabled:opacity-50",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-fg hover:bg-accent/90",
        secondary: "border border-line bg-surface text-fg hover:bg-subtle",
        ghost: "text-fg hover:bg-subtle",
        danger: "border border-line bg-surface text-danger hover:bg-danger-soft",
      },
      size: {
        sm: "h-9 px-2.5 text-[13px] sm:h-8",
        md: "h-9 px-3.5 text-sm",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export type ButtonVariant = NonNullable<VariantProps<typeof buttonVariants>["variant"]>;
export type ButtonSize = NonNullable<VariantProps<typeof buttonVariants>["size"]>;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, className, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});

export interface IconButtonProps extends Omit<ButtonProps, "size" | "aria-label" | "children"> {
  /** 화면 읽기 이름이자 툴팁 글 */
  label: string;
  children: ReactNode;
  /** 좁은 화면 36px · 넓은 화면 32px */
  size?: "icon" | "sm";
  side?: "top" | "right" | "bottom" | "left";
}

/** 아이콘만 있는 단추: aria-label 과 같은 글의 툴팁을 함께 단다. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, children, size = "icon", variant = "ghost", side = "top", className, ...props },
  ref,
) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          ref={ref}
          variant={variant}
          size="icon"
          aria-label={label}
          className={cn(size === "sm" && "sm:h-8 sm:w-8", variant === "ghost" && "text-muted hover:text-fg", className)}
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  );
});
