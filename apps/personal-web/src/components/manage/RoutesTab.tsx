import type { ModelRoute, ModelsStatus } from "@career-brain/client";
import { Button, Input, Label, Select, Spinner } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api } from "../../lib/api";
import { connectionKindLabel, NO_FALLBACK, ROUTE_PURPOSE_LABEL } from "../../lib/modelLabels";
import { ErrorText } from "../common";
import { modelsErrorText, Rows, useConnectionTest } from "./parts";

/** 기본 → 알려진 용도 → 임베딩 순. 라우트만 있고 용도 목록에 없는 것도 보인다 */
function purposeOrder(status: ModelsStatus) {
  const rest = [...status.purposes, ...status.routes.map((r) => r.purpose)].filter(
    (p) => p !== "default" && p !== "embed",
  );
  return ["default", ...new Set(rest), "embed"];
}

/** 화면 값 전체 + 바꾼 것. PUT이 덮기든 합치기든 다른 화면 값이 사라지지 않게 지금 화면 값을 함께 보낸다 */
function routesBody(status: ModelsStatus, purpose: string, value: string | null) {
  const body: Record<string, string | null> = {};
  for (const r of status.routes) if (r.source === "ui") body[r.purpose] = `${r.endpoint}/${r.model}`;
  body[purpose] = value;
  return body;
}

/** 용도별 모델 탭: 용도마다 "연결/모델" 고르기. 화면 값은 models.toml 값을 덮고, "파일 값으로"로 되돌린다 */
export function RoutesTab({ status, canEdit }: { status: ModelsStatus; canEdit: boolean }) {
  const [editing, setEditing] = useState<string | null>(null);
  const routes = new Map(status.routes.map((r) => [r.purpose, r]));
  const fallback = routes.get("default");
  return (
    <div className="max-w-3xl space-y-6">
      <p className="text-[13px] text-muted">
        작업마다 쓸 모델을 고릅니다. 정하지 않은 작업은 기본 모델을 씁니다. 검색 색인·이미지 읽기·판정은 기본으로 넘어가지
        않으니 따로 정해야 합니다.
      </p>
      <div className="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_auto] gap-4 border-b border-line pb-2 text-xs text-muted sm:grid">
        <span>작업</span>
        <span>모델</span>
        <span className="w-[9rem]" aria-hidden />
      </div>
      <Rows>
        {purposeOrder(status).map((p) => (
          <RouteRow
            key={p}
            purpose={p}
            route={routes.get(p) ?? null}
            fallback={p === "default" || NO_FALLBACK.has(p) ? null : (fallback ?? null)}
            status={status}
            canEdit={canEdit}
            editing={editing === p}
            onEdit={(on) => setEditing(on ? p : null)}
          />
        ))}
      </Rows>
      {!status.connections.length ? (
        <p className="text-[13px] text-muted">연결이 없어 고를 것이 없습니다. 모델 연결 탭에서 먼저 연결을 추가하세요.</p>
      ) : null}
    </div>
  );
}

