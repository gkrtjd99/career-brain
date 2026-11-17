import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { Check, Minus } from "lucide-react";
import { forwardRef, type ComponentProps } from "react";
import { cn } from "./cn";

// shadcn/ui checkbox · radio-group · switch. 흑백: 켜짐 = 채운 검정(어둡게는 흰색).
// 눈에 보이는 상자는 16px 이지만, 감싼 <label> 줄이 누르는 자리(좁은 화면 36px)를 맡는다.

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export interface CheckboxProps extends Omit<ComponentProps<typeof CheckboxPrimitive.Root>, "onCheckedChange" | "checked"> {
  checked?: boolean | "indeterminate";
  onCheckedChange?: (checked: boolean) => void;
}

export const Checkbox = forwardRef<HTMLButtonElement, CheckboxProps>(function Checkbox(
  { className, onCheckedChange, ...props },
  ref,
) {
  return (
    <CheckboxPrimitive.Root
      ref={ref}
      onCheckedChange={onCheckedChange ? (c) => onCheckedChange(c === true) : undefined}
      className={cn(
        "peer grid size-4 shrink-0 place-items-center rounded-[4px] border border-line-strong bg-surface text-accent-fg transition-colors",
        "data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=indeterminate]:border-accent data-[state=indeterminate]:bg-accent",
        "disabled:cursor-not-allowed disabled:opacity-50",
        FOCUS,
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="grid place-items-center">
        {props.checked === "indeterminate" ? <Minus size={12} strokeWidth={3} /> : <Check size={12} strokeWidth={3} />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
});

export const RadioGroup = forwardRef<HTMLDivElement, ComponentProps<typeof RadioGroupPrimitive.Root>>(
  function RadioGroup({ className, ...props }, ref) {
    return <RadioGroupPrimitive.Root ref={ref} className={cn("grid gap-1", className)} {...props} />;
  },
);

export const RadioGroupItem = forwardRef<HTMLButtonElement, ComponentProps<typeof RadioGroupPrimitive.Item>>(
  function RadioGroupItem({ className, ...props }, ref) {
    return (
      <RadioGroupPrimitive.Item
        ref={ref}
        className={cn(
          "grid size-4 shrink-0 place-items-center rounded-full border border-line-strong bg-surface transition-colors",
          "data-[state=checked]:border-accent disabled:cursor-not-allowed disabled:opacity-50",
          FOCUS,
          className,
        )}
        {...props}
      >
        <RadioGroupPrimitive.Indicator className="size-2 rounded-full bg-accent" />
      </RadioGroupPrimitive.Item>
    );
  },
);

/** 바로 적용되는 켜고 끄기 설정에만 쓴다 (나중에 저장하는 폼 안의 선택은 Checkbox). */
export const Switch = forwardRef<HTMLButtonElement, ComponentProps<typeof SwitchPrimitive.Root>>(function Switch(
  { className, ...props },
  ref,
) {
  return (
    <SwitchPrimitive.Root
      ref={ref}
      className={cn(
        "inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent bg-line-strong/60 transition-colors",
        "data-[state=checked]:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
        FOCUS,
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        className={cn(
          "pointer-events-none block size-4 rounded-full bg-surface transition-transform duration-150",
          "translate-x-0.5 data-[state=checked]:translate-x-4 data-[state=checked]:bg-accent-fg",
        )}
      />
    </SwitchPrimitive.Root>
  );
});
