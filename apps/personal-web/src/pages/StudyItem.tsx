import type { ClaimKind, StudyItem, StudyItemStatus } from "@career-brain/client";
import { Button, cn, Input, Label, Section, Select, SelectItem, StatusBadge, Textarea } from "@career-brain/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import {
  AttemptForm,
  AttemptRow,
  MistakeForm,
  ReviewCard,
  Rows,
  TAP,
  useStudyInvalidate,
} from "../components/study/parts";
import { api, ApiError } from "../lib/api";
import { formatDateTime } from "../lib/dates";
import { ATTEMPT_RESULT, ITEM_KIND, ITEM_STATUS, KIND_LABEL, REQ_FIELD } from "../lib/labels";

const route = getRouteApi("/study/items/$itemId");
const STATUSES: StudyItemStatus[] = ["planned", "in_progress", "done", "dropped"];
const TEXT_LINK = "inline-flex min-h-9 items-center text-fg underline underline-offset-2 hover:text-muted sm:min-h-6";

export function StudyItemPage() {
  const { itemId } = route.useParams();
  const q = useQuery({ queryKey: ["study-item", itemId], queryFn: () => api.studyItem(itemId) });
  if (q.error instanceof ApiError && q.error.status === 404) return <MissingItem />;
  return <Loaded query={q}>{(item) => <ItemView item={item} />}</Loaded>;
}

