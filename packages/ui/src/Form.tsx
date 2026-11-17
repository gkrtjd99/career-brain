import {
  forwardRef,
  type InputHTMLAttributes,
  type LabelHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "./cn";

const FIELD =
  "w-full rounded-md border border-line-strong bg-surface px-3 text-sm text-fg placeholder:text-muted " +
  "focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-accent/30 disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(FIELD, "h-9", className)} {...props} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea ref={ref} className={cn(FIELD, "min-h-20 py-2 leading-6", className)} {...props} />
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, ...props }, ref) {
    return <select ref={ref} className={cn(FIELD, "h-9 pr-8", className)} {...props} />;
  },
);

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("block text-[13px] font-medium text-muted", className)} {...props} />;
}
