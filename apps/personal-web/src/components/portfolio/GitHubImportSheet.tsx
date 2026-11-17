import type { PortfolioImport } from "@career-brain/client";
import { Button, Input, Label, Select, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { errorText } from "../common";
import { api } from "../../lib/api";
import { portfolioErrorText } from "../../lib/portfolio";

const errText = (e: unknown) => portfolioErrorText(e) ?? errorText(e);

const day = (v: string | null) => (v ? v.slice(0, 10).replaceAll("-", ".") : null);

/**
 * GitHub 저장소 가져오기: README를 받은 자료함에 넣어 보통 추출을 탑니다 (확인한 뒤에 근거가 됩니다).
 * 주 언어·만든 날·마지막 push는 항목 링크의 참고 정보로만 둡니다. 별·포크 수는 근거가 아닙니다.
 * 비공개 저장소는 공간 관리자가 넣은 토큰으로만 읽습니다 (토큰은 암호화해 이 기기에만 둡니다).
 */
export function GitHubImportSheet({ itemId, onClose }: { itemId?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const items = useQuery({ queryKey: ["portfolio", "items"], queryFn: () => api.portfolioItems() });
  const token = useQuery({ queryKey: ["portfolio", "github-token"], queryFn: api.gitHubToken });
  const [url, setUrl] = useState("");
  const [priv, setPriv] = useState(false);
  const [target, setTarget] = useState(itemId ?? "");
  const [done, setDone] = useState<PortfolioImport | null>(null);
  const run = useMutation({
    mutationFn: () => api.importGitHub({ url: url.trim(), private: priv, item_id: target || null }),
    onSuccess: (r) => {
      setDone(r);
      void qc.invalidateQueries({ queryKey: ["portfolio"] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title="GitHub에서 가져오기"
      description="README를 받은 자료함에 넣습니다. 거기서 뽑은 사실을 확인하면 근거가 됩니다. 별·포크 수는 근거로 쓰지 않습니다."
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          setDone(null);
          run.mutate();
        }}
      >
        <div>
          <Label htmlFor="gh-url" className="mb-1">
            저장소 주소
          </Label>
          <Input
            id="gh-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/이름/저장소"
            inputMode="url"
            autoComplete="off"
            maxLength={500}
          />
        </div>
        <div>
          <Label htmlFor="gh-item" className="mb-1">
            링크를 넣을 항목
          </Label>
          <Select id="gh-item" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">넣지 않음</option>
            {(items.data ?? []).map((it) => (
              <option key={it.id} value={it.id}>
                {it.title}
              </option>
            ))}
          </Select>
        </div>
        <label className="flex min-h-9 items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={priv}
            disabled={!token.data?.set}
            onChange={(e) => setPriv(e.target.checked)}
          />
          <span>
            비공개 저장소
            <span className="block text-[13px] text-muted">
              {token.data?.set ? `저장한 토큰(끝 ${token.data.last4})으로 읽습니다.` : "아래에 토큰을 넣으면 켤 수 있습니다."}
            </span>
          </span>
        </label>
        {run.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {errText(run.error)}
          </p>
        ) : null}
        {done ? <ImportDone r={done} /> : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" disabled={run.isPending || !url.trim()}>
            가져오기
          </Button>
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        </div>
      </form>
      <TokenSection set={!!token.data?.set} last4={token.data?.last4 ?? null} available={token.data?.available ?? false} />
    </Sheet>
  );
}

function ImportDone({ r }: { r: PortfolioImport }) {
  const info = [r.repo.language, day(r.repo.created_at) && `만든 날 ${day(r.repo.created_at)}`, day(r.repo.pushed_at) && `마지막 push ${day(r.repo.pushed_at)}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <div role="status" className="border-y border-line py-3 text-[13px]">
      <p className="text-sm font-medium">{r.repo.full_name}</p>
      {info ? <p className="text-muted tabular-nums">{info}</p> : null}
      <p className="mt-1">
        {r.readme
          ? r.deduplicated
            ? "같은 README가 이미 받은 자료함에 있습니다."
            : "README를 받은 자료함에 넣었습니다."
          : "이 저장소에는 README가 없습니다."}
        {r.item_id ? " 항목 링크에 저장소 정보를 남겼습니다." : ""}
      </p>
      {r.source_id ? (
        <Link
          to="/brain/sources/$sourceId"
          params={{ sourceId: r.source_id }}
          className="inline-flex min-h-9 items-center underline underline-offset-2 sm:min-h-6"
        >
          받은 자료 보기
        </Link>
      ) : null}
    </div>
  );
}

function TokenSection({ set, last4, available }: { set: boolean; last4: string | null; available: boolean }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const done = () => {
    setValue("");
    void qc.invalidateQueries({ queryKey: ["portfolio", "github-token"] });
  };
  const save = useMutation({ mutationFn: () => api.setGitHubToken(value.trim()), onSuccess: done });
  const remove = useMutation({ mutationFn: api.deleteGitHubToken, onSuccess: done });
  return (
    <section aria-label="GitHub 토큰" className="mt-8">
      <h3 className="text-[15px] font-semibold">GitHub 토큰</h3>
      <p className="text-[13px] text-muted">
        비공개 저장소를 읽을 때만 씁니다. 읽기 권한만 있는 토큰을 넣습니다. 암호화해 이 기기에 두고, 백업 파일에 넣지 않습니다. 공간
        관리자만 넣을 수 있습니다.
      </p>
      {set ? (
        <div className="mt-3 flex flex-wrap items-center gap-3 border-y border-line py-3 text-sm">
          <span className="min-w-0 flex-1 tabular-nums">저장한 토큰 · 끝 {last4}</span>
          <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
            토큰 삭제
          </Button>
        </div>
      ) : null}
      {available ? (
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <div className="min-w-0 flex-1">
            <Label htmlFor="gh-token" className="mb-1">
              {set ? "새 토큰으로 바꾸기" : "토큰"}
            </Label>
            <Input
              id="gh-token"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              maxLength={400}
            />
          </div>
          <Button type="submit" disabled={save.isPending || !value.trim()}>
            저장
          </Button>
        </form>
      ) : (
        <p className="mt-3 text-[13px] text-muted">이 설치에는 토큰을 암호화할 열쇠가 없어 저장할 수 없습니다.</p>
      )}
      {save.error || remove.error ? (
        <p role="alert" className="mt-2 text-[13px] text-danger">
          {errText(save.error ?? remove.error)}
        </p>
      ) : null}
    </section>
  );
}
