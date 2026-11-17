import type {
  ModelBilling,
  ModelConnection,
  ModelConnectionIn,
  ModelConnectionPatch,
  ModelConnectionTest,
  ModelsStatus,
} from "@career-brain/client";
import { Button, ConfirmDialog, cn, Input, Label, Select, Sheet, Spinner } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/dates";
import { ERROR_TEXT } from "../../lib/labels";
import {
  BILLING_LABEL,
  connectionKindLabel,
  connectionKindOf,
  dataClassText,
  DEFAULT_BASE_URL,
  KIND_CHOICES,
  KIND_PRESET,
  keyStateText,
  OFFICIAL_PRESETS,
  type ConnectionKind,
} from "../../lib/modelLabels";
import { ErrorText } from "../common";
import { modelsErrorText, Quiet, Rows, useConnectionTest } from "./parts";

const NAME_RE = /^[a-z][a-z0-9_-]{0,39}$/;

function testErrorText(code: string | null | undefined) {
  if (!code) return "시험 실패";
  return ERROR_TEXT[code] ?? code;
}

/** 모델 연결 탭: 연결 목록과 추가·고치기·끊기·시험 */
export function ModelsTab({ status, canEdit }: { status: ModelsStatus; canEdit: boolean }) {
  const [sheet, setSheet] = useState<{ mode: "add" } | { mode: "edit"; conn: ModelConnection } | null>(null);
  const [removing, setRemoving] = useState<ModelConnection | null>(null);
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: (name: string) => api.deleteModelConnection(name),
    onSuccess: (_r, name) => {
      setRemoving(null);
      qc.removeQueries({ queryKey: ["model-test", name] });
      void qc.invalidateQueries({ queryKey: ["models-status"] });
    },
  });
  const rows = status.connections;
  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 flex-1 text-[13px] text-muted">
          모델을 부를 곳입니다. models.toml에 적은 연결은 읽기 전용으로 보이고, 화면에서 만든 연결은 이 공간에 둡니다
          (키는 암호화해 두고 다시 보여 주지 않습니다). 저장하면 재시작 없이 바로 씁니다.
        </p>
        {canEdit ? (
          <Button variant="primary" onClick={() => setSheet({ mode: "add" })}>
            연결 추가
          </Button>
        ) : null}
      </div>

      {rows.length ? (
        <Rows>
          {rows.map((c) => (
            <ConnectionRow
              key={c.name}
              conn={c}
              canEdit={canEdit}
              onEdit={() => setSheet({ mode: "edit", conn: c })}
              onRemove={() => setRemoving(c)}
            />
          ))}
        </Rows>
      ) : (
        <Quiet>
          연결이 없습니다. "연결 추가"로 공식 API 키, 구독 계정 프록시, 로컬 모델 중 하나를 이으세요. models.toml에 적은
          연결도 여기 보입니다.
        </Quiet>
      )}

      <p className="text-[13px] text-muted">
        시험은 과금되지 않는 모델 목록 조회(<code>GET /models</code>)만 부릅니다. 실제 생성 호출로 확인하려면{" "}
        <code>personal-api ping</code>을 쓰세요 (짧은 요청이지만 과금됩니다).
      </p>

      <ConnectionSheet
        key={sheet ? (sheet.mode === "edit" ? `edit:${sheet.conn.name}` : "add") : "closed"}
        state={sheet}
        status={status}
        onClose={() => setSheet(null)}
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRemoving(null);
            remove.reset();
          }
        }}
        title={`연결 "${removing?.name ?? ""}" 끊기`}
        description="이 연결과 저장한 키를 지웁니다. 되돌릴 수 없습니다. 이 연결을 쓰는 용도별 모델이 있으면 먼저 바꾸어야 합니다."
        confirmLabel="끊기"
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.name)}
      >
        <ErrorText error={remove.error} />
      </ConfirmDialog>
    </div>
  );
}

