import type { ReferenceKind, ReferenceSummary } from "@career-brain/client";
import { Button, cn, FilterChips, Input, Select, SelectItem } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Loaded, PageHeader } from "../components/common";
import { AddReferenceSheet } from "../components/references/ReferenceForm";
import { api } from "../lib/api";
import { REFERENCE_KIND_LABEL, REFERENCE_KIND_ORDER, referenceMeta } from "../lib/references";

const route = getRouteApi("/brain/references");
const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";

/** 참고 › 참고 글: 다른 사람의 자기소개서·이력서·면접 후기·직무 글. 내 경험과 따로 둔다. */
export function ReferencesPage() {
  const search = route.useSearch();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(!!search.from);
  // 자료 넣기에서 "참고 글로 만들기"로 들어오면 만들기 시트를 연다
  useEffect(() => {
    if (search.from) setAdding(true);
  }, [search.from]);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(draft.trim()), 250);
    return () => clearTimeout(t);
  }, [draft]);
  const filter = { kind: search.type, company_id: search.company, year: search.year, q };
  const list = useQuery({
    queryKey: ["references", filter],
    queryFn: () => api.references(filter),
    placeholderData: keepPreviousData,
  });
  // 회사·연도 고르기는 이미 있는 참고 글에서만 (전체 목록 기준)
  const all = useQuery({ queryKey: ["references", {}], queryFn: () => api.references() });
  const companies = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of all.data ?? []) if (r.company_id && r.company_name) m.set(r.company_id, r.company_name);
    return [...m].sort((a, b) => a[1].localeCompare(b[1], "ko"));
  }, [all.data]);
  const years = useMemo(
    () => [...new Set((all.data ?? []).map((r) => r.year).filter((y): y is number => !!y))].sort((a, b) => b - a),
    [all.data],
  );
  const filtered = !!(search.type || search.company || search.year || q);
  const empty = all.data?.length === 0;
  const setSearch = (next: Partial<typeof search>) =>
    navigate({ to: "/brain/references", search: { ...search, from: undefined, ...next }, replace: true });
  const closeAdd = () => {
    setAdding(false);
    if (search.from) setSearch({ from: undefined });
  };

  return (
    <div>
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="참고 글"
        subtitle="남의 글이라 문서 근거로 쓰지 않습니다."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus size={15} /> 참고 글 추가
          </Button>
        }
      />
      {!empty ? (
        <div className="mb-6 space-y-3">
          <FilterChips<ReferenceKind | undefined>
            label="종류 필터"
            value={search.type}
            onChange={(type) => setSearch({ type })}
            items={[
              { value: undefined, label: "전체" },
              ...REFERENCE_KIND_ORDER.map((k) => ({ value: k, label: REFERENCE_KIND_LABEL[k] })),
            ]}
          />
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_12rem_8rem]">
            <Input
              type="search"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="제목·직무·글로 찾기"
              aria-label="참고 글 찾기"
              maxLength={200}
            />
            <Select
              aria-label="회사 필터"
              value={search.company ?? ""}
              onValueChange={(v) => setSearch({ company: v || undefined })}
            >
              <SelectItem value="">모든 회사</SelectItem>
              {companies.map(([id, name]) => (
                <SelectItem key={id} value={id}>
                  {name}
                </SelectItem>
              ))}
            </Select>
            <Select
              aria-label="연도 필터"
              value={search.year ? String(search.year) : ""}
              onValueChange={(v) => setSearch({ year: v ? Number(v) : undefined })}
              className="tabular-nums"
            >
              <SelectItem value="">모든 연도</SelectItem>
              {years.map((y) => (
                <SelectItem key={y} value={y}>
                  {y}년
                </SelectItem>
              ))}
            </Select>
          </div>
        </div>
      ) : null}
      <Loaded query={list}>
        {(rows) =>
          empty ? (
            <p className="border-t border-line pt-3 text-sm text-muted">
              아직 참고 글이 없습니다. 합격 자기소개서나 면접 후기를 붙여넣으면 문단 구성·강조 역량 같은 구조 메모로
              정리합니다. 원문 문장은 내 문서로 옮기지 않습니다.{" "}
              <button type="button" onClick={() => setAdding(true)} className="text-fg underline underline-offset-2">
                참고 글 추가
              </button>
            </p>
          ) : rows.length === 0 ? (
            <p className="border-t border-line pt-3 text-sm text-muted">
              맞는 참고 글이 없습니다.{" "}
              {filtered ? (
                <button
                  type="button"
                  onClick={() => {
                    setDraft("");
                    setSearch({ type: undefined, company: undefined, year: undefined });
                  }}
                  className="text-fg underline underline-offset-2"
                >
                  필터 초기화
                </button>
              ) : null}
            </p>
          ) : (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((r) => (
                <ReferenceRow key={r.id} r={r} withKind={!search.type} />
              ))}
            </ul>
          )
        }
      </Loaded>
      <AddReferenceSheet open={adding} onClose={closeAdd} sourceId={search.from} />
    </div>
  );
}

function ReferenceRow({ r, withKind }: { r: ReferenceSummary; withKind: boolean }) {
  const notes = r.notes_confirmed + r.notes_candidate;
  const meta = referenceMeta(r, withKind);
  return (
    <li>
      <Link
        to="/brain/references/$referenceId"
        params={{ referenceId: r.id }}
        className={cn(ROW, "flex items-center gap-4")}
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{r.title}</div>
          {meta ? <div className="line-clamp-2 text-[13px] text-muted tabular-nums">{meta}</div> : null}
        </div>
        <div className="shrink-0 text-right text-[13px] leading-tight tabular-nums">
          {r.notes_candidate ? <div className="font-semibold">확인 필요 {r.notes_candidate}</div> : null}
          <div className="text-muted">{notes ? `메모 ${notes}` : "메모 없음"}</div>
        </div>
      </Link>
    </li>
  );
}
