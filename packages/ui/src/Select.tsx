import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import {
  Children,
  forwardRef,
  isValidElement,
  type ComponentProps,
  type ReactElement,
  type ReactNode,
} from "react";
import { cn } from "./cn";
import { FIELD } from "./Form";

/*
 * shadcn/ui select (Radix). 부품은 SelectRoot·SelectTrigger·SelectValue·SelectContent·SelectItem…
 * 대부분의 칸은 짧은 `Select` 하나로 쓴다:
 *
 *   <Select id="kind" value={kind} onValueChange={setKind}>
 *     <SelectItem value="">고르지 않음</SelectItem>
 *     <SelectItem value="a">A</SelectItem>
 *   </Select>
 *
 * Radix 는 빈 문자열 값을 쓰지 못한다 (자리표시 글로 쓴다). 그래서 value="" 항목은 안에서 다른 값으로 바꿔
 * 그대로 고를 수 있게 한다. 숫자 값도 받는다 (onValueChange 는 언제나 문자열).
 */

const EMPTY = "__empty__";
const enc = (v: string | number) => (v === "" ? EMPTY : String(v));
const dec = (v: string) => (v === EMPTY ? "" : v);

export const SelectRoot = SelectPrimitive.Root;
export const SelectGroup = SelectPrimitive.Group;
export const SelectValue = SelectPrimitive.Value;

export const SelectTrigger = forwardRef<HTMLButtonElement, ComponentProps<typeof SelectPrimitive.Trigger>>(
  function SelectTrigger({ className, children, ...props }, ref) {
    return (
      <SelectPrimitive.Trigger
        ref={ref}
        className={cn(
          FIELD,
          "flex h-9 items-center justify-between gap-2 text-left whitespace-nowrap",
          "data-[placeholder]:text-muted [&>span]:min-w-0 [&>span]:truncate",
          className,
        )}
        {...props}
      >
        {children}
        <SelectPrimitive.Icon asChild>
          <ChevronDown size={15} aria-hidden className="shrink-0 text-muted" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
    );
  },
);

export function SelectContent({
  className,
  children,
  position = "popper",
  ...props
}: ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position={position}
        sideOffset={4}
        collisionPadding={8}
        className={cn(
          "relative z-50 min-w-32 overflow-hidden rounded-md border border-line bg-surface text-fg shadow-md",
          "motion-safe:animate-pop-in",
          position === "popper" &&
            "max-h-(--radix-select-content-available-height) w-full min-w-(--radix-select-trigger-width) origin-(--radix-select-content-transform-origin)",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center text-muted">
          <ChevronUp size={14} />
        </SelectPrimitive.ScrollUpButton>
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
        <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center text-muted">
          <ChevronDown size={14} />
        </SelectPrimitive.ScrollDownButton>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export interface SelectItemProps extends Omit<ComponentProps<typeof SelectPrimitive.Item>, "value"> {
  value: string | number;
}

export const SelectItem = forwardRef<HTMLDivElement, SelectItemProps>(function SelectItem(
  { className, children, value, ...props },
  ref,
) {
  return (
    <SelectPrimitive.Item
      ref={ref}
      value={enc(value)}
      className={cn(
        "relative flex min-h-9 w-full cursor-default items-center rounded-sm py-1.5 pr-8 pl-2 text-sm outline-none select-none sm:min-h-8",
        "data-[highlighted]:bg-subtle data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <span className="absolute right-2 flex size-4 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check size={14} />
        </SelectPrimitive.ItemIndicator>
      </span>
    </SelectPrimitive.Item>
  );
});

export function SelectLabel({ className, ...props }: ComponentProps<typeof SelectPrimitive.Label>) {
  return <SelectPrimitive.Label className={cn("px-2 pt-2 pb-1 text-xs text-muted", className)} {...props} />;
}

export function SelectSeparator({ className, ...props }: ComponentProps<typeof SelectPrimitive.Separator>) {
  return <SelectPrimitive.Separator className={cn("-mx-1 my-1 h-px bg-line", className)} {...props} />;
}

export interface SelectProps {
  value: string | number;
  onValueChange: (value: string) => void;
  children: ReactNode;
  id?: string;
  disabled?: boolean;
  /** 고른 값이 없을 때(빈 값 항목도 없을 때) 보이는 글 */
  placeholder?: string;
  className?: string;
  contentClassName?: string;
  name?: string;
  required?: boolean;
  "aria-label"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

function hasEmptyItem(children: ReactNode): boolean {
  let found = false;
  Children.forEach(children, (child) => {
    if (found || !isValidElement(child)) return;
    const el = child as ReactElement<{ value?: unknown; children?: ReactNode }>;
    if (el.type === SelectItem && el.props.value === "") found = true;
    else if (el.props.children) found = hasEmptyItem(el.props.children);
  });
  return found;
}

/** 짧게 쓰는 고르기 칸: 트리거 + 목록. 항목은 SelectItem (무리는 SelectGroup·SelectLabel). */
export const Select = forwardRef<HTMLButtonElement, SelectProps>(function Select(
  { value, onValueChange, children, id, disabled, placeholder, className, contentClassName, name, required, ...aria },
  ref,
) {
  const shown = value === "" ? (hasEmptyItem(children) ? EMPTY : "") : String(value);
  return (
    <SelectRoot value={shown} onValueChange={(v) => onValueChange(dec(v))} disabled={disabled} name={name} required={required}>
      <SelectTrigger ref={ref} id={id} className={className} {...aria}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className={contentClassName}>{children}</SelectContent>
    </SelectRoot>
  );
});