function ConnectionRow({
  conn: c,
  canEdit,
  onEdit,
  onRemove,
}: {
  conn: ModelConnection;
  canEdit: boolean;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const qc = useQueryClient();
  const test = useConnectionTest(c.name);
  const run = async () => {
    await test.refetch();
    void qc.invalidateQueries({ queryKey: ["models-status"] });
  };
  // 행동이 필요한 상태(키 없음·다시 넣기·보낼 자료 없음)만 진하게
  const keyNeedsWork = c.key === "missing" || c.key === "needs_reentry";
  const fresh = test.data;
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:gap-4">
      <div className="min-w-0 flex-1 space-y-0.5 text-[13px]">
        <div className="text-sm">
          <span className="font-medium break-all">{c.name}</span>
          <span className="text-muted"> · {connectionKindLabel(c)}</span>
        </div>
        <div className="text-muted">
          {BILLING_LABEL[c.billing]}
          {c.billing === "subscription" ? (c.price_as ? ` (환산 기준 ${c.price_as})` : " (환산 안 함)") : null}
          {!c.leaves_host ? " · 이 기계 밖으로 나가지 않음" : null}
          {c.source === "file" ? " · 파일에서 옴 · 읽기 전용" : null}
        </div>
        <div className={c.data_classes.length ? "text-muted" : "font-medium text-fg"}>
          보낼 수 있는 자료: {dataClassText(c.data_classes)}
        </div>
        <div className="text-muted">
          키: <span className={keyNeedsWork ? "font-medium text-fg" : undefined}>{keyStateText(c)}</span>
          {" · "}
          <span className="tabular-nums">
            {c.last_test_at
              ? `마지막 시험 ${c.last_test_ok ? "통과" : "실패"} ${formatDateTime(c.last_test_at)}`
              : "아직 시험 안 함"}
          </span>
        </div>
        {test.isFetching ? (
          <div className="flex items-center gap-2 text-muted">
            <Spinner /> 모델 목록을 묻는 중
          </div>
        ) : test.isError ? (
          <p role="alert" className="text-danger">
            {modelsErrorText(test.error)}
          </p>
        ) : fresh ? (
          <TestResult result={fresh} />
        ) : null}
      </div>
      <div className="flex shrink-0 flex-wrap gap-1.5">
        <Button size="sm" onClick={() => void run()} disabled={test.isFetching}>
          시험
        </Button>
        {canEdit && c.source === "ui" ? (
          <>
            <Button size="sm" variant="ghost" onClick={onEdit}>
              고치기
            </Button>
            <Button size="sm" variant="danger" onClick={onRemove}>
              끊기
            </Button>
          </>
        ) : null}
      </div>
    </li>
  );
}

function TestResult({ result }: { result: ModelConnectionTest }) {
  if (!result.ok)
    return (
      <p role="alert" className="text-danger">
        {testErrorText(result.error_code)}
      </p>
    );
  const shown = result.models.slice(0, 12);
  return (
    <p className="break-words">
      <span className="font-medium">통과 · 모델 {result.models.length}개</span>
      {shown.length ? <span className="text-muted"> — {shown.join(", ")}</span> : null}
      {result.models.length > shown.length ? <span className="text-muted"> 외 {result.models.length - shown.length}개</span> : null}
    </p>
  );
}

// ---------------------------------------------------------------- 추가·고치기 시트

interface Draft {
  kind: ConnectionKind;
  preset: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  clearKey: boolean;
  billing: ModelBilling;
  priceAs: string;
  dataClasses: string[];
  tokenParam: string;
}

const SUGGESTED_NAME: Record<ConnectionKind, string> = {
  official: "",
  subscription: "proxy",
  litellm: "litellm",
  ollama: "local",
  custom: "other",
};

function freshDraft(kind: ConnectionKind, taken: Set<string>): Draft {
  const preset = kind === "official" ? "anthropic" : KIND_PRESET[kind];
  let name = kind === "official" ? preset : SUGGESTED_NAME[kind];
  for (let i = 2; taken.has(name); i++) name = `${kind === "official" ? preset : SUGGESTED_NAME[kind]}-${i}`;
  return {
    kind,
    preset,
    name,
    baseUrl: DEFAULT_BASE_URL[kind] ?? "",
    apiKey: "",
    clearKey: false,
    billing: kind === "subscription" ? "subscription" : kind === "ollama" ? "free" : "metered",
    priceAs: kind === "subscription" ? "anthropic" : "",
    dataClasses: [],
    tokenParam: "",
  };
}

