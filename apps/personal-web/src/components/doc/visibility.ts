// 요건 가시성 판정 이름 (용어집: "공고 요건이 글에 보이나요"). 값은 근거가 글에 보이는지이지 합격 가능성이 아니다.
import type { Visibility } from "@career-brain/client";
import type { BadgeTone } from "@career-brain/ui";

export const VIS_LABEL: Record<Visibility, { label: string; tone: BadgeTone }> = {
  met_visible: { label: "글에 보임", tone: "ok" },
  missing: { label: "경험은 있는데 글에 없음", tone: "warn" },
  overclaim_risk: { label: "과장 위험", tone: "danger" },
  unmet: { label: "경험 없음", tone: "none" },
  not_checkable: { label: "직접 확인", tone: "neutral" },
};

export function visLabel(v: string): { label: string; tone: BadgeTone } {
  return VIS_LABEL[v as Visibility] ?? { label: v, tone: "neutral" };
}
