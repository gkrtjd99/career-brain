// 공간 공용 설정 (c0021 workspace-settings): 표시 시간대와 기술명 별칭 사전.
import type { TechAliasEntry, WorkspaceSettings } from "@career-brain/client";
import { Badge, Button, Card, Label, Section, Select, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { ErrorText } from "../common";

const COMMON_ZONES = ["Asia/Seoul", "Asia/Tokyo", "UTC", "Europe/London", "America/New_York", "America/Los_Angeles"];

/** "FastAPI = 패스트API, fast api" 한 줄 → 항목 */
export function parseAliasLines(text: string): TechAliasEntry[] {
  const out: TechAliasEntry[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = line.indexOf("=");
    const term = (at < 0 ? line : line.slice(0, at)).trim();
    const aliases = at < 0 ? [] : line.slice(at + 1).split(",").map((x) => x.trim()).filter(Boolean);
    if (term) out.push({ term, aliases });
  }
  return out;
}

export function aliasLines(entries: TechAliasEntry[]): string {
  return entries.map((e) => (e.aliases.length ? `${e.term} = ${e.aliases.join(", ")}` : e.term)).join("\n");
}

/** 모든 화면의 날짜 표시 기준 시간대와, 문서 검사·요건 빈도가 같이 쓰는 기술명 별칭 */
export function WorkspaceSettingsSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["workspace-settings"], queryFn: api.workspaceSettings });
  const [aliases, setAliases] = useState("");
  useEffect(() => {
    if (q.data) setAliases(aliasLines(q.data.tech_aliases));
  }, [q.data]);
  const done = (d: WorkspaceSettings) => {
    qc.setQueryData(["workspace-settings"], d);
    // 시간대는 복습 마감, 별칭은 문서 검사·요건 빈도에 쓰인다
    for (const key of ["workspace-settings", "study-settings", "validate", "requirement-frequency"]) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  };
  const saveZone = useMutation({
    mutationFn: (timezone: string | null) => api.saveWorkspaceSettings({ timezone }),
    onSuccess: done,
  });
  const saveAliases = useMutation({
    mutationFn: () => api.saveWorkspaceSettings({ tech_aliases: parseAliasLines(aliases) }),
    onSuccess: done,
  });
  const d = q.data;
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zones = Array.from(new Set([d?.timezone, browser, ...COMMON_ZONES].filter((z): z is string => Boolean(z))));
  return (
    <Section title="작업 공간">
      <Card className="space-y-4 p-4">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor="workspace-timezone">표시 시간대</Label>
            {d ? <Badge tone={d.timezone_is_default ? "neutral" : "accent"}>{d.timezone_is_default ? "기본값" : "직접 정함"}</Badge> : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              id="workspace-timezone"
              className="w-auto"
              value={d?.timezone ?? ""}
              disabled={!d || saveZone.isPending}
              onChange={(e) => saveZone.mutate(e.target.value)}
            >
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z}
                  {z === browser ? " (이 브라우저)" : ""}
                </option>
              ))}
            </Select>
            {d && !d.timezone_is_default ? (
              <Button size="sm" disabled={saveZone.isPending} onClick={() => saveZone.mutate(null)}>
                기본값으로
              </Button>
            ) : null}
          </div>
          <p className="text-xs text-muted">
            모든 화면의 날짜·시각, D-day, 복습 마감(오늘 끝)이 이 시간대를 따릅니다.{d ? ` 지금 기준 오늘: ${d.today}` : ""}
          </p>
          <ErrorText error={saveZone.error} />
        </div>
        <div className="space-y-2 border-t border-line pt-4">
          <Label htmlFor="tech-aliases">기술명 별칭 (한 줄에 하나: 대표 표기 = 다른 표기, 다른 표기)</Label>
          <Textarea
            id="tech-aliases"
            value={aliases}
            placeholder="FastAPI = 패스트API, fast api"
            className="min-h-32 font-mono text-[13px]"
            onChange={(e) => setAliases(e.target.value)}
            disabled={!d}
          />
          <p className="text-xs text-muted">
            문서 검사(요건이 문서에 보이는지, 사실 바뀜)와 학습 화면의 요건 빈도 묶기가 같은 기술로 봅니다. 기본 표 위에 합쳐지며, 같은
            대표 표기를 적으면 기본 표 항목을 대신합니다. 200개, 항목당 다른 표기 20개, 40자까지.
          </p>
          <ErrorText error={saveAliases.error} />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" disabled={!d || saveAliases.isPending} onClick={() => saveAliases.mutate()}>
              별칭 저장
            </Button>
          </div>
          {d?.builtin_aliases.length ? (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">기본 표 ({d.builtin_aliases.length}개, 읽기 전용)</summary>
              <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap font-mono">{aliasLines(d.builtin_aliases)}</pre>
            </details>
          ) : null}
        </div>
      </Card>
    </Section>
  );
}
