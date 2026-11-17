import {
  EMPLOYMENT_TYPES,
  MILITARY_STATUSES,
  type EmploymentType,
  type MilitaryStatus,
  type ProfileDetails,
  type ProfileDetailsPatch,
} from "@career-brain/client";
import { Button, Checkbox, cn, Collapsible, CollapsibleContent, CollapsibleTrigger, Input, Label, MonthField, Select, SelectItem, toast } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { EMPLOYMENT_LABEL, meErrorText, MILITARY_LABEL } from "../../lib/brainMe";
import { errorText, Loaded } from "../common";

/**
 * 프로필 더하기 (c0041): 병역·보훈·장애·희망 조건. 모두 선택이고, 접어 둔다.
 * 이 칸들은 모델에도, 연결한 AI 에이전트(MCP)에도 보내지 않는다. 지원서 양식에 직접 옮겨 적을 때 본다.
 */
export function ProfileDetailsSection() {
  const q = useQuery({ queryKey: ["profile", "details"], queryFn: api.profileDetails });
  return (
    <section aria-labelledby="pd-title">
      <h2 id="pd-title" className="text-[15px] font-semibold">
        더 적어 둘 것 <span className="font-normal text-muted">선택</span>
      </h2>
      <p className="mt-0.5 text-[13px] text-muted">
        모델과 AI 에이전트에 보내지 않습니다.
      </p>
      <Loaded query={q}>{(d) => <DetailsForm initial={d} />}</Loaded>
    </section>
  );
}

interface Values {
  military_status: MilitaryStatus | "";
  military_branch: string;
  military_rank: string;
  military_started_on: string;
  military_ended_on: string;
  military_exemption_reason: string;
  veteran: "" | "yes" | "no";
  disability: "" | "yes" | "no";
  desired_roles: string;
  desired_locations: string;
  desired_salary: string;
  available_from: string;
  employment_types: EmploymentType[];
}

const tri = (b: boolean | null): "" | "yes" | "no" => (b === null ? "" : b ? "yes" : "no");
const untri = (v: "" | "yes" | "no") => (v === "" ? null : v === "yes");
const list = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

function valuesOf(d: ProfileDetails): Values {
  return {
    military_status: d.military_status ?? "",
    military_branch: d.military_branch ?? "",
    military_rank: d.military_rank ?? "",
    military_started_on: d.military_started_on?.slice(0, 7) ?? "",
    military_ended_on: d.military_ended_on?.slice(0, 7) ?? "",
    military_exemption_reason: d.military_exemption_reason ?? "",
    veteran: tri(d.veteran),
    disability: tri(d.disability),
    desired_roles: d.desired_roles.join(", "),
    desired_locations: d.desired_locations.join(", "),
    desired_salary: d.desired_salary ?? "",
    available_from: d.available_from ?? "",
    employment_types: d.employment_types,
  };
}

function bodyOf(v: Values): ProfileDetailsPatch {
  const t = (s: string) => s.trim() || null;
  return {
    military_status: v.military_status || null,
    military_branch: t(v.military_branch),
    military_rank: t(v.military_rank),
    military_started_on: v.military_started_on || null,
    military_ended_on: v.military_ended_on || null,
    military_exemption_reason: t(v.military_exemption_reason),
    veteran: untri(v.veteran),
    disability: untri(v.disability),
    desired_roles: list(v.desired_roles),
    desired_locations: list(v.desired_locations),
    desired_salary: t(v.desired_salary),
    available_from: t(v.available_from),
    employment_types: v.employment_types,
  };
}

