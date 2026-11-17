import type { SourcePurpose } from "@career-brain/client";
import { Sheet } from "@career-brain/ui";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CreateExperienceForm, EMPTY_EXPERIENCE } from "./ExperienceForm";
import { UploadSheet } from "./UploadSheet";

interface IngestContext {
  /** 자료 넣기 시트를 연다. purpose = 처음 고른 용도 (사람이 바꿀 수 있다) */
  open: (purpose?: SourcePurpose, files?: File[]) => void;
  /** 경험 추가 시트를 연다 */
  openExperience: () => void;
}

const Ctx = createContext<IngestContext | null>(null);

/**
 * 어느 화면에서나 여는 두 시트: 자료 넣기(UploadSheet)와 경험 추가.
 * AppShell에 한 번 놓고, 화면·명령 팔레트는 useIngest()로 연다.
 */
export function IngestProvider({ children }: { children: ReactNode }) {
  const [upload, setUpload] = useState<{ open: boolean; purpose: SourcePurpose; files: File[] | null }>({
    open: false,
    purpose: "experience",
    files: null,
  });
  const [creating, setCreating] = useState(false);
  const open = useCallback(
    (purpose: SourcePurpose = "experience", files?: File[]) => setUpload({ open: true, purpose, files: files ?? null }),
    [],
  );
  const openExperience = useCallback(() => setCreating(true), []);
  const value = useMemo(() => ({ open, openExperience }), [open, openExperience]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {/* 용도마다 따로 둔다: 처음 고른 용도는 시트가 처음 그려질 때만 읽힌다 */}
      <UploadSheet
        key={upload.purpose}
        open={upload.open}
        purpose={upload.purpose}
        initialFiles={upload.files}
        onClose={() => setUpload((u) => ({ ...u, open: false }))}
      />
      <Sheet open={creating} onOpenChange={setCreating} title="경험 추가">
        {creating ? <CreateExperienceForm initial={EMPTY_EXPERIENCE} onDone={() => setCreating(false)} /> : null}
      </Sheet>
    </Ctx.Provider>
  );
}

export function useIngest(): IngestContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useIngest는 IngestProvider 안에서만 씁니다");
  return ctx;
}
