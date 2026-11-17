import { Spinner } from "@career-brain/ui";
import { useEffect, useRef, useState } from "react";

/** A4 너비 (210mm, CSS 96dpi) */
const A4_PX = 794;
const A4_PAGE_PX = 1123;

/**
 * 미리보기 = 내보낸 HTML 파일. 서버가 만든 HTML을 sandbox iframe(srcdoc)에 그대로 띄운다. 파일 안의 스크립트가 A4 쪽을
 * 나누고 쪽 수·높이를 알려 준다 (postMessage). 인쇄도 이 iframe 안에서 하므로 화면과 종이가 같다.
 * 좁은 화면에서는 A4 너비를 칸에 맞게 줄여 보인다 (가로 넘침 없음).
 */
export function ResumePreview({
  html,
  printNonce,
  onPages,
}: {
  html: string | undefined;
  /** 바뀔 때마다 인쇄 (iframe이 준비되면) */
  printNonce: number;
  onPages?: (pages: number) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const [width, setWidth] = useState(A4_PX);
  const [height, setHeight] = useState(A4_PAGE_PX);
  const [ready, setReady] = useState(false);
  const pendingPrint = useRef(0);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    setReady(false);
  }, [html]);

  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return;
      const d = e.data as { type?: string; pages?: number; height?: number } | null;
      if (!d || d.type !== "career-resume") return;
      if (typeof d.height === "number" && d.height > 0) setHeight(d.height);
      if (typeof d.pages === "number") onPages?.(d.pages);
      setReady(true);
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [onPages]);

  useEffect(() => {
    if (!printNonce) return;
    pendingPrint.current = printNonce;
  }, [printNonce]);

  useEffect(() => {
    if (ready && pendingPrint.current) {
      pendingPrint.current = 0;
      frame.current?.contentWindow?.postMessage({ type: "career-resume:print" }, "*");
    }
  }, [ready, printNonce]);

  const scale = Math.min(1, width / A4_PX);
  return (
    <div ref={wrap} className="relative w-full overflow-hidden" style={{ height: html ? height * scale : 320 }}>
      {html ? (
        <iframe
          ref={frame}
          title="이력서 미리보기"
          srcDoc={html}
          sandbox="allow-scripts allow-modals"
          className="absolute top-0 left-0 origin-top-left border-0 bg-transparent"
          style={{ width: A4_PX, height, transform: `scale(${scale})` }}
        />
      ) : null}
      {!html || !ready ? (
        <div className="absolute inset-x-0 top-6 flex items-center justify-center gap-2 text-[13px] text-muted">
          <Spinner /> 미리보기를 만드는 중
        </div>
      ) : null}
    </div>
  );
}