function RouteRow({
  purpose,
  route,
  fallback,
  status,
  canEdit,
  editing,
  onEdit,
}: {
  purpose: string;
  route: ModelRoute | null;
  fallback: ModelRoute | null;
  status: ModelsStatus;
  canEdit: boolean;
  editing: boolean;
  onEdit: (on: boolean) => void;
}) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (value: string | null) => api.putModelRoutes(routesBody(status, purpose, value)),
    onSuccess: () => {
      onEdit(false);
      void qc.invalidateQueries({ queryKey: ["models-status"] });
    },
  });
  const conn = route ? status.connections.find((c) => c.name === route.endpoint) : undefined;
  const label = ROUTE_PURPOSE_LABEL[purpose];
  return (
    <li className="py-3">
      <div className="grid gap-1 text-[13px] sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_auto] sm:items-center sm:gap-4">
        <div className="text-sm font-medium">{label ?? purpose}</div>
        <div className="min-w-0">
          {route ? (
            <>
              <span className="break-all">
                {route.model}
                <span className="text-muted"> · {route.endpoint}</span>
              </span>
              {route.source === "file" ? <span className="text-muted"> · models.toml</span> : null}
            </>
          ) : (
            <span className="text-muted">
              {fallback
                ? `기본 모델 사용 (${fallback.model})`
                : purpose === "default"
                  ? "정하지 않음 — 따로 정한 작업만 실행됩니다"
                  : "정하지 않음 — 이 작업은 실행되지 않습니다"}
            </span>
          )}
          {route && !conn ? <div className="font-medium">이 연결이 없습니다. 다른 모델을 고르세요.</div> : null}
          {conn && !conn.data_classes.length ? (
            <div className="font-medium">이 연결은 보낼 수 있는 자료가 없어 실행되지 않습니다.</div>
          ) : null}
        </div>
        <div className="flex gap-1 sm:w-[9rem] sm:justify-end">
          {canEdit && !editing ? (
            <>
              <Button size="sm" variant="ghost" onClick={() => onEdit(true)} disabled={!status.connections.length}>
                변경
              </Button>
              {route?.source === "ui" ? (
                <Button size="sm" variant="ghost" disabled={save.isPending} onClick={() => save.mutate(null)}>
                  초기화
                </Button>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
      {editing ? (
        <RouteEditor
          route={route}
          status={status}
          busy={save.isPending}
          onSave={(v) => save.mutate(v)}
          onReset={route?.source === "ui" ? () => save.mutate(null) : undefined}
          onCancel={() => {
            save.reset();
            onEdit(false);
          }}
        />
      ) : null}
      <ErrorText error={save.error} className="mt-1" />
    </li>
  );
}

function RouteEditor({
  route,
  status,
  busy,
  onSave,
  onReset,
  onCancel,
}: {
  route: ModelRoute | null;
  status: ModelsStatus;
  busy: boolean;
  onSave: (value: string) => void;
  onReset?: () => void;
  onCancel: () => void;
}) {
  const fid = useId();
  const initialConn =
    route && status.connections.some((c) => c.name === route.endpoint) ? route.endpoint : (status.connections[0]?.name ?? "");
  const [conn, setConn] = useState(initialConn);
  const [model, setModel] = useState(route && route.endpoint === initialConn ? route.model : "");
  const test = useConnectionTest(conn || null);
  const models = test.data?.ok ? test.data.models : [];
  const valid = !!conn && model.trim().length > 0 && !/\s/.test(model.trim());
  return (
    <form
      className="mt-3 space-y-3 border-t border-line pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onSave(`${conn}/${model.trim()}`);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-1">
          <Label htmlFor={`${fid}-conn`}>연결</Label>
          <Select id={`${fid}-conn`} value={conn} onChange={(e) => setConn(e.target.value)}>
            {status.connections.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} · {connectionKindLabel(c)}
              </option>
            ))}
          </Select>
        </div>
        <div className="min-w-0 space-y-1">
          <Label htmlFor={`${fid}-model`}>모델</Label>
          <Input
            id={`${fid}-model`}
            value={model}
            list={`${fid}-models`}
            autoComplete="off"
            spellCheck={false}
            placeholder={models[0] ?? "모델 이름"}
            onChange={(e) => setModel(e.target.value)}
          />
          <datalist id={`${fid}-models`}>
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>
      </div>
      <div className="text-[13px] text-muted">
        {test.isFetching ? (
          <span className="inline-flex items-center gap-2">
            <Spinner /> 모델 목록을 묻는 중
          </span>
        ) : test.isError ? (
          <span className="text-danger">{modelsErrorText(test.error)}</span>
        ) : test.data && !test.data.ok ? (
          <span className="text-danger">모델 목록을 받지 못했습니다. 이름을 직접 적어도 됩니다.</span>
        ) : models.length ? (
          `이 연결의 모델 ${models.length}개에서 고르거나 직접 적습니다.`
        ) : (
          <>
            모델 이름을 직접 적거나{" "}
            <button
              type="button"
              className="inline-flex min-h-9 items-center underline underline-offset-2 hover:text-fg sm:min-h-0"
              onClick={() => void test.refetch()}
              disabled={!conn}
            >
              이 연결의 모델 목록 불러오기
            </button>{" "}
            (요금 없음).
          </>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={!valid || busy}>
          저장
        </Button>
        {onReset ? (
          <Button size="sm" disabled={busy} onClick={onReset}>
            초기화
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </div>
    </form>
  );
}
