import type { SourcePurpose } from "@career-brain/client";
import { Button, cn, Sheet, Textarea } from "@career-brain/ui";
import { FileUp } from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { ACCEPT, addFiles, addPaste, clearFinished, useIngestQueue } from "../lib/ingest";
import { IngestQueue } from "./ingest/IngestQueue";
import { OrganizeResult } from "./ingest/OrganizeResult";

/** 드래그한 것에 파일이 있나 (글·링크를 끌 때는 놓는 곳을 띄우지 않는다) */
export function hasFiles(e: DragEvent) {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

/**
 * 브레인에 넣기 (c0039). 파일 여러 개·붙여넣은 글·주소를 한 창에 넣으면 앱이 읽고 무엇인지 골라 보낸다.
 * 확신하지 못한 것만 "확인 필요"로 묻는다. 사진 증빙은 이 기기에서 읽는다.
 *
 * 이름·props는 예전 "자료 올리기"와 같다 (부르는 곳이 그대로 쓴다).
 */
export function UploadSheet({
  open,
  onClose,
  purpose,
  initialFiles,
}: {
  open: boolean;
  onClose: () => void;
  /** 화면이 정한 용도 (증빙 서류 화면에서 열면 proof). 넣는 파일마다 그 용도로 간다 */
  purpose?: SourcePurpose;
  /** 끌어다 놓은 파일 (브레인 화면 등). 열릴 때 한 번 넣는다 */
  initialFiles?: File[] | null;
}) {
  const queue = useIngestQueue();
  const fixed = purpose && purpose !== "experience" ? purpose : undefined;
  const [pasted, setPasted] = useState("");
  const [over, setOver] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const taken = useRef<File[] | null>(null);

  useEffect(() => {
    if (open && initialFiles?.length && taken.current !== initialFiles) {
      taken.current = initialFiles;
      addFiles(initialFiles, fixed);
    }
  }, [open, initialFiles, fixed]);

  const take = (files: FileList | File[] | null | undefined) => {
    if (files && files.length) addFiles(Array.from(files), fixed);
  };
  const onPaste = (e: ClipboardEvent) => {
    // 클립보드의 그림(화면 캡처 등)은 파일로 넣는다. 글은 입력 칸이 받는다
    const files = Array.from(e.clipboardData.files ?? []);
    if (files.length) {
      e.preventDefault();
      take(files);
    }
  };
  const done = queue.filter((x) => x.stage === "saved").length;

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="브레인에 넣기"
      description="넣으면 읽고 알아서 정리합니다. 증빙 서류는 이 기기 밖으로 보내지 않습니다."
      width="lg"
    >
      <div
        className="space-y-6"
        onPaste={onPaste}
        onDragOver={(e) => {
          if (!hasFiles(e)) return;
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={(e) => {
          if (e.currentTarget === e.target) setOver(false);
        }}
        onDrop={(e) => {
          if (!hasFiles(e)) return;
          e.preventDefault();
          setOver(false);
          take(e.dataTransfer.files);
        }}
      >
        <div
          className={cn(
            "flex flex-col items-center gap-3 rounded-lg border border-dashed px-4 py-7 text-center transition-colors duration-150",
            over ? "border-fg bg-subtle" : "border-line",
          )}
        >
          <FileUp size={20} aria-hidden className="text-muted" />
          <p className="text-sm">
            파일을 여기에 놓거나{" "}
            <button
              type="button"
              onClick={() => picker.current?.click()}
              className="inline-flex min-h-9 items-center font-medium underline underline-offset-2"
            >
              파일 고르기
            </button>
          </p>
          <p className="text-[13px] text-muted">PDF·DOCX·PPTX·엑셀·한글·사진·텍스트, 여러 개를 한꺼번에 넣을 수 있습니다.</p>
          <input
            ref={picker}
            type="file"
            multiple
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-hidden
            onChange={(e) => {
              take(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            addPaste(pasted, fixed);
            setPasted("");
          }}
        >
          <label htmlFor="ingest-paste" className="block text-sm font-medium">
            붙여넣기
          </label>
          <Textarea
            id="ingest-paste"
            rows={4}
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            placeholder="글이나 주소를 붙여넣으세요. 주소는 한 줄에 하나씩 넣습니다."
          />
          <div className="flex justify-end">
            <Button type="submit" variant="primary" disabled={!pasted.trim()}>
              넣기
            </Button>
          </div>
        </form>

        {queue.length ? (
          <section aria-labelledby="ingest-queue" className="space-y-2">
            <div className="flex items-baseline justify-between gap-3">
              <h3 id="ingest-queue" className="text-[15px] font-semibold">
                넣은 것 <span className="font-normal text-muted tabular-nums">{queue.length}</span>
              </h3>
              {done ? (
                <Button variant="ghost" size="sm" className="-mr-2.5" onClick={clearFinished}>
                  저장된 것 목록에서 빼기
                </Button>
              ) : null}
            </div>
            <OrganizeResult items={queue} onNavigate={onClose} />
            <IngestQueue items={queue} onNavigate={onClose} />
          </section>
        ) : null}

      </div>
    </Sheet>
  );
}
