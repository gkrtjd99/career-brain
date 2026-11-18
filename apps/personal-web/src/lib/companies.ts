// 회사 조사 (O13) 화면 문구. 회사 항목은 참고 지식이다: 내 경험·근거와 말을 섞지 않는다.
import type { Company, CompanyFactKind, CompanyPage } from "@career-brain/client";
import { formatDate } from "./dates";
import { hostOf } from "./format";
import { FETCH_ERROR, noteLabel } from "./labels";

/** 화면에 보이는 순서 */
export const FACT_KIND_ORDER: CompanyFactKind[] = [
  "talent",
  "values",
  "business",
  "product",
  "finance",
  "culture",
  "news",
  "hiring",
  "other",
];

export const FACT_KIND_LABEL: Record<CompanyFactKind, string> = {
  talent: "인재상",
  values: "핵심 가치",
  business: "주요 사업",
  product: "제품·서비스",
  finance: "재무",
  culture: "조직 문화",
  news: "최근 소식",
  hiring: "채용 절차",
  other: "기타",
};

/** 기준일 "2024" → "2024년", "2024-06" → "2024년 6월", 날짜는 날짜 표시 */
export function asOfLabel(asOf: string | null): string | null {
  if (!asOf) return null;
  const [y, m, d] = asOf.split("-");
  if (d) return formatDate(asOf);
  if (m) return `${y}년 ${Number(m)}월`;
  return `${y}년`;
}

/** 원문 한 건의 이름: 붙여넣은 글은 제목(없으면 "붙여넣은 글"), 가져온 페이지는 제목이나 주소의 호스트 */
export function pageName(p: Pick<CompanyPage, "origin" | "title" | "url" | "final_url">): string {
  if (p.origin === "paste") return p.title || "붙여넣은 글";
  if (p.origin === "posting") return `공고 · ${p.title || hostOf(p.url ?? "")}`;
  return p.title || hostOf(p.final_url ?? p.url ?? "");
}

/** 회사 정보 찾기 한 줄: 찾는 중 · 마지막으로 찾은 날 · 홈페이지를 못 찾았는지 */
export function researchLine(c: Pick<Company, "research_status" | "researched_at" | "research_notes">): string | null {
  if (c.research_status === "pending") return "회사 정보를 찾는 중입니다.";
  if (c.research_status === "failed") return "회사 정보를 찾지 못했습니다. 다시 찾거나 원문을 직접 넣어 주세요.";
  if (c.research_status !== "done") return null;
  const when = c.researched_at ? `${formatDate(c.researched_at)}에 찾음` : "찾음";
  const notes = c.research_notes;
  if (notes.includes("homepage:none"))
    return `${when} · 홈페이지를 찾지 못했습니다. 수정에서 홈페이지를 넣고 다시 찾으면 소개·인재상 쪽을 가져옵니다.`;
  if (notes.some((n) => n.startsWith("homepage_failed:"))) return `${when} · 홈페이지를 열지 못했습니다.`;
  return when;
}

/** 원문 상태 한 줄 (가져오는 중·실패 이유·모델 메모) */
export function pageStatusLabel(p: CompanyPage): string {
  if (p.status === "pending") return "가져오는 중";
  if (p.status === "failed") return FETCH_ERROR[p.error_code ?? ""] ?? "가져오지 못함";
  if (p.notes.includes("no_model")) return "모델이 없어 원문만 저장했습니다. 항목은 직접 추가합니다.";
  return [p.candidates ? `후보 ${p.candidates}개` : "후보 없음", ...p.notes.map(noteLabel)].join(" · ");
}