function DetailsForm({ initial }: { initial: ProfileDetails }) {
  const qc = useQueryClient();
  const [v, setV] = useState(() => valuesOf(initial));
  const dirty = JSON.stringify(bodyOf(v)) !== JSON.stringify(bodyOf(valuesOf(initial)));
  const set = (patch: Partial<Values>) => {
    setV((prev) => ({ ...prev, ...patch }));
  };
  const save = useMutation({
    mutationFn: () => api.patchProfileDetails(bodyOf(v)),
    onSuccess: (d) => {
      qc.setQueryData(["profile", "details"], d);
      setV(valuesOf(d)); // 서버가 정리한 값 (복무 칸 비우기 등)
      toast("저장했습니다.");
    },
  });
  const serving = v.military_status === "served" || v.military_status === "serving";

  const militarySummary = v.military_status
    ? [
        MILITARY_LABEL[v.military_status],
        serving ? [v.military_branch, v.military_rank].filter(Boolean).join(" ") : null,
        v.military_status === "exempt" ? v.military_exemption_reason : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  const supportSummary =
    [
      v.veteran === "yes" ? "보훈 대상" : v.veteran === "no" ? "보훈 비대상" : null,
      v.disability === "yes" ? "장애 있음" : v.disability === "no" ? "장애 없음" : null,
    ]
      .filter(Boolean)
      .join(" · ") || null;
  const wishSummary =
    [
      list(v.desired_roles).join(", "),
      v.employment_types.map((x) => EMPLOYMENT_LABEL[x]).join(", "),
      list(v.desired_locations).join(", "),
    ]
      .filter(Boolean)
      .join(" · ") || null;

  return (
    <form
      className="mt-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty) save.mutate();
      }}
    >
      <div className="divide-y divide-line border-y border-line">
        <Group title="병역" summary={militarySummary}>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="pd-mil">구분</Label>
              <Select
                id="pd-mil"
                value={v.military_status}
                onValueChange={(v) => set({ military_status: v as MilitaryStatus | "" })}
              >
                <SelectItem value="">적지 않음</SelectItem>
                {MILITARY_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {MILITARY_LABEL[s]}
                  </SelectItem>
                ))}
              </Select>
            </div>
            {v.military_status === "exempt" ? (
              <div>
                <Label htmlFor="pd-exempt">면제 사유 (선택)</Label>
                <Input
                  id="pd-exempt"
                  value={v.military_exemption_reason}
                  onChange={(e) => set({ military_exemption_reason: e.target.value })}
                  maxLength={200}
                />
              </div>
            ) : null}
          </div>
          {serving ? (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="pd-branch">군별</Label>
                <Input
                  id="pd-branch"
                  value={v.military_branch}
                  onChange={(e) => set({ military_branch: e.target.value })}
                  maxLength={20}
                  placeholder="육군"
                />
              </div>
              <div>
                <Label htmlFor="pd-rank">계급</Label>
                <Input
                  id="pd-rank"
                  value={v.military_rank}
                  onChange={(e) => set({ military_rank: e.target.value })}
                  maxLength={20}
                  placeholder="병장"
                />
              </div>
              <div>
                <Label htmlFor="pd-start">입대</Label>
                <MonthField
                  id="pd-start"
                  value={v.military_started_on}
                  onChange={(value) => set({ military_started_on: value })}
                />
              </div>
              <div>
                <Label htmlFor="pd-end">{v.military_status === "serving" ? "전역 예정" : "전역"}</Label>
                <MonthField
                  id="pd-end"
                  value={v.military_ended_on}
                  onChange={(value) => set({ military_ended_on: value })}
                />
              </div>
            </div>
          ) : null}
        </Group>

        <Group title="보훈·장애" summary={supportSummary}>
          <p className="text-[13px] text-muted">가산점·우대 칸에 쓰는 값입니다. 밝히고 싶지 않으면 비워 둡니다.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="pd-veteran">보훈 대상</Label>
              <Select
                id="pd-veteran"
                value={v.veteran}
                onValueChange={(v) => set({ veteran: v as Values["veteran"] })}
              >
                <SelectItem value="">적지 않음</SelectItem>
                <SelectItem value="yes">대상</SelectItem>
                <SelectItem value="no">비대상</SelectItem>
              </Select>
            </div>
            <div>
              <Label htmlFor="pd-disability">장애</Label>
              <Select
                id="pd-disability"
                value={v.disability}
                onValueChange={(v) => set({ disability: v as Values["disability"] })}
              >
                <SelectItem value="">적지 않음</SelectItem>
                <SelectItem value="yes">있음</SelectItem>
                <SelectItem value="no">없음</SelectItem>
              </Select>
            </div>
          </div>
        </Group>

        <Group title="희망 조건" summary={wishSummary}>
          <div>
            <Label htmlFor="pd-roles">희망 직무</Label>
            <Input
              id="pd-roles"
              value={v.desired_roles}
              onChange={(e) => set({ desired_roles: e.target.value })}
              placeholder="백엔드 개발, 데이터 엔지니어 (쉼표로 나눕니다)"
            />
          </div>
          <fieldset>
            <legend className="mb-1 text-[13px] font-medium">고용 형태</legend>
            <div className="flex flex-wrap gap-x-4">
              {EMPLOYMENT_TYPES.map((t) => (
                <label key={t} className="flex min-h-9 items-center gap-2 text-sm">
                  <Checkbox
                    checked={v.employment_types.includes(t)}
                    onCheckedChange={(checked) => set({
                        employment_types: checked
                          ? EMPLOYMENT_TYPES.filter((x) => x === t || v.employment_types.includes(x))
                          : v.employment_types.filter((x) => x !== t),
                      })
                    }
                  />
                  {EMPLOYMENT_LABEL[t]}
                </label>
              ))}
            </div>
          </fieldset>
          <div>
            <Label htmlFor="pd-loc">희망 근무지</Label>
            <Input
              id="pd-loc"
              value={v.desired_locations}
              onChange={(e) => set({ desired_locations: e.target.value })}
              placeholder="서울, 판교"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="pd-salary">희망 연봉</Label>
              <Input
                id="pd-salary"
                value={v.desired_salary}
                onChange={(e) => set({ desired_salary: e.target.value })}
                maxLength={100}
                placeholder="회사 내규에 따름"
              />
            </div>
            <div>
              <Label htmlFor="pd-from">입사 가능일</Label>
              <Input
                id="pd-from"
                value={v.available_from}
                onChange={(e) => set({ available_from: e.target.value })}
                maxLength={50}
                placeholder="즉시"
              />
            </div>
          </div>
        </Group>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
          {save.isPending ? "저장하는 중…" : "저장"}
        </Button>
        {dirty ? (
          <Button variant="ghost" onClick={() => setV(valuesOf(initial))} disabled={save.isPending}>
            취소
          </Button>
        ) : null}
        {save.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {meErrorText(save.error) ?? errorText(save.error)}
          </p>
) : null}
      </div>
    </form>
  );
}

function Group({ title, summary, children }: { title: string; summary: string | null; children: ReactNode }) {
  return (
    <Collapsible className="group/group">
      <CollapsibleTrigger
        className={cn(
          "-mx-2 flex min-h-11 w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-2.5 text-left",
          "transition-colors duration-150 hover:bg-subtle/60",
        )}
      >
        <span className="text-sm font-medium">{title}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted">{summary ?? "적지 않음"}</span>
        <ChevronRight
          size={16}
          aria-hidden
          className="shrink-0 text-muted transition-transform group-data-[state=open]/group:rotate-90"
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 pt-1 pb-4">{children}</CollapsibleContent>
    </Collapsible>
  );
}
