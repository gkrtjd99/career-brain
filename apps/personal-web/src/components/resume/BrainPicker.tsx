import type { ResumeItem, ResumeOperation, ResumePicker, ResumeSectionKind } from "@career-brain/client";
import { Button, FilterChips, Input, Label, Sheet, Textarea } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Loaded } from "../common";
import { api } from "../../lib/api";
import { SECTION_LABEL } from "../../lib/resume";

type Source = "experience" | "education" | "credential" | "skill" | "profile" | "manual";

/** 구역마다 고를 수 있는 브레인 기록 (서버 ALLOWED_REFS와 같다) */
const ALLOWED: Record<ResumeSectionKind, Source[]> = {
  profile: ["profile"],
  work: ["experience", "manual"],
  project: ["experience", "manual"],
  activities: ["experience", "manual"],
  publications: ["experience", "manual"],
  education: ["education", "experience", "manual"],
  skills: ["skill", "manual"],
  credentials: ["credential", "manual"],
  awards: ["credential", "manual"],
};

const SOURCE_LABEL: Record<Source, string> = {
  experience: "경험",
  education: "학력",
  credential: "자격·수상",
  skill: "기술",
  profile: "프로필",
  manual: "직접 쓰기",
};

const ROW = "-mx-2 flex min-h-9 w-[calc(100%+1rem)] items-start gap-3 rounded-md px-2 py-2.5 text-left transition-colors duration-150 hover:bg-subtle/60 disabled:opacity-50 disabled:hover:bg-transparent";

function matches(q: string, ...parts: (string | null | undefined)[]) {
  if (!q) return true;
  const needle = q.toLowerCase();
  return parts.some((p) => p?.toLowerCase().includes(needle));
}

