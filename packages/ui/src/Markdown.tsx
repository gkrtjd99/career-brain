import type { ReactNode } from "react";
import { cn } from "./cn";

const HEADING = [
  "",
  "text-xl font-semibold",
  "pt-2 text-base font-semibold",
  "text-sm font-semibold",
  "text-sm font-medium",
];

interface Item {
  text: string;
  children: string[];
}

/**
 * 위키처럼 코드가 만든 단순한 Markdown만 그린다:
 * 제목, 한 단계 중첩 목록, 인용, 문단, **굵게**, _기울임_, `코드`.
 * HTML은 해석하지 않는다 (모든 글자를 텍스트 노드로 넣는다).
 */
export function MarkdownView({ source, className }: { source: string; className?: string }) {
  const out: ReactNode[] = [];
  let list: Item[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) {
      out.push(<p key={out.length}>{inline(para.join(" "))}</p>);
      para = [];
    }
    if (list.length) {
      const items = list;
      out.push(
        <ul key={out.length} className="list-disc space-y-2 pl-5">
          {items.map((item, i) => (
            <li key={i}>
              {inline(item.text)}
              {item.children.length ? (
                <ul className="mt-1 space-y-1 text-[13px] text-muted">
                  {item.children.map((c, j) => (
                    <li key={j}>{quoteOrText(c)}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const li = /^(\s*)[-*]\s+(.*)$/.exec(line);
    if (h) {
      flush();
      const level = h[1]!.length;
      out.push(
        <div key={out.length} role="heading" aria-level={level} className={HEADING[level]}>
          {inline(h[2]!)}
        </div>,
      );
    } else if (li) {
      if (para.length) flush();
      const nested = li[1]!.length > 0 && list.length > 0;
      if (nested) list[list.length - 1]!.children.push(li[2]!);
      else list.push({ text: li[2]!, children: [] });
    } else if (line.startsWith(">")) {
      flush();
      out.push(
        <blockquote key={out.length} className="border-l-2 border-line pl-3 text-muted">
          {inline(line.replace(/^>\s?/, ""))}
        </blockquote>,
      );
    } else if (!line.trim()) {
      flush();
    } else {
      if (list.length) flush();
      para.push(line.trim());
    }
  }
  flush();
  return <div className={cn("space-y-3 text-[15px] leading-7", className)}>{out}</div>;
}

function quoteOrText(text: string): ReactNode {
  if (!text.startsWith(">")) return inline(text);
  return (
    <span className="block border-l-2 border-accent/50 pl-2">{inline(text.replace(/^>\s?/, ""))}</span>
  );
}

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\b_[^_]+_\b|(?<!\w)_\([^)]*\)_)/g).map((part, i) => {
    if (!part) return null;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**"))
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`"))
      return (
        <code key={i} className="rounded bg-subtle px-1 text-[13px]">
          {part.slice(1, -1)}
        </code>
      );
    if (part.length > 2 && part.startsWith("_") && part.endsWith("_"))
      return (
        <em key={i} className="text-[13px] text-muted not-italic">
          {part.slice(1, -1)}
        </em>
      );
    return part;
  });
}
