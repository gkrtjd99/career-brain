import type { SourcePurpose } from "@career-brain/client";
import { cn } from "@career-brain/ui";
import { PURPOSE_OPTIONS } from "../../lib/labels";

/**
 * 원본 용도 고르기 (c0032). 올릴 때와 원본 화면에서 같은 말로 묻는다.
 * suggested: 파일 이름·형식으로 제안한 값 ("추천"을 붙인다). 고르는 것은 사람이다.
 */
export function PurposeChoice({
  name,
  value,
  onChange,
  suggested,
  disabled = false,
  hideLegend = false,
}: {
  name: string;
  value: SourcePurpose;
  onChange: (v: SourcePurpose) => void;
  suggested?: SourcePurpose | null;
  disabled?: boolean;
  /** 위에 같은 제목이 있으면 화면에서만 숨긴다 (읽기 프로그램은 그대로 읽는다) */
  hideLegend?: boolean;
}) {
  return (
    <fieldset disabled={disabled}>
      <legend className={hideLegend ? "sr-only" : "mb-1.5 text-sm font-medium"}>용도</legend>
      <div className="divide-y divide-line border-y border-line">
        {PURPOSE_OPTIONS.map((o) => (
          <div key={o.value} className="py-0.5">
            <label
              className={cn(
                "-mx-2 flex min-h-9 cursor-pointer items-start gap-3 rounded-md px-2 py-2.5 transition-colors duration-150",
                "hover:bg-subtle/60 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
                disabled && "cursor-default opacity-60 hover:bg-transparent",
              )}
            >
              <input
                type="radio"
                name={name}
                value={o.value}
                checked={value === o.value}
                onChange={() => onChange(o.value)}
                className="mt-1 size-4 shrink-0 accent-fg focus-visible:outline-none"
              />
              <span className="min-w-0">
                <span className={cn("text-sm", value === o.value ? "font-semibold" : "font-medium")}>{o.label}</span>
                {suggested === o.value ? <span className="ml-1.5 text-[13px] text-muted">추천</span> : null}
                <span className="block text-[13px] text-muted">{o.text}</span>
              </span>
            </label>
          </div>
        ))}
      </div>
    </fieldset>
  );
}
