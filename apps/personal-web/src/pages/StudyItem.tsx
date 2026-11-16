import type { ClaimKind, StudyItem, StudyItemStatus } from "@career-brain/client";
import { Badge, Button, Input, Label, Section, Select, StatusBadge, Textarea } from "@career-brain/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { AttemptForm, AttemptRow, MistakeForm, ReviewCard, useStudyInvalidate } from "../components/study/parts";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { ATTEMPT_RESULT, ITEM_KIND, ITEM_STATUS, KIND_LABEL, REQ_FIELD } from "../lib/labels";

const route = getRouteApi("/study/items/$itemId");
const STATUSES: StudyItemStatus[] = ["planned", "in_progress", "done", "dropped"];

export function StudyItemPage() {
  const { itemId } = route.useParams();
  const q = useQuery({ queryKey: ["study-item", itemId], queryFn: () => api.studyItem(itemId) });
  return <Loaded query={q}>{(item) => <ItemView item={item} />}</Loaded>;
}

function ItemView({ item }: { item: StudyItem }) {
  const invalidate = useStudyInvalidate();
  const status = useMutation({
    mutationFn: (s: StudyItemStatus) => api.patchStudyItem(item.id, { status: s }),
    onSuccess: invalidate,
  });
  return (
    <div>
      <PageHeader
        back={{ to: "/study", search: { tab: "items" }, label: "학습 항목" }}
        title={item.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-1.5">
            <Badge tone={ITEM_STATUS[item.status].tone}>{ITEM_STATUS[item.status].label}</Badge>
            <span>{ITEM_KIND[item.kind]}</span>
            {item.total_minutes ? <span>· 누적 {item.total_minutes}분</span> : null}
          </span>
        }
      />
      {item.requirement ? (
        <p className="mb-4 rounded-lg border border-line bg-subtle/60 px-3 py-2 text-[13px]">
          <Link to="/study" search={{ tab: "coverage", posting: item.requirement.posting_id }} className="text-accent hover:underline">
            {item.requirement.posting_title ?? "공고"}
          </Link>{" "}
          · {REQ_FIELD[item.requirement.field]}: {item.requirement.text}
        </p>
      ) : null}
      <div className="mb-6 flex flex-wrap gap-1.5" role="radiogroup" aria-label="상태">
        {STATUSES.map((s) => (
          <Button
            key={s}
            size="sm"
            role="radio"
            aria-checked={item.status === s}
            variant={item.status === s ? "primary" : "secondary"}
            disabled={status.isPending}
            onClick={() => item.status !== s && status.mutate(s)}
          >
            {ITEM_STATUS[s].label}
          </Button>
        ))}
      </div>
      <p className="-mt-4 mb-6 text-xs text-muted">완료해도 경험(주장)은 생기지 않습니다. 학습함과 입증함은 다릅니다.</p>
      <ErrorText error={status.error} className="mb-3" />

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <Section title={`학습 기록 ${item.sessions.length}`}>
          <SessionForm itemId={item.id} />
          {item.sessions.length ? (
            <ul className="space-y-1.5">
              {item.sessions.map((s) => (
                <li key={s.id} className="rounded-md border border-line bg-surface px-2.5 py-2 text-[13px]">
                  <span className="font-medium tabular-nums">{s.minutes}분</span>
                  <span className="text-xs text-muted"> · {formatDateTime(s.started_at)}</span>
                  {s.notes ? <p className="mt-0.5 whitespace-pre-wrap">{s.notes}</p> : null}
                  {s.resources.length ? (
                    <p className="mt-0.5 text-xs text-muted">
                      자료:{" "}
                      {s.resources.map((r, i) => (
                        <span key={i}>
                          {i ? ", " : ""}
                          {r.url ? (
                            <a href={r.url} target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">
                              {r.title}
                            </a>
                          ) : (
                            r.title
                          )}
                        </span>
                      ))}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </Section>

        <Section title={`연습 응시 ${item.attempts.length}`}>
          {item.attempts.length ? (
            <ul className="space-y-1.5">
              {item.attempts.map((a) => (
                <li key={a.id} className="rounded-md border border-line bg-surface px-2.5 py-2">
                  <AttemptRow a={a} />
                </li>
              ))}
            </ul>
          ) : null}
          <AttemptForm studyItemId={item.id} />
        </Section>

        <Section title={`오답 ${item.mistakes.length}`}>
          {item.mistakes.map((m) => (
            <ReviewCard key={m.id} m={m} showGrade={false} />
          ))}
          <MistakeForm studyItemId={item.id} />
        </Section>

        <Section title="경험 후보로 올리기">
          <CandidateForm item={item} />
        </Section>
      </div>
    </div>
  );
}

function SessionForm({ itemId }: { itemId: string }) {
  const invalidate = useStudyInvalidate();
  const empty = { minutes: "", notes: "", rtitle: "", rurl: "" };
  const [form, setForm] = useState(empty);
  const save = useMutation({
    mutationFn: () =>
      api.logStudySession(itemId, {
        minutes: Number(form.minutes),
        notes: form.notes.trim() || null,
        resources: form.rtitle.trim() ? [{ title: form.rtitle.trim(), url: form.rurl.trim() || null }] : [],
      }),
    onSuccess: () => {
      setForm(empty);
      invalidate();
    },
  });
  return (
    <form
      className="space-y-2 rounded-lg border border-line bg-surface p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (Number(form.minutes) > 0) save.mutate();
      }}
    >
      <div className="grid grid-cols-[100px_minmax(0,1fr)] gap-2">
        <div>
          <Label htmlFor="ss-min">분</Label>
          <Input id="ss-min" type="number" min={1} max={1440} inputMode="numeric" value={form.minutes} onChange={(e) => setForm({ ...form, minutes: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="ss-notes">메모</Label>
          <Input id="ss-notes" value={form.notes} maxLength={4000} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Input value={form.rtitle} maxLength={300} placeholder="자료 제목 (선택)" aria-label="자료 제목" onChange={(e) => setForm({ ...form, rtitle: e.target.value })} />
        <Input value={form.rurl} maxLength={2000} placeholder="자료 URL (선택)" aria-label="자료 URL" onChange={(e) => setForm({ ...form, rurl: e.target.value })} />
      </div>
      <ErrorText error={save.error} />
      <Button type="submit" size="sm" variant="primary" disabled={!(Number(form.minutes) > 0) || save.isPending}>
        학습 기록
      </Button>
    </form>
  );
}

function CandidateForm({ item }: { item: StudyItem }) {
  const invalidate = useStudyInvalidate();
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: api.experiences });
  const [form, setForm] = useState({ experience: "", statement: "", kind: "skill" as ClaimKind, attempt: "" });
  const ready = item.status === "done" || item.attempts.length > 0;
  const save = useMutation({
    mutationFn: () =>
      api.proposeStudyClaim(item.id, {
        experience_id: form.experience,
        statement: form.statement.trim(),
        kind: form.kind,
        attempt_id: form.attempt || null,
      }),
    onSuccess: () => {
      setForm({ ...form, statement: "", attempt: "" });
      invalidate();
    },
  });
  const exps = experiences.data ?? [];
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        학습 결과를 내 경험으로 옮기고 싶을 때만 씁니다. 문장은 직접 쓰고, 주장은 “확인 필요” 상태로 들어가 브레인에서 확인해야 근거가 됩니다.
      </p>
      {item.claim_candidates.length ? (
        <ul className="space-y-1.5">
          {item.claim_candidates.map((c) => (
            <li key={c.claim_id} className="flex items-start gap-2 rounded-md border border-line bg-surface px-2.5 py-1.5 text-[13px]">
              <span className="min-w-0 flex-1">{c.statement}</span>
              <StatusBadge status={c.status} />
            </li>
          ))}
        </ul>
      ) : null}
      {!ready ? (
        <p className="text-[13px] text-warn">항목을 완료하거나 응시 기록이 있어야 올릴 수 있습니다.</p>
      ) : !exps.length ? (
        <p className="text-[13px] text-muted">
          먼저{" "}
          <Link to="/brain" className="text-accent hover:underline">
            브레인
          </Link>
          에 경험을 만드세요.
        </p>
      ) : (
        <form
          className="space-y-2 rounded-lg border border-line bg-surface p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (form.experience && form.statement.trim()) save.mutate();
          }}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="cc-exp">경험</Label>
              <Select id="cc-exp" value={form.experience} onChange={(e) => setForm({ ...form, experience: e.target.value })}>
                <option value="">고르기</option>
                {exps.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.title}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="cc-kind">종류</Label>
              <Select id="cc-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as ClaimKind })}>
                {(Object.keys(KIND_LABEL) as ClaimKind[]).map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k]}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          {item.attempts.length ? (
            <div>
              <Label htmlFor="cc-att">근거로 남길 응시 (선택)</Label>
              <Select id="cc-att" value={form.attempt} onChange={(e) => setForm({ ...form, attempt: e.target.value })}>
                <option value="">없음</option>
                {item.attempts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {formatDateTime(a.taken_at)} · {ATTEMPT_RESULT[a.result].label}
                  </option>
                ))}
              </Select>
            </div>
          ) : null}
          <Textarea
            value={form.statement}
            maxLength={4000}
            placeholder="예: 개인 프로젝트에서 Kubernetes로 서비스 2개를 배포·운영해 봤다"
            aria-label="주장 문장"
            onChange={(e) => setForm({ ...form, statement: e.target.value })}
          />
          <ErrorText error={save.error} />
          <Button type="submit" size="sm" variant="primary" disabled={!form.experience || !form.statement.trim() || save.isPending}>
            확인 대기 주장으로 올리기
          </Button>
        </form>
      )}
    </div>
  );
}
