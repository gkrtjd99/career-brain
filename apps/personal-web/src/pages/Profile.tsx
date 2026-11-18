import { PHOTO_SPECS, type Profile, type ProfileIn, type ProfilePhoto, type ProfileSuggestion } from "@career-brain/client";
import { Button, cn, ConfirmDialog, DateField, IconButton, Input, Label, Section, toast } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Plus, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { ProfileDetailsSection } from "../components/profile/ProfileDetailsSection";
import { api } from "../lib/api";
import { formatKB } from "../lib/photo";
import { PHOTO_SPEC_LABEL, profileErrorText } from "../lib/profile";

/**
 * 브레인 › 나 › 프로필 (O21). 인적 사항·연락처·링크와 규격별 증명사진.
 * 증명사진은 사진 변환(`/tools/photo`)의 "프로필에 저장"으로만 들어온다 (변환본만, 메타데이터 없음).
 */

function message(error: unknown) {
  return profileErrorText(error) ?? errorText(error);
}

export function ProfilePage() {
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  return (
    <div className="max-w-2xl">
      <PageHeader
        title="프로필"
        back={{ to: "/brain", label: "브레인" }}
        subtitle="연결한 AI 에이전트에는 이름·한 줄 소개·링크만 보입니다."
      />
      <div className="space-y-12">
        <Loaded query={profile}>{(p) => <ProfileForm initial={p} />}</Loaded>
        <ProfileDetailsSection />
        <PhotosSection />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 기본 정보·연락처·링크

interface Values {
  name: string;
  name_en: string;
  birth_date: string;
  headline: string;
  email: string;
  phone: string;
  address: string;
  links: { key: number; label: string; url: string }[];
}

let linkKey = 0;

function toValues(p: Profile): Values {
  return {
    name: p.name ?? "",
    name_en: p.name_en ?? "",
    birth_date: p.birth_date ?? "",
    headline: p.headline ?? "",
    email: p.email ?? "",
    phone: p.phone ?? "",
    address: p.address ?? "",
    links: p.links.map((l) => ({ key: ++linkKey, label: l.label, url: l.url })),
  };
}

function toBody(v: Values): ProfileIn {
  const t = (s: string) => s.trim() || null;
  return {
    name: t(v.name),
    name_en: t(v.name_en),
    birth_date: v.birth_date || null,
    headline: t(v.headline),
    email: t(v.email),
    phone: t(v.phone),
    address: t(v.address),
    links: v.links
      .map((l) => ({ label: l.label.trim(), url: l.url.trim() }))
      .filter((l) => l.label || l.url),
  };
}

function ProfileForm({ initial }: { initial: Profile }) {
  const qc = useQueryClient();
  const [v, setV] = useState(() => toValues(initial));
  const base = JSON.stringify(toBody(toValues(initial)));
  const dirty = JSON.stringify(toBody(v)) !== base;
  const save = useMutation({
    mutationFn: () => api.saveProfile(toBody(v)),
    onSuccess: (p) => {
      qc.setQueryData(["profile"], p);
      void qc.invalidateQueries({ queryKey: ["profile-suggestions"] });
      setV(toValues(p)); // 서버가 다듬은 값 (공백·https:// 붙이기)
      toast("저장했습니다.");
    },
  });
  const set = <K extends keyof Values>(key: K, value: Values[K]) => {
    setV((prev) => ({ ...prev, [key]: value }));
  };
  const setLink = (key: number, patch: Partial<{ label: string; url: string }>) =>
    set(
      "links",
      v.links.map((l) => (l.key === key ? { ...l, ...patch } : l)),
    );
  const field = (
    key: "name" | "name_en" | "headline" | "email" | "phone" | "address",
    label: ReactNode,
    props: { placeholder?: string; type?: string; maxLength: number; autoComplete?: string },
    className?: string,
  ) => (
    <div className={className}>
      <Label htmlFor={`profile-${key}`}>{label}</Label>
      <Input id={`profile-${key}`} value={v[key]} onChange={(e) => set(key, e.target.value)} {...props} />
    </div>
  );

  return (
    <form
      className="space-y-10"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty) save.mutate();
      }}
    >
      <FromResume
        v={v}
        onFill={(sg) => {
          if (sg.field === "link") {
            if (v.links.length < 10)
              set("links", [...v.links, { key: ++linkKey, label: sg.label ?? "링크", url: sg.value }]);
          } else set(sg.field, sg.value);
        }}
      />

      <Section title="기본 정보">
        <div className="grid gap-3 sm:grid-cols-2">
          {field("name", "이름", { maxLength: 100, autoComplete: "name" })}
          {field("name_en", "영문 이름", { maxLength: 100, placeholder: "예: Gildong Hong" })}
          <div>
            <Label htmlFor="profile-birth">생년월일 (선택)</Label>
            <DateField
              id="profile-birth"
              value={v.birth_date}
              min="1900-01-01"
              max={new Date().toISOString().slice(0, 10)}
              onChange={(value) => set("birth_date", value)}
              calendar={false}
            />
          </div>
        </div>
        {field("headline", "한 줄 소개", {
          maxLength: 200,
          placeholder: "예: 데이터로 문제를 푸는 백엔드 개발자",
        })}
      </Section>

      <Section title="연락처">
        <div className="grid gap-3 sm:grid-cols-2">
          {field("email", "이메일", { type: "email", maxLength: 254, autoComplete: "email" })}
          {field("phone", "전화", { type: "tel", maxLength: 40, autoComplete: "tel" })}
        </div>
        {field("address", "주소 (선택)", { maxLength: 200, placeholder: "예: 서울 마포구" })}
      </Section>

      <Section
        title="링크"
        action={
          <Button
            size="sm"
            variant="ghost"
            disabled={v.links.length >= 10}
            onClick={() => set("links", [...v.links, { key: ++linkKey, label: "", url: "" }])}
          >
            <Plus size={15} /> 링크 추가
          </Button>
        }
      >
        {v.links.length ? (
          <ul className="divide-y divide-line border-y border-line">
            {v.links.map((l, i) => (
              <li key={l.key} className="flex items-center gap-2 py-2">
                <Input
                  aria-label={`링크 ${i + 1} 이름`}
                  value={l.label}
                  placeholder="GitHub"
                  maxLength={40}
                  onChange={(e) => setLink(l.key, { label: e.target.value })}
                  className="w-24 shrink-0 sm:w-36"
                />
                <Input
                  aria-label={`링크 ${i + 1} 주소`}
                  inputMode="url"
                  autoCapitalize="off"
                  spellCheck={false}
                  value={l.url}
                  placeholder="https://"
                  maxLength={500}
                  onChange={(e) => setLink(l.key, { url: e.target.value })}
                  className="min-w-0 flex-1"
                />
                <IconButton
                 
                 
                  label={`링크 ${i + 1} 삭제`}
                  onClick={() =>
                    set(
                      "links",
                      v.links.filter((x) => x.key !== l.key),
                    )
                  }
                >
                  <X size={16} />
                </IconButton>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border-t border-line pt-3 text-sm text-muted">
            포트폴리오·GitHub·블로그 주소를 둡니다. 이력서의 프로필 구역에 함께 나옵니다.
          </p>
        )}
      </Section>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
          {save.isPending ? "저장하는 중…" : "저장"}
        </Button>
        {dirty ? (
          <Button variant="ghost" onClick={() => setV(toValues(initial))} disabled={save.isPending}>
            취소
          </Button>
        ) : null}
        {save.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {message(save.error)}
          </p>
) : null}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- 증명사진

function PhotosSection() {
  const photos = useQuery({ queryKey: ["profile", "photos"], queryFn: api.profilePhotos });
  const [removing, setRemoving] = useState<ProfilePhoto | null>(null);
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["profile"] });
  const setDefault = useMutation({ mutationFn: api.setDefaultProfilePhoto, onSuccess: refresh });
  const remove = useMutation({
    mutationFn: api.deleteProfilePhoto,
    onSuccess: () => {
      setRemoving(null);
      return refresh();
    },
  });
  const error = setDefault.error ?? remove.error;

  return (
    <Section
      title="증명사진"
      action={
        <Link
          to="/tools/photo"
          className="inline-flex min-h-9 items-center gap-0.5 text-[13px] text-muted hover:text-fg"
        >
          사진 변환에서 만들기 <ChevronRight size={14} />
        </Link>
      }
    >
      <p className="text-[13px] text-muted">
        사진 변환에서 만든 변환본만 보관합니다. 원본은 받지 않고, 촬영 정보 같은 메타데이터는 서버에서도 제거합니다.
      </p>
      {error ? (
        <p role="alert" className="text-[13px] text-danger">
          {message(error)}
        </p>
      ) : null}
      <Loaded query={photos}>
        {(list) =>
          list.length ? (
            <div className="space-y-6">
              {PHOTO_SPECS.filter((s) => list.some((p) => p.spec === s)).map((spec) => {
                const group = list.filter((p) => p.spec === spec);
                return (
                  <section key={spec} aria-label={PHOTO_SPEC_LABEL[spec]} className="border-t border-line pt-3">
                    <h3 className="text-[13px] font-medium text-muted">
                      {PHOTO_SPEC_LABEL[spec]}
                      {group.length > 1 ? <span className="ml-1.5 tabular-nums">{group.length}</span> : null}
                    </h3>
                    <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-5">
                      {group.map((p) => (
                        <PhotoItem
                          key={p.id}
                          photo={p}
                          busy={setDefault.isPending || remove.isPending}
                          onDefault={() => setDefault.mutate(p.id)}
                          onDelete={() => setRemoving(p)}
                        />
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
          ) : (
            <p className="border-t border-line pt-3 text-sm text-muted">
              아직 증명사진이 없습니다. 사진 변환에서 규격에 맞춰 만든 뒤 '프로필에 저장'을 누르세요.
            </p>
          )
        }
      </Loaded>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="증명사진을 삭제할까요?"
        description={
          removing?.is_default
            ? "삭제한 사진은 되돌릴 수 없습니다. 기본 사진이라 남은 사진 중 가장 최근 사진이 기본이 됩니다."
            : "삭제한 사진은 되돌릴 수 없습니다."
        }
        confirmLabel="삭제"
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.id)}
      />
    </Section>
  );
}

const ACTION =
  "-mx-1.5 inline-flex min-h-9 items-center rounded-md px-1.5 text-[13px] transition-colors duration-150 " +
  "hover:bg-subtle hover:text-fg focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50";

function PhotoItem({
  photo,
  busy,
  onDefault,
  onDelete,
}: {
  photo: ProfilePhoto;
  busy: boolean;
  onDefault: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="w-36">
      <PhotoThumb photo={photo} />
      <div className="mt-2 text-xs leading-5 text-muted tabular-nums">
        <div>
          {photo.width_px} × {photo.height_px} px
        </div>
        <div>
          {photo.mime === "image/png" ? "PNG" : "JPG"} · {formatKB(photo.bytes)}
        </div>
      </div>
      <div className="mt-0.5 flex items-center gap-2">
        {photo.is_default ? (
          <span className="inline-flex min-h-9 items-center text-[13px] font-medium">기본</span>
        ) : (
          <button type="button" className={cn(ACTION, "text-fg")} disabled={busy} onClick={onDefault}>
            기본으로 지정
          </button>
        )}
        <button type="button" className={cn(ACTION, "text-muted")} disabled={busy} onClick={onDelete}>
          삭제
        </button>
      </div>
    </li>
  );
}

/** 사진 파일은 토큰이 있어야 받으므로 바이트를 받아 object URL로 보여 준다. */
function PhotoThumb({ photo }: { photo: ProfilePhoto }) {
  const file = useQuery({
    queryKey: ["profile-photo-file", photo.id],
    queryFn: () => api.profilePhotoFile(photo.id),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file.data) return;
    const u = URL.createObjectURL(file.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file.data]);
  return (
    <div
      className="h-32 overflow-hidden rounded-sm bg-subtle shadow-[0_0_0_1px_var(--app-line)]"
      style={{ aspectRatio: `${photo.width_px} / ${photo.height_px}`, maxWidth: "100%" }}
    >
      {url ? (
        <img
          src={url}
          alt={`${PHOTO_SPEC_LABEL[photo.spec]} 증명사진${photo.is_default ? " (기본)" : ""}`}
          className="block h-full w-full object-cover"
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- 이력서에서 찾은 값

const FIELD_LABEL: Record<ProfileSuggestion["field"], string> = {
  name: "이름",
  email: "이메일",
  phone: "전화",
  link: "링크",
};

/** 내 경험 자료(이력서 등)에 적힌 이름·연락처·링크. 누르면 칸에 넣기만 하고, 저장은 사람이 누른다. */
function FromResume({ v, onFill }: { v: Values; onFill: (sg: ProfileSuggestion) => void }) {
  const q = useQuery({ queryKey: ["profile-suggestions"], queryFn: api.profileSuggestions });
  const shown = (q.data?.suggestions ?? []).filter((sg) =>
    sg.field === "link" ? !v.links.some((l) => l.url.trim() === sg.value) : v[sg.field].trim() !== sg.value,
  );
  if (!shown.length) return null;
  return (
    <section aria-label="자료에서 찾은 정보" className="space-y-2">
      <h2 className="text-[15px] font-semibold">자료에서 찾은 정보</h2>
      <p className="text-[13px] text-muted">
        내 경험 자료에 적힌 값입니다. 넣은 뒤 저장을 눌러야 프로필에 남습니다.
      </p>
      <ul className="divide-y divide-line border-y border-line">
        {shown.map((sg) => {
          const filled = sg.field !== "link" && v[sg.field].trim();
          return (
            <li key={`${sg.field}-${sg.value}`} className="flex min-h-11 items-center gap-3 py-2">
              <span className="w-12 shrink-0 text-[13px] text-muted">{sg.field === "link" ? (sg.label ?? "링크") : FIELD_LABEL[sg.field]}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{sg.value}</span>
                <span className="block truncate text-xs text-muted">원본 {sg.source_title}</span>
              </span>
              <Button size="sm" className="shrink-0 max-sm:h-9" onClick={() => onFill(sg)}>
                {filled ? "바꾸기" : "넣기"}
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