function draftFrom(c: ModelConnection): Draft {
  return {
    kind: connectionKindOf(c),
    preset: c.preset,
    name: c.name,
    baseUrl: c.base_url,
    apiKey: "",
    clearKey: false,
    billing: c.billing,
    priceAs: c.price_as ?? "",
    dataClasses: c.data_classes.filter((d) => d !== "restricted"),
    tokenParam: "",
  };
}

function ConnectionSheet({
  state,
  status,
  onClose,
}: {
  state: { mode: "add" } | { mode: "edit"; conn: ModelConnection } | null;
  status: ModelsStatus;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const editing = state?.mode === "edit" ? state.conn : null;
  const taken = new Set(status.connections.map((c) => c.name));
  const [draft, setDraft] = useState<Draft | null>(editing ? draftFrom(editing) : null);
  const [saved, setSaved] = useState<{ name: string; test: ModelConnectionTest | null; testError: unknown } | null>(
    null,
  );
  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      if (editing) await api.updateModelConnection(editing.name, patchBody(d, editing));
      else await api.createModelConnection(createBody(d));
      // 저장한 뒤 바로 시험한다 (과금 없는 GET /models)
      try {
        const test = await api.testModelConnection(d.name);
        return { name: d.name, test, testError: null };
      } catch (e) {
        return { name: d.name, test: null, testError: e };
      }
    },
    onSuccess: (res) => {
      setDraft((d) => (d ? { ...d, apiKey: "", clearKey: false } : d)); // 키는 화면에 남기지 않는다
      setSaved(res);
      if (res.test) qc.setQueryData(["model-test", res.name], res.test);
      void qc.invalidateQueries({ queryKey: ["models-status"] });
    },
  });

  const title = saved ? `연결 "${saved.name}" 저장함` : editing ? `연결 "${editing.name}" 고치기` : "연결 추가";
  return (
    <Sheet
      open={state !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={title}
      description={editing ? connectionKindLabel(editing) : draft ? KIND_CHOICES.find((k) => k.kind === draft.kind)?.label : "종류 고르기"}
    >
      {saved ? (
        <SavedStep saved={saved} onClose={onClose} />
      ) : !draft ? (
        <KindStep onPick={(kind) => setDraft(freshDraft(kind, taken))} />
      ) : (
        <form
          className="space-y-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (validate(draft, editing, taken) === null) save.mutate(draft);
          }}
        >
          {!editing ? (
            <button
              type="button"
              onClick={() => setDraft(null)}
              className="-ml-1 inline-flex min-h-9 items-center gap-0.5 text-[13px] text-muted hover:text-fg"
            >
              <ChevronLeft size={16} /> 종류 다시 고르기
            </button>
          ) : null}
          <KindNotes kind={draft.kind} />
          <DraftFields draft={draft} set={set} editing={editing} status={status} />
          {validate(draft, editing, taken) && draft.name ? (
            <p className="text-[13px] text-muted">{validate(draft, editing, taken)}</p>
          ) : null}
          <ErrorText error={save.error} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={validate(draft, editing, taken) !== null || save.isPending}>
              {save.isPending ? "저장하고 시험하는 중" : "저장하고 시험"}
            </Button>
            <Button variant="ghost" onClick={onClose}>
              취소
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}

function needsKey(d: Draft) {
  return d.kind === "official";
}

function validate(d: Draft, editing: ModelConnection | null, taken: Set<string>): string | null {
  if (!editing) {
    if (!NAME_RE.test(d.name)) return "이름은 영문 소문자로 시작하고 소문자·숫자·-·_ 만 씁니다 (40자까지).";
    if (taken.has(d.name)) return "같은 이름의 연결이 이미 있습니다.";
    if (needsKey(d) && !d.apiKey.trim()) return "API 키를 넣어 주세요.";
  }
  if (d.kind !== "official" && !/^https?:\/\/\S+$/.test(d.baseUrl.trim())) return "주소는 http:// 또는 https://로 시작합니다.";
  return null;
}

function createBody(d: Draft): ModelConnectionIn {
  const body: ModelConnectionIn = { name: d.name, preset: d.preset, data_classes: d.dataClasses };
  if (d.kind !== "official") body.base_url = d.baseUrl.trim();
  if (d.apiKey.trim()) body.api_key = d.apiKey.trim();
  if (d.kind === "litellm" || d.kind === "custom") body.billing = d.billing;
  if ((d.kind === "subscription" || d.kind === "litellm") && d.priceAs) body.price_as = d.priceAs;
  if (d.kind === "custom" && d.tokenParam) body.token_param = d.tokenParam;
  return body;
}

function patchBody(d: Draft, c: ModelConnection): ModelConnectionPatch {
  const body: ModelConnectionPatch = { data_classes: d.dataClasses };
  if (d.kind !== "official" && d.baseUrl.trim() !== c.base_url) body.base_url = d.baseUrl.trim();
  if (d.apiKey.trim()) body.api_key = d.apiKey.trim();
  else if (d.clearKey) body.api_key = null;
  if ((d.kind === "litellm" || d.kind === "custom") && d.billing !== c.billing) body.billing = d.billing;
  if ((d.kind === "subscription" || d.kind === "litellm") && (d.priceAs || null) !== c.price_as)
    body.price_as = d.priceAs || null;
  if (d.kind === "custom" && d.tokenParam) body.token_param = d.tokenParam;
  return body;
}

function KindStep({ onPick }: { onPick: (kind: ConnectionKind) => void }) {
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-muted">어디로 모델을 부를지 고르세요. 종류마다 묻는 칸이 다릅니다.</p>
      <Rows>
        {KIND_CHOICES.map((k) => (
          <li key={k.kind}>
            <button
              type="button"
              onClick={() => onPick(k.kind)}
              className="-mx-2 flex min-h-11 w-[calc(100%+1rem)] flex-col items-start rounded-md px-2 py-2.5 text-left transition-colors duration-150 hover:bg-subtle/60"
            >
              <span className="text-sm font-medium">{k.label}</span>
              <span className="text-[13px] text-muted">{k.hint}</span>
            </button>
          </li>
        ))}
      </Rows>
    </div>
  );
}

