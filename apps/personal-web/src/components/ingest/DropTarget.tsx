import { FileUp } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { hasFiles } from "../UploadSheet";

/**
 * 화면 어디에 파일을 끌어다 놓아도 브레인에 넣기 창으로 보낸다 (c0039). 놓는 동안만 화면 전체에 안내를 띄운다.
 * 글·링크를 끌 때는 아무것도 하지 않는다 (파일만).
 */
export function IngestDropTarget({ onFiles, children }: { onFiles: (files: File[]) => void; children: ReactNode }) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  return (
    <div
      onDragEnter={(e) => {
        if (!hasFiles(e)) return;
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(e) => {
        if (hasFiles(e)) e.preventDefault();
      }}
      onDragLeave={(e) => {
        if (!hasFiles(e)) return;
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) setOver(false);
      }}
      onDrop={(e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth.current = 0;
        setOver(false);
        const files = Array.from(e.dataTransfer.files);
        if (files.length) onFiles(files);
      }}
    >
      {children}
      {over ? (
        <div
          className="pointer-events-none fixed inset-0 z-30 flex items-center justify-center bg-bg/85 p-6"
          role="status"
        >
          <div className="flex flex-col items-center gap-2 text-center">
            <FileUp size={22} aria-hidden />
            <p className="text-[15px] font-semibold">놓으면 브레인에 넣습니다</p>
            <p className="text-[13px] text-muted">무엇인지는 앱이 고르고, 모르는 것만 묻습니다.</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