/** 지웠거나 다른 공간의 항목: 일반 오류 대신 학습으로 돌아가는 길을 준다 */
function MissingItem() {
  return (
    <div className="pt-6">
      <h1 className="text-xl leading-8 font-semibold">이 학습 항목을 찾을 수 없습니다</h1>
      <p className="mt-1 text-sm text-muted">
        지웠거나 합쳐진 항목일 수 있습니다.{" "}
        <Link to="/study" search={{ tab: "items" }} className={TEXT_LINK}>
          학습으로
        </Link>
      </p>
    </div>
  );
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
        subtitle={[
          ITEM_STATUS[item.status].label,
          ITEM_KIND[item.kind],
          item.total_minutes ? `누적 ${item.total_minutes}분` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      />
      {item.status === "merged" && item.merged_into_id ? (
        <p className="mb-6 rounded-lg border border-line bg-subtle/60 px-3 py-2 text-[13px]">
          다른 항목에 합쳐졌습니다. 기록은{" "}
          <Link to="/study/items/$itemId" params={{ itemId: item.merged_into_id }} className={TEXT_LINK}>
            합친 항목
          </Link>
          에서 이어 갑니다. 응시·경험 후보 기록은 바꾸지 않는 기록이라 이 항목에 남아 합친 항목에서 함께 보입니다.
        </p>
      ) : null}
      {item.requirement || item.merged_from?.length ? (
        <div className="mb-6 space-y-1 text-[13px] text-muted">
          {item.requirement ? (
            <p>
              <Link
                to="/study"
                search={{ tab: "coverage", posting: item.requirement.posting_id }}
                className={TEXT_LINK}
              >
                {item.requirement.posting_title ?? "공고"}
              </Link>{" "}
              · {REQ_FIELD[item.requirement.field]}: {item.requirement.text}
            </p>
          ) : null}
          {item.merged_from?.length ? (
            <p>
              합쳐 온 항목: {item.merged_from.map((m) => m.title).join(", ")} (응시·경험 후보 기록은 원래 항목에 남아
              여기서 함께 보입니다)
            </p>
          ) : null}
        </div>
      ) : null}
      {item.status === "merged" ? null : (
        <>
          <div className="mb-10 space-y-2">
            <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="상태">
              {STATUSES.map((s) => (
                <Button
                  key={s}
                  size="sm"
                  role="radio"
                  aria-checked={item.status === s}
                  variant={item.status === s ? "primary" : "secondary"}
                  className={TAP}
                  disabled={status.isPending}
                  onClick={() => item.status !== s && status.mutate(s)}
                >
                  {ITEM_STATUS[s].label}
                </Button>
              ))}
            </div>
            <p className="text-[13px] text-muted">완료해도 경험(확인한 사실)은 생기지 않습니다. 학습함과 입증함은 다릅니다.</p>
            <ErrorText error={status.error} />
          </div>

          <div className="grid grid-cols-1 gap-x-14 gap-y-10 lg:grid-cols-2">
            <Section title={`학습 기록 ${item.sessions.length}`}>
              <SessionForm itemId={item.id} />
              {item.sessions.length ? (
                <Rows className="mt-4">
                  {item.sessions.map((s) => (
                    <li key={s.id} className="py-3 text-sm">
                      <p>
                        <span className="font-medium tabular-nums">{s.minutes}분</span>
                        <span className="text-[13px] text-muted tabular-nums"> · {formatDateTime(s.started_at)}</span>
                      </p>
                      {s.notes ? <p className="mt-0.5 whitespace-pre-wrap">{s.notes}</p> : null}
                      {s.resources.length ? (
                        <p className="mt-0.5 text-[13px] text-muted">
                          자료:{" "}
                          {s.resources.map((r, i) => (
                            <span key={i}>
                              {i ? ", " : ""}
                              {r.url ? (
                                <a href={r.url} target="_blank" rel="noreferrer noopener" className={TEXT_LINK}>
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
                </Rows>
              ) : null}
            </Section>

            <Section title={`연습 응시 ${item.attempts.length}`}>
              {item.attempts.length ? (
                <Rows>
                  {item.attempts.map((a) => (
                    <li key={a.id} className="py-3">
                      <AttemptRow a={a} />
                    </li>
                  ))}
                </Rows>
              ) : null}
              <div className={cn(item.attempts.length > 0 && "mt-4 border-t border-line pt-4")}>
                <AttemptForm studyItemId={item.id} />
              </div>
            </Section>

            <Section title={`오답 ${item.mistakes.length}`}>
              {item.mistakes.length ? (
                <Rows>
                  {item.mistakes.map((m) => (
                    <li key={m.id}>
                      <ReviewCard m={m} showGrade={false} />
                    </li>
                  ))}
                </Rows>
              ) : null}
              <div className={cn(item.mistakes.length > 0 && "mt-4 border-t border-line pt-4")}>
                <MistakeForm studyItemId={item.id} />
              </div>
            </Section>

            <Section title="경험 후보로 올리기">
              <CandidateForm item={item} />
            </Section>

            <Section title="합치기·삭제">
              <MergeDelete item={item} />
            </Section>
          </div>
        </>
      )}
    </div>
  );
}

function MergeDelete({ item }: { item: StudyItem }) {
  const invalidate = useStudyInvalidate();
  const navigate = useNavigate();
  const items = useQuery({ queryKey: ["study-items", ""], queryFn: () => api.studyItems() });
  const [into, setInto] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const merge = useMutation({
    mutationFn: () => api.mergeStudyItem(item.id, into),
    onSuccess: (r) => {
      invalidate();
      void navigate({ to: "/study/items/$itemId", params: { itemId: r.target_id } });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteStudyItem(item.id),
    onSuccess: () => {
      invalidate();
      void navigate({ to: "/study", search: { tab: "items" } });
    },
  });
  const others = (items.data ?? []).filter((x) => x.id !== item.id);
  return (
    <div className="space-y-3 text-[13px]">
      <p className="text-muted">
        같은 내용을 두 번 만들었으면 합칩니다. 학습 기록·오답은 고른 항목으로 옮기고, 응시·경험 후보 기록은 이 항목에
        그대로 남아 고른 항목에서 함께 보입니다.
      </p>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (into) merge.mutate();
        }}
      >
        <div className="min-w-0 flex-1">
          <Label htmlFor="mg-into">합칠 대상</Label>
          <Select id="mg-into" value={into} onValueChange={(v) => setInto(v)}>
            <SelectItem value="">고르기</SelectItem>
            {others.map((x) => (
              <SelectItem key={x.id} value={x.id}>
                {x.title}
              </SelectItem>
            ))}
          </Select>
        </div>
        <Button type="submit" size="sm" className={TAP} disabled={!into || merge.isPending}>
          이 항목을 합치기
        </Button>
      </form>
      <ErrorText error={merge.error} />
      <div className={cn("flex flex-wrap items-center gap-2", !confirmDelete && "-ml-2.5")}>
        {confirmDelete ? (
          <>
            <span>
              학습 기록 {item.sessions.length}개·오답 {item.mistakes.length}개도 함께 지웁니다.
            </span>
            <Button
              size="sm"
              variant="danger"
              className={TAP}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              삭제
            </Button>
            <Button size="sm" variant="ghost" className={TAP} onClick={() => setConfirmDelete(false)}>
              취소
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" className={TAP} onClick={() => setConfirmDelete(true)}>
            항목 삭제
          </Button>
        )}
      </div>
      <p className="text-muted">응시·경험 후보·복습 기록이 있는 항목은 지우지 않습니다 (그만둠으로 두세요).</p>
      <ErrorText error={remove.error} />
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
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (Number(form.minutes) > 0) save.mutate();
      }}
    >
      <div className="grid grid-cols-[100px_minmax(0,1fr)] gap-2">
        <div>
          <Label htmlFor="ss-min">분</Label>
          <Input
            id="ss-min"
            type="number"
            min={1}
            max={1440}
            inputMode="numeric"
            value={form.minutes}
            onChange={(e) => setForm({ ...form, minutes: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor="ss-notes">메모</Label>
          <Input
            id="ss-notes"
            value={form.notes}
            maxLength={4000}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
          />
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Input
          value={form.rtitle}
          maxLength={300}
          placeholder="자료 제목 (선택)"
          aria-label="자료 제목"
          onChange={(e) => setForm({ ...form, rtitle: e.target.value })}
        />
        <Input
          value={form.rurl}
          maxLength={2000}
          placeholder="자료 URL (선택)"
          aria-label="자료 URL"
          onChange={(e) => setForm({ ...form, rurl: e.target.value })}
        />
      </div>
      <ErrorText error={save.error} />
      <Button
        type="submit"
        size="sm"
        variant="primary"
        className={TAP}
        disabled={!(Number(form.minutes) > 0) || save.isPending}
      >
        학습 기록
      </Button>
    </form>
  );
}

function CandidateForm({ item }: { item: StudyItem }) {
  const invalidate = useStudyInvalidate();
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });
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
      <p className="text-[13px] text-muted">
        학습 결과를 내 경험으로 옮기고 싶을 때만 씁니다. 문장은 직접 쓰고, 사실은 “확인 필요” 상태로 들어가 브레인에서
        확인해야 근거가 됩니다.
      </p>
      {item.claim_candidates.length ? (
        <Rows>
          {item.claim_candidates.map((c) => (
            <li key={c.claim_id} className="flex items-start gap-4 py-3 text-sm">
              <span className="min-w-0 flex-1">{c.statement}</span>
              <StatusBadge status={c.status} />
            </li>
          ))}
        </Rows>
      ) : null}
      {!ready ? (
        <p className="text-[13px] font-medium">항목을 완료하거나 응시 기록이 있어야 올릴 수 있습니다.</p>
      ) : !exps.length ? (
        <p className="text-[13px] text-muted">
          먼저{" "}
          <Link to="/brain" className={TEXT_LINK}>
            브레인
          </Link>
          에 경험을 만드세요.
        </p>
      ) : (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (form.experience && form.statement.trim()) save.mutate();
          }}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="cc-exp">경험</Label>
              <Select
                id="cc-exp"
                value={form.experience}
                onValueChange={(v) => setForm({ ...form, experience: v })}
              >
                <SelectItem value="">고르기</SelectItem>
                {exps.map((x) => (
                  <SelectItem key={x.id} value={x.id}>
                    {x.title}
                  </SelectItem>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="cc-kind">종류</Label>
              <Select
                id="cc-kind"
                value={form.kind}
                onValueChange={(v) => setForm({ ...form, kind: v as ClaimKind })}
              >
                {(Object.keys(KIND_LABEL) as ClaimKind[]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {KIND_LABEL[k]}
                  </SelectItem>
                ))}
              </Select>
            </div>
          </div>
          {item.attempts.length ? (
            <div>
              <Label htmlFor="cc-att">근거로 남길 응시 (선택)</Label>
              <Select id="cc-att" value={form.attempt} onValueChange={(v) => setForm({ ...form, attempt: v })}>
                <SelectItem value="">없음</SelectItem>
                {item.attempts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {formatDateTime(a.taken_at)} · {ATTEMPT_RESULT[a.result].label}
                  </SelectItem>
                ))}
              </Select>
            </div>
          ) : null}
          <Textarea
            value={form.statement}
            maxLength={4000}
            placeholder="예: 개인 프로젝트에서 Kubernetes로 서비스 2개를 배포·운영해 봤다"
            aria-label="사실 문장"
            onChange={(e) => setForm({ ...form, statement: e.target.value })}
          />
          <ErrorText error={save.error} />
          <Button
            type="submit"
            size="sm"
            variant="primary"
            className={TAP}
            disabled={!form.experience || !form.statement.trim() || save.isPending}
          >
            확인 대기 사실로 올리기
          </Button>
        </form>
      )}
    </div>
  );
}