/** 종류마다 꼭 알아야 할 것 (구독 계정은 plan §21·§12의 문장을 그대로) */
function KindNotes({ kind }: { kind: ConnectionKind }) {
  if (kind === "subscription")
    return (
      <ul className="space-y-1.5 border-y border-line py-3 text-[13px]">
        <li>
          <span className="font-medium">로그인은 CLIProxyAPI에서 합니다.</span>{" "}
          <span className="text-muted">
            이 앱은 ChatGPT·Claude·Gemini 구독 OAuth를 직접 하지 않습니다 (다른 CLI의 클라이언트 ID를 빌려 쓰는 일이 되고,
            약관·보안 책임이 이 앱으로 넘어오기 때문). 여기에는 프록시 주소와 키만 넣습니다.
          </span>
        </li>
        <li className="font-medium">소비자 구독을 API처럼 쓰는 것은 제공자 약관에 어긋날 수 있습니다. 본인 책임으로 판단하세요.</li>
        <li className="text-muted">
          CLIProxyAPI 기본 설정은 모든 네트워크 인터페이스에 열립니다. <code>config.yaml</code>에{" "}
          <code>host: "127.0.0.1"</code>을 두세요.
        </li>
      </ul>
    );
  if (kind === "ollama")
    return (
      <p className="border-y border-line py-3 text-[13px] text-muted">
        무료이고, 주소가 이 기계(127.0.0.1·localhost)면 자료가 밖으로 나가지 않습니다. 다른 기계의 주소를 넣으면 자료가
        그 서버로 갑니다.
      </p>
    );
  if (kind === "litellm")
    return (
      <p className="border-y border-line py-3 text-[13px] text-muted">
        LiteLLM이 뒤에서 어떤 제공자를 부르는지에 따라 과금 방식을 고르세요. 구독 계정을 잇는 경우 약관 주의는 구독 계정과
        같습니다.
      </p>
    );
  return null;
}

