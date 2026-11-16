import { useEffect, useRef } from "react";
import { cn } from "./cn";

export interface HighlightProps {
  text: string;
  start: number;
  end: number;
  className?: string;
  /** 처음 그릴 때 강조 위치로 스크롤 */
  scrollIntoView?: boolean;
}

/** 원문 안의 근거 위치를 강조한다. 위치가 범위를 벗어나면 원문만 보여준다. */
export function Highlight({ text, start, end, className, scrollIntoView = true }: HighlightProps) {
  const ref = useRef<HTMLElement>(null);
  const valid = start >= 0 && end > start && end <= text.length;
  useEffect(() => {
    if (valid && scrollIntoView) ref.current?.scrollIntoView({ block: "center" });
  }, [valid, scrollIntoView, start]);
  return (
    <div className={cn("text-sm leading-7 break-words whitespace-pre-wrap", className)}>
      {valid ? (
        <>
          {text.slice(0, start)}
          <mark ref={ref} className="rounded-sm bg-accent-soft px-0.5 text-fg ring-1 ring-accent/40">
            {text.slice(start, end)}
          </mark>
          {text.slice(end)}
        </>
      ) : (
        text
      )}
    </div>
  );
}