/** 항목 더하기: 브레인 기록을 찾아 고른다. 고른 기록의 지금 값이 복사되고, 경험이면 확인한 사실을 줄로 같이 넣을 수 있다. */
export function ItemPicker({
  versionId,
  section,
  onClose,
  onAdd,
  busy,
  error,
}: {
  versionId: string;
  section: ResumeSectionKind;
  onClose: () => void;
  onAdd: (ops: ResumeOperation[]) => void;
  busy: boolean;
  error: string | null;
}) {
  const sources = ALLOWED[section];
  const [source, setSource] = useState<Source>(sources[0]!);
  const [q, setQ] = useState("");
  const [withClaims, setWithClaims] = useState(true);
  const [manual, setManual] = useState({ title: "", organization: "", role: "", period_text: "", detail: "" });
  const picker = useQuery({ queryKey: ["resume", versionId, "picker"], queryFn: () => api.resumePicker(versionId) });
  const add = (ref: { type: string; id: string }, claimIds?: string[]) =>
    onAdd([{ op: "add_item", section, ref, ...(claimIds?.length ? { claim_ids: claimIds.slice(0, 30) } : {}) }]);

  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${SECTION_LABEL[section]}에 더하기`}
      description="브레인 기록의 지금 값을 복사합니다. 브레인에서 바뀌면 '브레인 값이 바뀜'으로 알려 줍니다."
      width="lg"
    >
      {sources.length > 1 ? (
        <FilterChips
          label="가져올 곳"
          value={source}
          onChange={setSource}
          items={sources.map((s) => ({ value: s, label: SOURCE_LABEL[s] }))}
          className="mb-3"
        />
      ) : null}
      {source === "manual" ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            const fields = Object.fromEntries(
              Object.entries(manual).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]),
            );
            onAdd([{ op: "add_item", section, fields }]);
          }}
        >
          <p className="text-[13px] text-muted">직접 쓴 항목은 브레인 기록과 잇지 않습니다. 줄은 '근거 없음'으로 표시합니다.</p>
          {(
            [
              ["title", "제목", true],
              ["organization", "기관·회사", false],
              ["role", "역할", false],
              ["period_text", "기간", false],
            ] as const
          ).map(([k, label, required]) => (
            <div key={k}>
              <Label htmlFor={`mi-${k}`} className="mb-1">
                {label}
              </Label>
              <Input
                id={`mi-${k}`}
                value={manual[k]}
                required={required}
                maxLength={k === "title" ? 200 : 120}
                onChange={(e) => setManual({ ...manual, [k]: e.target.value })}
              />
            </div>
          ))}
          <div>
            <Label htmlFor="mi-detail" className="mb-1">
              설명
            </Label>
            <Textarea id="mi-detail" rows={2} maxLength={500} value={manual.detail} onChange={(e) => setManual({ ...manual, detail: e.target.value })} />
          </div>
          {error ? (
            <p role="alert" className="text-[13px] text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex gap-2 border-t border-line pt-3">
            <Button type="submit" variant="primary" disabled={busy || !manual.title.trim()}>
              더하기
            </Button>
            <Button variant="ghost" onClick={onClose}>
              닫기
            </Button>
          </div>
        </form>
      ) : (
        <Loaded query={picker}>
          {(p) => (
            <div className="space-y-3">
              {source !== "profile" ? (
                <div className="relative">
                  <Search size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted" />
                  <Input
                    aria-label="찾기"
                    placeholder="이름·기관으로 찾기"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    className="pl-8"
                  />
                </div>
              ) : null}
              {source === "experience" ? (
                <label className="flex min-h-9 items-center gap-2 text-[13px]">
                  <input type="checkbox" checked={withClaims} onChange={(e) => setWithClaims(e.target.checked)} />
                  확인한 사실을 성과 줄로 같이 넣기
                </label>
              ) : null}
              {error ? (
                <p role="alert" className="text-[13px] text-danger">
                  {error}
                </p>
              ) : null}
              <SourceList p={p} source={source} q={q} busy={busy} onPick={(ref, claims) => add(ref, withClaims ? claims : undefined)} />
            </div>
          )}
        </Loaded>
      )}
    </Sheet>
  );
}

function SourceList({
  p,
  source,
  q,
  busy,
  onPick,
}: {
  p: ResumePicker;
  source: Source;
  q: string;
  busy: boolean;
  onPick: (ref: { type: string; id: string }, claims?: string[]) => void;
}) {
  const rows = useMemo(() => {
    switch (source) {
      case "experience":
        return p.experiences
          .filter((e) => matches(q, e.title, e.organization, e.role))
          .map((e) => ({
            key: e.id,
            title: e.title,
            sub: [e.organization, e.role, e.period, e.claims.length ? `확인한 사실 ${e.claims.length}` : "확인한 사실 없음"]
              .filter(Boolean)
              .join(" · "),
            added: e.added,
            ref: { type: "experience", id: e.id },
            claims: e.claims.map((c) => c.id),
          }));
      case "education":
        return p.education
          .filter((e) => matches(q, e.school, e.major))
          .map((e) => ({
            key: e.id,
            title: e.school,
            sub: [e.major, e.period].filter(Boolean).join(" · "),
            added: e.added,
            ref: { type: "education", id: e.id },
          }));
      case "credential":
        return p.credentials
          .filter((c) => matches(q, c.name, c.issuer))
          .map((c) => ({
            key: c.id,
            title: c.name,
            sub: [c.issuer, c.acquired_on?.slice(0, 7)].filter(Boolean).join(" · "),
            added: c.added,
            ref: { type: "credential", id: c.id },
          }));
      case "skill":
        return p.skills
          .filter((s) => matches(q, s.name))
          .map((s) => ({ key: s.key, title: s.name, sub: "", added: s.added, ref: { type: "skill", id: s.key } }));
      case "profile":
        return p.profile
          ? [
              {
                key: p.profile.id,
                title: p.profile.name ?? "이름 없음",
                sub: "이름·연락처·링크 (사진·생년월일·주소는 판본에서 켭니다)",
                added: p.profile.added,
                ref: { type: "profile", id: p.profile.id },
              },
            ]
          : [];
      default:
        return [];
    }
  }, [p, source, q]);
  if (!rows.length)
    return (
      <p className="border-t border-line pt-3 text-[13px] text-muted">
        {q ? "찾는 기록이 없습니다." : "브레인에 이 종류의 기록이 아직 없습니다."}
      </p>
    );
  return (
    <ul className="divide-y divide-line border-y border-line">
      {rows.map((r) => (
        <li key={r.key}>
          <button
            type="button"
            className={ROW}
            disabled={r.added || busy}
            onClick={() => onPick(r.ref, "claims" in r ? (r.claims as string[]) : undefined)}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{r.title}</span>
              {r.sub ? <span className="block truncate text-[13px] text-muted">{r.sub}</span> : null}
            </span>
            <span className="shrink-0 text-[13px] text-muted">{r.added ? "넣음" : "더하기"}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** 줄 더하기: 그 경험(과 바로 아래 경험)의 확인한 사실을 고르거나 직접 쓴다. 직접 쓴 줄은 '근거 없음'이다. */
export function LinePicker({
  versionId,
  item,
  onClose,
  onAdd,
  busy,
  error,
}: {
  versionId: string;
  item: ResumeItem;
  onClose: () => void;
  onAdd: (ops: ResumeOperation[]) => void;
  busy: boolean;
  error: string | null;
}) {
  const fromBrain = item.ref?.type === "experience";
  const [mode, setMode] = useState<"claims" | "text">(fromBrain ? "claims" : "text");
  const [text, setText] = useState("");
  const picker = useQuery({
    queryKey: ["resume", versionId, "picker"],
    queryFn: () => api.resumePicker(versionId),
    enabled: fromBrain,
  });
  const used = new Set(item.lines.map((l) => l.claim_id).filter(Boolean));
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title="줄 더하기" width="lg">
      {fromBrain ? (
        <FilterChips
          label="줄 종류"
          value={mode}
          onChange={setMode}
          items={[
            { value: "claims", label: "확인한 사실에서" },
            { value: "text", label: "직접 쓰기" },
          ]}
          className="mb-3"
        />
      ) : null}
      {error ? (
        <p role="alert" className="mb-2 text-[13px] text-danger">
          {error}
        </p>
      ) : null}
      {mode === "claims" ? (
        <Loaded query={picker}>
          {(p) => {
            const exp = p.experiences.find((e) => e.id === item.ref?.id);
            if (!exp?.claims.length)
              return (
                <p className="border-t border-line pt-3 text-[13px] text-muted">
                  이 경험에 확인한 사실이 없습니다. 브레인에서 사실을 더하고 확인하면 여기서 고를 수 있습니다.
                </p>
              );
            return (
              <ul className="divide-y divide-line border-y border-line">
                {exp.claims.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      className={ROW}
                      disabled={used.has(c.id) || busy}
                      onClick={() => onAdd([{ op: "add_line", item_id: item.id, claim_id: c.id }])}
                    >
                      <span className="min-w-0 flex-1 text-sm leading-6">{c.statement}</span>
                      <span className="shrink-0 text-[13px] text-muted">{used.has(c.id) ? "넣음" : "더하기"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            );
          }}
        </Loaded>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onAdd([{ op: "add_line", item_id: item.id, text: text.trim() }]);
          }}
        >
          <p className="text-[13px] text-muted">직접 쓴 줄은 '근거 없음'으로 표시하고 범용 문장·근거 없는 수치를 검사합니다.</p>
          <Textarea aria-label="줄" rows={3} maxLength={500} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex gap-2 border-t border-line pt-3">
            <Button type="submit" variant="primary" disabled={busy || !text.trim()}>
              더하기
            </Button>
            <Button variant="ghost" onClick={onClose}>
              닫기
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}
