import type { ExperienceKind, ExperienceStructure, ExperienceSummary, YearMonth } from "@career-brain/client";

export const EXPERIENCE_KINDS: ExperienceKind[] = ["work", "project", "education", "activity", "other"];

export const EXPERIENCE_KIND_LABEL: Record<ExperienceKind, string> = {
  work: "경력",
  project: "프로젝트",
  education: "교육",
  activity: "대외활동",
  other: "기타",
};

/** 부모로 고를 수 있는 종류 (경력 › 그 안의 프로젝트, 교육 › 그때 한 프로젝트) */
export const PARENT_KINDS: ExperienceKind[] = ["work", "education"];

/** "2024-03" → "2024.03" */
export function monthLabel(m: YearMonth | null): string | null {
  return m ? m.replace("-", ".") : null;
}

/** 월 단위 기간. 날짜가 없으면 원문 기간(period_text)을 그대로. */
export function periodLabel(
  e: Pick<ExperienceStructure, "started_on" | "ended_on" | "ongoing"> & { period_text?: string | null },
): string | null {
  const start = monthLabel(e.started_on);
  if (!start) return e.period_text ?? null;
  if (e.ongoing) return `${start} – 진행 중`;
  const end = monthLabel(e.ended_on);
  return end && end !== start ? `${start} – ${end}` : start;
}

export function confirmedCount(e: Pick<ExperienceSummary, "claim_counts">) {
  return (e.claim_counts.self_confirmed ?? 0) + (e.claim_counts.evidence_supported ?? 0);
}

export interface ExperienceNode {
  item: ExperienceSummary;
  children: ExperienceSummary[];
}

/**
 * 결과 안에서 부모 아래 자식을 묶는다. 부모가 결과에 없는 자식은 맨 위 행이 된다 (부모 이름을 같이 보인다).
 * 순서는 서버 정렬을 따른다.
 */
export function nest(items: ExperienceSummary[]): ExperienceNode[] {
  const present = new Set(items.map((e) => e.id));
  const kids = new Map<string, ExperienceSummary[]>();
  for (const e of items) {
    if (e.parent_id && present.has(e.parent_id)) {
      const list = kids.get(e.parent_id) ?? [];
      list.push(e);
      kids.set(e.parent_id, list);
    }
  }
  return items
    .filter((e) => !(e.parent_id && present.has(e.parent_id)))
    .map((item) => ({ item, children: kids.get(item.id) ?? [] }));
}

/** 시작 연도별로 (최근 먼저), 시작 달이 없는 것은 맨 끝 "기간 없음". */
export function groupByYear(nodes: ExperienceNode[]): { year: number | null; nodes: ExperienceNode[] }[] {
  const groups = new Map<number | null, ExperienceNode[]>();
  for (const n of nodes) {
    const year = n.item.started_on ? Number(n.item.started_on.slice(0, 4)) : null;
    const list = groups.get(year) ?? [];
    list.push(n);
    groups.set(year, list);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : b - a))
    .map(([year, list]) => ({ year, nodes: list }));
}

/** 쉼표·줄바꿈으로 나눈 태그. 서버가 다시 정규화한다 (30개·40자). */
export function splitTags(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[,\n]/)) {
    const t = raw.trim().replace(/\s+/g, " ");
    if (t && !seen.has(t.toLowerCase())) {
      seen.add(t.toLowerCase());
      out.push(t);
    }
  }
  return out;
}