function DraftFields({
  draft: d,
  set,
  editing,
  status,
}: {
  draft: Draft;
  set: (patch: Partial<Draft>) => void;
  editing: ModelConnection | null;
  status: ModelsStatus;
}) {
  const fid = useId();
  const priceChoices = status.presets.filter((p) => p.billing === "metered").map((p) => p.name);
  const priceOptions = priceChoices.length ? priceChoices : OFFICIAL_PRESETS.map(([p]) => p);
  return (
    <div className="space-y-4">
      {d.kind === "official" && !editing ? (
        <Field label="제공자" id={`${fid}-preset`}>
          <Select
            id={`${fid}-preset`}
            value={d.preset}
            onChange={(e) => {
              const preset = e.target.value;
              // 이름을 손대지 않았으면 제공자 이름을 따라간다
              set({ preset, ...(OFFICIAL_PRESETS.some(([p]) => p === d.name) ? { name: preset } : {}) });
            }}
          >
            {OFFICIAL_PRESETS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}

      <Field label="이름" id={`${fid}-name`} hint={editing ? "이름은 바꿀 수 없습니다." : "용도별 모델에서 \"이름/모델\"로 씁니다."}>
        <Input
          id={`${fid}-name`}
          value={d.name}
          disabled={!!editing}
          maxLength={40}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => set({ name: e.target.value.trim() })}
        />
      </Field>

      {d.kind !== "official" ? (
        <Field label={d.kind === "subscription" ? "프록시 주소" : "주소"} id={`${fid}-url`}>
          <Input
            id={`${fid}-url`}
            value={d.baseUrl}
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder={DEFAULT_BASE_URL[d.kind] ?? "https://example.invalid/v1"}
            onChange={(e) => set({ baseUrl: e.target.value })}
          />
        </Field>
      ) : null}

      {d.kind !== "ollama" ? (
        <KeyField draft={d} set={set} editing={editing} id={`${fid}-key`} />
      ) : null}

      {d.kind === "litellm" || d.kind === "custom" ? (
        <Field label="과금 방식" id={`${fid}-billing`}>
          <Select id={`${fid}-billing`} value={d.billing} onChange={(e) => set({ billing: e.target.value as ModelBilling })}>
            <option value="metered">종량제 (단가표로 계산, 월 예산에 듦)</option>
            <option value="subscription">구독 (실지출 0, 환산만)</option>
            {d.kind === "custom" ? <option value="free">무료</option> : null}
          </Select>
        </Field>
      ) : null}

      {d.kind === "subscription" || d.kind === "litellm" ? (
        <Field
          label={d.kind === "subscription" ? "환산 기준" : "단가 기준"}
          id={`${fid}-price`}
          hint={
            d.kind === "subscription"
              ? "\"API였다면 얼마\"를 이 제공자의 단가로 계산해 사용량에 따로 보입니다."
              : "이 제공자의 단가표로 비용을 셉니다."
          }
        >
          <Select id={`${fid}-price`} value={d.priceAs} onChange={(e) => set({ priceAs: e.target.value })}>
            <option value="">{d.kind === "subscription" ? "환산 안 함" : "정하지 않음"}</option>
            {priceOptions.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}

      {d.kind === "custom" ? (
        <Field label="토큰 파라미터" id={`${fid}-tp`} hint="최대 출력 토큰을 어떤 이름으로 보낼지. 서버가 max_tokens를 거부할 때만 바꿉니다.">
          <Select id={`${fid}-tp`} value={d.tokenParam} onChange={(e) => set({ tokenParam: e.target.value })}>
            <option value="">기본</option>
            <option value="max_tokens">max_tokens</option>
            <option value="max_completion_tokens">max_completion_tokens</option>
          </Select>
        </Field>
      ) : null}

      <DataClassField value={d.dataClasses} onChange={(dataClasses) => set({ dataClasses })} />
    </div>
  );
}

function KeyField({
  draft: d,
  set,
  editing,
  id,
}: {
  draft: Draft;
  set: (patch: Partial<Draft>) => void;
  editing: ModelConnection | null;
  id: string;
}) {
  const label = d.kind === "subscription" ? "프록시 키" : d.kind === "official" ? "API 키" : "키 (없으면 비움)";
  const hint =
    d.kind === "subscription"
      ? "CLIProxyAPI config.yaml의 access.api-keys 값."
      : "저장하면 암호화해 두고, 다시 보여 주지 않습니다 (끝 4자리만).";
  return (
    <Field
      label={label}
      id={id}
      hint={
        editing ? (
          <>
            지금: {keyStateText(editing)}. 바꿀 때만 넣으세요. {hint}
          </>
        ) : (
          hint
        )
      }
    >
      <Input
        id={id}
        type="password"
        autoComplete="new-password"
        spellCheck={false}
        value={d.apiKey}
        disabled={d.clearKey}
        placeholder={editing ? "그대로 두려면 비움" : undefined}
        onChange={(e) => set({ apiKey: e.target.value })}
      />
      {editing && editing.key === "set" ? (
        <label className="mt-1.5 flex min-h-9 items-center gap-2 text-[13px] sm:min-h-0">
          <input
            type="checkbox"
            checked={d.clearKey}
            onChange={(e) => set({ clearKey: e.target.checked, apiKey: "" })}
          />
          저장한 키 지우기
        </label>
      ) : null}
    </Field>
  );
}

const DATA_CLASSES: { value: string; label: string; hint: string }[] = [
  { value: "public", label: "공개", hint: "공고 본문처럼 이미 공개된 글" },
  { value: "private", label: "개인", hint: "내 경험·문서·인터뷰 답" },
];

function DataClassField({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const toggle = (v: string, on: boolean) => onChange(on ? [...value.filter((x) => x !== v), v] : value.filter((x) => x !== v));
  return (
    <fieldset className="space-y-1">
      <legend className="text-[13px] font-medium text-muted">보낼 수 있는 자료</legend>
      {DATA_CLASSES.map((c) => (
        <label key={c.value} className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={value.includes(c.value)} onChange={(e) => toggle(c.value, e.target.checked)} />
          <span>
            {c.label} <span className="text-[13px] text-muted">— {c.hint}</span>
          </span>
        </label>
      ))}
      <label className="flex min-h-9 items-center gap-2 text-sm text-muted">
        <input type="checkbox" checked={false} disabled />
        <span>
          민감 <span className="text-[13px]">— 고를 수 없음. 증빙·식별 정보는 어떤 모델 연결로도 보내지 않습니다.</span>
        </span>
      </label>
      <p className={cn("text-[13px]", value.length ? "text-muted" : "font-medium")}>
        {value.length ? "고른 등급의 자료만 이 연결로 보냅니다." : "아무것도 고르지 않으면 이 연결로는 호출하지 않습니다 (거부)."}
      </p>
    </fieldset>
  );
}

function Field({ label, id, hint, children }: { label: ReactNode; id: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? <p className="text-[13px] text-muted">{hint}</p> : null}
    </div>
  );
}

function SavedStep({
  saved,
  onClose,
}: {
  saved: { name: string; test: ModelConnectionTest | null; testError: unknown };
  onClose: () => void;
}) {
  return (
    <div className="space-y-4 text-[13px]">
      <p>저장했습니다. 바로 씁니다.</p>
      <div className="border-y border-line py-3">
        <p className="mb-1 text-muted">시험 (과금 없는 모델 목록 조회)</p>
        {saved.test ? (
          <TestResult result={saved.test} />
        ) : (
          <p role="alert" className="text-danger">
            {modelsErrorText(saved.testError)}
          </p>
        )}
      </div>
      {saved.test?.ok ? (
        <p className="text-muted">용도별 모델 탭에서 이 연결의 모델을 고르면 그 작업이 이 연결로 갑니다.</p>
      ) : (
        <p className="text-muted">주소·키를 확인한 뒤 목록의 "고치기"로 다시 저장하거나 "시험"을 누르세요.</p>
      )}
      <Button variant="primary" onClick={onClose}>
        닫기
      </Button>
    </div>
  );
}
