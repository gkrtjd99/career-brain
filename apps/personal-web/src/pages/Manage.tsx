import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Loaded, PageHeader, Tabs } from "../components/common";
import { McpTab } from "../components/manage/McpTab";
import { ModelsTab } from "../components/manage/ModelsTab";
import { modelsErrorText } from "../components/manage/parts";
import { RoutesTab } from "../components/manage/RoutesTab";
import { RunsTab } from "../components/manage/RunsTab";
import { SettingsTab } from "../components/manage/SettingsTab";
import { TokensTab } from "../components/manage/TokensTab";
import { UsageTab } from "../components/manage/UsageTab";
import { MaintenanceTab } from "../components/system/MaintenanceTab";
import { api, useToken } from "../lib/api";

export type ManageTab = "models" | "routes" | "usage" | "mcp" | "runs" | "tokens" | "maintenance" | "settings";
export const MANAGE_TABS: ManageTab[] = ["models", "routes", "usage", "mcp", "runs", "tokens", "maintenance", "settings"];
export interface ManageSearch {
  tab?: ManageTab;
  approval?: string;
  run?: string;
}

const route = getRouteApi("/manage");

/** 연결·관리 (plan §21): 모델 연결부터 설정까지 관리 일을 한 화면의 탭으로 모은다. ?tab=로 바로 열린다. */
export function ManagePage() {
  const { tab = "models", approval, run } = route.useSearch();
  const navigate = useNavigate();
  const token = useToken();
  const me = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, staleTime: 300_000 });
  const scopes = me.data?.scopes ?? [];
  const isAdmin = scopes.includes("workspace-admin");
  const pending = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.approvals({ status: "pending", limit: 20 }),
  });
  const models = useQuery({
    queryKey: ["models-status"],
    queryFn: api.modelsStatus,
    enabled: tab === "models" || tab === "routes",
    retry: false,
  });
  const go = (search: ManageSearch) =>
    navigate({
      to: "/manage",
      search: {
        ...(search.tab && search.tab !== "models" ? { tab: search.tab } : {}),
        ...(search.approval ? { approval: search.approval } : {}),
        ...(search.run ? { run: search.run } : {}),
      },
      replace: true,
    });

  return (
    <div>
      <PageHeader title="연결·관리" subtitle="모델 연결, 사용량, 에이전트, 작업, 토큰, 백업과 설정." />
      <Tabs<ManageTab>
        value={tab}
        onChange={(t) => go({ tab: t })}
        items={[
          { value: "models", label: "모델 연결" },
          { value: "routes", label: "용도별 모델" },
          { value: "usage", label: "사용량·예산" },
          { value: "mcp", label: "MCP·에이전트" },
          { value: "runs", label: `작업·승인${pending.data?.length ? ` ${pending.data.length}` : ""}` },
          { value: "tokens", label: "토큰" },
          { value: "maintenance", label: "백업·유지보수" },
          { value: "settings", label: "설정" },
        ]}
      />
      <div className="pt-2">
        {tab === "models" || tab === "routes" ? (
          models.isError ? (
            <p role="alert" className="py-4 text-[13px] text-danger">
              {modelsErrorText(models.error)}
            </p>
          ) : (
            <Loaded query={models}>
              {(status) =>
                tab === "models" ? (
                  <ModelsTab status={status} canEdit={isAdmin} />
                ) : (
                  <RoutesTab status={status} canEdit={isAdmin} />
                )
              }
            </Loaded>
          )
        ) : null}
        {tab === "usage" ? <UsageTab canEdit={isAdmin} /> : null}
        {tab === "mcp" ? <McpTab isAdmin={isAdmin} /> : null}
        {tab === "runs" ? (
          <RunsTab approval={approval} run={run} open={(t) => go({ tab: "runs", ...t })} />
        ) : null}
        {tab === "tokens" ? <TokensTab me={me.data} /> : null}
        {tab === "maintenance" ? <MaintenanceTab /> : null}
        {tab === "settings" ? <SettingsTab scopes={scopes} /> : null}
      </div>
    </div>
  );
}
