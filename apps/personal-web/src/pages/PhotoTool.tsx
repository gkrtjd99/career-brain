import type { PhotoSpec } from "@career-brain/client";
import { Button, cn, Input, Label, Section } from "@career-brain/ui";
import { Download, ImagePlus, Lock, RotateCcw, RotateCw, Undo2 } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as RDragEvent,
  type KeyboardEvent as RKeyboardEvent,
  type PointerEvent as RPointerEvent,
  type ReactNode,
} from "react";
import { PageHeader } from "../components/common";
import { SaveToProfile } from "../components/profile/SaveToProfile";
import * as P from "../lib/photo";

/**
 * 지원서 사진 변환. 모든 처리는 이 브라우저의 canvas로 한다 — 원본은 서버로 보내지 않는다.
 * "프로필에 저장"(O21)만 변환한 파일을 보낸다 (`components/profile/SaveToProfile.tsx`).
 * 계산·파일 머리 고치기는 `lib/photo.ts`.
 */

const MAX_FILE_BYTES = 40 * 1024 * 1024;
const BACKGROUNDS: [string, string][] = [
  ["#ffffff", "흰색"],
  ["#f0f0f0", "연회색"],
  ["#dbe8f6", "하늘색"],
];
// 편집 화면의 참고 안내선 (틀 높이 비율). 어느 기관의 규격도 아니다.
const GUIDE_HEAD_TOP = 0.1;
const GUIDE_CHIN = 0.8;

interface Source {
  img: CanvasImageSource;
  width: number;
  height: number;
  name: string;
  close: () => void;
}

type SizeMode = "preset" | "px" | "cm";

interface Target {
  w: number;
  h: number;
  dpi: number;
}

interface Output {
  url: string;
  /** "프로필에 저장"이 보내는 파일 (내려받기와 같은 바이트) */
  blob: Blob;
  bytes: number;
  quality: number | null;
  /** 용량 제한이 없으면 null */
  fits: boolean | null;
  format: P.PhotoFormat;
  w: number;
  h: number;
  dpi: number;
}

class UnsupportedFormat extends Error {}

function isHeic(file: File) {
  return /hei[cf]/i.test(file.type) || /\.(heic|heif)$/i.test(file.name);
}

/** EXIF 방향을 따라 연다. createImageBitmap이 못 열면 <img>로 한 번 더 (Safari는 HEIC를 이쪽으로 연다). */
async function decodeFile(file: File): Promise<Source> {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    if (bmp.width && bmp.height)
      return { img: bmp, width: bmp.width, height: bmp.height, name: file.name, close: () => bmp.close() };
  } catch {
    /* 아래 <img> 경로 */
  }
  const url = URL.createObjectURL(file);
  try {
    const el = new Image();
    el.src = url;
    await el.decode();
    if (!el.naturalWidth || !el.naturalHeight) throw new Error("empty");
    return {
      img: el,
      width: el.naturalWidth,
      height: el.naturalHeight,
      name: file.name,
      close: () => URL.revokeObjectURL(url),
    };
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e;
  }
}

function decodeErrorText(file: File): string {
  if (isHeic(file))
    return (
      "이 브라우저는 HEIC/HEIF 사진을 열지 못합니다. 아이폰이면 사진 앱에서 JPG로 내보내거나 " +
      "설정 → 카메라 → 포맷을 '높은 호환성'으로 바꿔 찍은 뒤 다시 올려 주세요. Safari에서는 열릴 수 있습니다."
    );
  return "이 파일을 사진으로 읽지 못했습니다. JPG·PNG·WebP 파일인지 확인하세요.";
}

async function renderOutput(
  src: Source,
  target: Target,
  t: P.PhotoTransform,
  o: { format: P.PhotoFormat; quality: number; maxBytes: number | null; bg: string; keepAlpha: boolean },
): Promise<Omit<Output, "url">> {
  const { w, h, dpi } = target;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas");
  if (o.format === "jpeg" || !o.keepAlpha) {
    ctx.fillStyle = o.bg;
    ctx.fillRect(0, 0, w, h);
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  P.drawPhoto(ctx, src.img, src.width, src.height, w, h, t, { cx: w / 2, cy: h / 2, scale: 1 });
  const mime = P.FORMAT_MIME[o.format];
  const encode = async (q: number): Promise<Blob> => {
    const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, mime, q));
    if (!blob) throw new Error("encode");
    // 지원하지 않는 형식이면 브라우저는 PNG로 돌려준다 (예: 오래된 Safari의 WebP)
    if (blob.type !== mime) throw new UnsupportedFormat();
    let bytes: Uint8Array<ArrayBuffer> = new Uint8Array(await blob.arrayBuffer());
    if (o.format === "jpeg") bytes = P.setJpegDpi(bytes, dpi);
    else if (o.format === "png") bytes = P.setPngDpi(bytes, dpi);
    return new Blob([bytes], { type: mime });
  };
  const base = { format: o.format, w, h, dpi };
  if (!P.hasQuality(o.format)) {
    const blob = await encode(1);
    return {
      ...base,
      blob,
      bytes: blob.size,
      quality: null,
      fits: o.maxBytes === null ? null : blob.size <= o.maxBytes,
    };
  }
  if (o.maxBytes === null) {
    const blob = await encode(o.quality);
    return { ...base, blob, bytes: blob.size, quality: o.quality, fits: null };
  }
  const r = await P.fitToBytes(encode, o.maxBytes, { max: o.quality });
  return { ...base, blob: r.blob, bytes: r.blob.size, quality: r.quality, fits: r.fits };
}

function num(s: string): number {
  return Number.parseFloat(s.replace(",", "."));
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-9 rounded-md border px-3 text-[13px] whitespace-nowrap transition-colors duration-150",
        active ? "border-fg bg-fg font-medium text-bg" : "border-line text-fg hover:bg-subtle",
      )}
    >
      {children}
    </button>
  );
}

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right tabular-nums">{children}</dd>
    </div>
  );
}

function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block space-y-1", className)}>
      <span className="block text-[13px] font-medium text-muted">{label}</span>
      {children}
    </label>
  );
}

// ---------------------------------------------------------------- 편집 화면

function Editor({
  src,
  fw,
  fh,
  t,
  bg,
  guides,
  onChange,
}: {
  src: Source;
  fw: number;
  fh: number;
  t: P.PhotoTransform;
  bg: string;
  guides: boolean;
  onChange: (fn: (t: P.PhotoTransform) => P.PhotoTransform) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [cw, setCw] = useState(0);
  const scaleRef = useRef(1);
  const pointers = useRef(new Map<number, { x: number; y: number }>());

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setCw(el.clientWidth));
    ro.observe(el);
    setCw(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const margin = cw < 420 ? 20 : 32;
  const ch = cw ? Math.round(Math.min(460, Math.max(260, ((cw - 2 * margin) * fh) / fw + 2 * margin))) : 0;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !cw || !ch) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cw * dpr);
    canvas.height = Math.round(ch * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    const box = P.fitBox(fw, fh, cw - 2 * margin, ch - 2 * margin);
    const scale = box.w / fw;
    scaleRef.current = scale;
    const fx = (cw - box.w) / 2;
    const fy = (ch - box.h) / 2;
    ctx.fillStyle = bg;
    ctx.fillRect(fx, fy, box.w, box.h);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    P.drawPhoto(ctx, src.img, src.width, src.height, fw, fh, t, { cx: cw / 2, cy: ch / 2, scale });
    // 틀 밖은 어둡게
    ctx.fillStyle = "rgba(0,0,0,0.55)";
    ctx.beginPath();
    ctx.rect(0, 0, cw, ch);
    ctx.rect(fx, fy, box.w, box.h);
    ctx.fill("evenodd");
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.lineWidth = 1;
    ctx.strokeRect(fx + 0.5, fy + 0.5, box.w - 1, box.h - 1);
    if (guides) {
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "rgba(20,184,166,0.95)";
      ctx.fillStyle = "rgba(20,184,166,0.95)";
      ctx.font = "11px system-ui, sans-serif";
      ctx.textBaseline = "bottom";
      const line = (y: number, label: string) => {
        ctx.beginPath();
        ctx.moveTo(fx, y);
        ctx.lineTo(fx + box.w, y);
        ctx.stroke();
        ctx.fillText(label, fx + 4, y - 2);
      };
      line(fy + box.h * GUIDE_HEAD_TOP, "머리 위");
      line(fy + box.h * GUIDE_CHIN, "턱");
      ctx.beginPath();
      ctx.moveTo(fx + box.w / 2, fy);
      ctx.lineTo(fx + box.w / 2, fy + box.h);
      ctx.stroke();
      ctx.restore();
    }
  }, [src, fw, fh, t, bg, guides, cw, ch, margin]);

  // 휠·트랙패드 확대 (passive가 아니어야 페이지 스크롤을 막을 수 있다)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const k = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
      onChange((p) => ({ ...p, zoom: p.zoom * k }));
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [onChange]);

  const gesture = () => {
    const pts = [...pointers.current.values()];
    const a = pts[0];
    const b = pts[1];
    if (!a) return null;
    if (!b) return { mx: a.x, my: a.y, dist: 0 };
    return { mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, dist: Math.hypot(a.x - b.x, a.y - b.y) };
  };

  const onPointerDown = (e: RPointerEvent<HTMLCanvasElement>) => {
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* 이미 끝난 포인터 */
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
  };
  const onPointerMove = (e: RPointerEvent<HTMLCanvasElement>) => {
    if (!pointers.current.has(e.pointerId)) return;
    const before = gesture();
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const after = gesture();
    if (!before || !after) return;
    const unit = scaleRef.current * fw; // 화면 px → 틀 너비 단위
    const dx = (after.mx - before.mx) / unit;
    const dy = (after.my - before.my) / unit;
    const zk = before.dist > 0 && after.dist > 0 ? after.dist / before.dist : 1;
    onChange((p) => ({ ...p, offsetX: p.offsetX + dx, offsetY: p.offsetY + dy, zoom: p.zoom * zk }));
  };
  const onPointerEnd = (e: RPointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(e.pointerId);
  };
  const onKeyDown = (e: RKeyboardEvent<HTMLCanvasElement>) => {
    const step = (e.shiftKey ? 10 : 1) / fw;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const m = moves[e.key];
    if (m) {
      e.preventDefault();
      onChange((p) => ({ ...p, offsetX: p.offsetX + m[0], offsetY: p.offsetY + m[1] }));
    } else if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      onChange((p) => ({ ...p, zoom: p.zoom * 1.05 }));
    } else if (e.key === "-") {
      e.preventDefault();
      onChange((p) => ({ ...p, zoom: p.zoom / 1.05 }));
    }
  };

  return (
    <div ref={wrapRef} className="overflow-hidden rounded-lg border border-line bg-subtle">
      <canvas
        ref={canvasRef}
        tabIndex={0}
        role="img"
        aria-label="자르기 화면. 끌어서 옮기고, 휠·두 손가락으로 확대합니다. 화살표 키로 옮기고 +·- 키로 확대합니다."
        data-testid="photo-editor"
        style={{ width: "100%", height: ch || 260, touchAction: "none" }}
        className="block cursor-grab select-none focus-visible:outline-2 focus-visible:outline-accent active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onKeyDown={onKeyDown}
      />
    </div>
  );
}

// ---------------------------------------------------------------- 화면

export function PhotoToolPage() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [src, setSrc] = useState<Source | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragging, setDragging] = useState(false);

  const [mode, setMode] = useState<SizeMode>("preset");
  const [presetId, setPresetId] = useState(P.SIZE_PRESETS[0]!.id);
  const [pxW, setPxW] = useState("354");
  const [pxH, setPxH] = useState("472");
  const [cmW, setCmW] = useState("3");
  const [cmH, setCmH] = useState("4");
  const [dpiText, setDpiText] = useState(String(P.PRESET_DPI));

  const [t, setT] = useState<P.PhotoTransform>(P.IDENTITY_TRANSFORM);
  const [guides, setGuides] = useState(true);

  const [format, setFormat] = useState<P.PhotoFormat>("jpeg");
  const [quality, setQuality] = useState(0.92);
  const [maxKBText, setMaxKBText] = useState("");
  const [bg, setBg] = useState("#ffffff");
  const [keepAlpha, setKeepAlpha] = useState(false);

  const [out, setOut] = useState<Output | null>(null);
  const [outError, setOutError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fitView, setFitView] = useState(false);

  // ---- 크기
  const parsed = useMemo((): { target: Target } | { error: string } => {
    if (mode === "preset") {
      const p = P.SIZE_PRESETS.find((x) => x.id === presetId) ?? P.SIZE_PRESETS[0]!;
      return { target: { w: p.widthPx, h: p.heightPx, dpi: p.dpi } };
    }
    const dpi = num(dpiText);
    if (!(dpi >= 72 && dpi <= 1200)) return { error: "dpi는 72–1200 사이로 넣으세요." };
    const w = mode === "px" ? num(pxW) : P.cmToPx(num(cmW), dpi);
    const h = mode === "px" ? num(pxH) : P.cmToPx(num(cmH), dpi);
    if (!(w >= P.MIN_PX && w <= P.MAX_PX && h >= P.MIN_PX && h <= P.MAX_PX))
      return { error: `가로·세로는 ${P.MIN_PX}–${P.MAX_PX} px 사이여야 합니다.` };
    return { target: { w: Math.round(w), h: Math.round(h), dpi: Math.round(dpi) } };
  }, [mode, presetId, pxW, pxH, cmW, cmH, dpiText]);

  // 잘못 넣은 동안은 마지막으로 맞았던 크기를 쓴다
  const [target, setTarget] = useState<Target>({ w: 354, h: 472, dpi: 300 });
  const parsedKey = "target" in parsed ? `${parsed.target.w}x${parsed.target.h}@${parsed.target.dpi}` : "";
  useEffect(() => {
    if ("target" in parsed) setTarget(parsed.target);
  }, [parsedKey]); // parsed는 parsedKey가 같으면 같은 값

  // ---- 배치 (늘 제한 안으로)
  const dims = useRef({ iw: 1, ih: 1, fw: target.w, fh: target.h });
  dims.current = { iw: src?.width ?? 1, ih: src?.height ?? 1, fw: target.w, fh: target.h };
  const update = useCallback((fn: (p: P.PhotoTransform) => P.PhotoTransform) => {
    setT((prev) => {
      const d = dims.current;
      return P.clampTransform(fn(prev), d.iw, d.ih, d.fw, d.fh);
    });
  }, []);
  useEffect(() => update((p) => p), [update, src, target.w, target.h]);

  // ---- 파일 열기
  const openFile = async (file: File | undefined) => {
    if (!file) return;
    setLoadError(null);
    if (file.size > MAX_FILE_BYTES) {
      setLoadError("파일이 너무 큽니다. 40MB까지 열 수 있습니다.");
      return;
    }
    setLoading(true);
    try {
      const s = await decodeFile(file);
      setSrc(s);
      setT(P.IDENTITY_TRANSFORM);
    } catch {
      setLoadError(decodeErrorText(file));
    } finally {
      setLoading(false);
    }
  };
  // 바뀌거나 화면을 떠나면 전 사진의 메모리(ImageBitmap·object URL)를 놓는다
  useEffect(() => () => src?.close(), [src]);

  // ---- 결과 만들기 (바뀐 뒤 0.15초)
  const maxKB = num(maxKBText);
  const maxBytes = maxKBText.trim() && maxKB > 0 ? P.maxKBToBytes(maxKB) : null;
  useEffect(() => {
    if (!src) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setBusy(true);
      try {
        const r = await renderOutput(src, target, t, { format, quality, maxBytes, bg, keepAlpha });
        if (cancelled) return;
        const url = URL.createObjectURL(r.blob);
        setOut((prev) => {
          if (prev) URL.revokeObjectURL(prev.url);
          return { ...r, url };
        });
        setOutError(null);
      } catch (e) {
        if (cancelled) return;
        setOutError(
          e instanceof UnsupportedFormat
            ? `이 브라우저는 ${P.FORMAT_LABEL[format]}로 저장하지 못합니다. JPG나 PNG를 고르세요.`
            : "사진을 만들지 못했습니다. 크기를 줄이거나 다른 형식을 골라 보세요.",
        );
      } finally {
        if (!cancelled) setBusy(false);
      }
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [src, target, t, format, quality, maxBytes, bg, keepAlpha]);
  const outRef = useRef<Output | null>(null);
  outRef.current = out;
  useEffect(
    () => () => {
      if (outRef.current) URL.revokeObjectURL(outRef.current.url);
    },
    [],
  );

  // "프로필에 저장"의 규격: 결과 크기가 지금 고른 크기와 같을 때만 그 규격 (만드는 중이면 저장을 막는다)
  const spec = P.photoSpecFor(mode, presetId, {
    w: target.w,
    h: target.h,
    cmW: num(cmW),
    cmH: num(cmH),
  }) as PhotoSpec;

  const zoomSlider = Math.round((Math.log(t.zoom) / Math.log(P.MAX_ZOOM)) * 1000);
  const printCm = `${P.pxToCm(target.w, target.dpi).toFixed(2)} × ${P.pxToCm(target.h, target.dpi).toFixed(2)} cm`;

  const fileInput = (
    <input
      ref={fileRef}
      type="file"
      accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif,image/*"
      className="hidden"
      data-testid="photo-file"
      onChange={(e) => void openFile(e.target.files?.[0])}
    />
  );

  const drop = {
    onDragOver: (e: RDragEvent) => {
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: () => setDragging(false),
    onDrop: (e: RDragEvent) => {
      e.preventDefault();
      setDragging(false);
      void openFile(e.dataTransfer.files[0]);
    },
  };

  return (
    <div>
      <PageHeader
        title="사진 변환"
        subtitle="지원서 사진을 양식이 정한 픽셀 크기·파일 형식·용량에 맞춥니다."
        actions={
          src ? (
            <Button size="sm" onClick={() => fileRef.current?.click()}>
              <ImagePlus size={15} /> 다른 사진
            </Button>
          ) : null
        }
      />
      {fileInput}
      <p className="mb-8 flex items-start gap-2 text-[13px] text-muted">
        <Lock size={14} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          사진은 이 브라우저 안에서 변환합니다. 원본은 서버로 보내지 않고, '프로필에 저장'을 누를 때만 변환한 파일을
          저장합니다. 새로 인코딩하므로 원본의 EXIF(촬영 기기·시각·GPS 위치)는 결과 파일에 들어가지 않습니다.
        </span>
      </p>

      {!src ? (
        <div
          {...drop}
          data-testid="photo-drop"
          className={cn(
            "rounded-lg border border-dashed px-4 py-14 text-center transition-colors duration-150",
            dragging ? "border-fg bg-subtle" : "border-line",
          )}
        >
          <ImagePlus size={24} className="mx-auto mb-3 text-muted" aria-hidden />
          <p className="mb-1 text-sm font-medium">사진을 여기로 끌어다 놓거나 고르세요</p>
          <p className="mb-4 text-[13px] text-muted">
            JPG · PNG · WebP. HEIC는 브라우저에 따라 열리지 않을 수 있습니다.
          </p>
          <Button variant="primary" onClick={() => fileRef.current?.click()} disabled={loading}>
            {loading ? "여는 중…" : "사진 고르기"}
          </Button>
          {loadError ? (
            <p role="alert" className="mt-4 text-[13px] text-danger">
              {loadError}
            </p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-10" {...drop}>
          {loadError ? (
            <p role="alert" className="text-[13px] text-danger">
              {loadError}
            </p>
          ) : null}
          <Section title="크기">
            <div className="flex flex-wrap gap-1.5">
              {P.SIZE_PRESETS.map((p) => (
                <Chip
                  key={p.id}
                  active={mode === "preset" && presetId === p.id}
                  onClick={() => {
                    setMode("preset");
                    setPresetId(p.id);
                  }}
                >
                  {p.label}
                </Chip>
              ))}
              <Chip
                active={mode === "px"}
                onClick={() => {
                  if (mode !== "px") {
                    setPxW(String(target.w));
                    setPxH(String(target.h));
                  }
                  setMode("px");
                }}
              >
                직접 입력 (px)
              </Chip>
              <Chip active={mode === "cm"} onClick={() => setMode("cm")}>
                cm · dpi로
              </Chip>
            </div>
            {mode === "px" ? (
              <div className="grid grid-cols-3 gap-2 sm:max-w-md">
                <Field label="가로 (px)">
                  <Input inputMode="numeric" value={pxW} onChange={(e) => setPxW(e.target.value)} />
                </Field>
                <Field label="세로 (px)">
                  <Input inputMode="numeric" value={pxH} onChange={(e) => setPxH(e.target.value)} />
                </Field>
                <Field label="dpi (인쇄 크기)">
                  <Input inputMode="numeric" value={dpiText} onChange={(e) => setDpiText(e.target.value)} />
                </Field>
              </div>
            ) : null}
            {mode === "cm" ? (
              <div className="grid grid-cols-3 gap-2 sm:max-w-md">
                <Field label="가로 (cm)">
                  <Input inputMode="decimal" value={cmW} onChange={(e) => setCmW(e.target.value)} />
                </Field>
                <Field label="세로 (cm)">
                  <Input inputMode="decimal" value={cmH} onChange={(e) => setCmH(e.target.value)} />
                </Field>
                <Field label="dpi">
                  <Input inputMode="numeric" value={dpiText} onChange={(e) => setDpiText(e.target.value)} />
                </Field>
              </div>
            ) : null}
            {"error" in parsed ? (
              <p role="alert" className="text-[13px] text-danger">
                {parsed.error}
              </p>
            ) : null}
            <p className="text-[13px] text-muted">
              출력{" "}
              <b className="font-medium text-fg tabular-nums">
                {target.w} × {target.h} px
              </b>{" "}
              · 인쇄하면 {printCm} ({target.dpi} dpi). cm 크기는 {P.PRESET_DPI} dpi 기준 계산값이며 어느 기관의 공식
              규격도 아닙니다. 양식의 안내를 먼저 확인하세요.
            </p>
          </Section>

          <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,27.5rem)] lg:gap-14">
            <Section title="자르기">
              <Editor src={src} fw={target.w} fh={target.h} t={t} bg={bg} guides={guides} onChange={update} />
              <p className="text-xs text-muted">
                끌어서 옮기고, 휠·두 손가락·막대로 확대합니다. 틀은 출력 비율({target.w}:{target.h})을 따릅니다.
              </p>
              <div className="space-y-3">
                <Field label={`확대 ${Math.round(t.zoom * 100)}%`}>
                  <input
                    type="range"
                    min={0}
                    max={1000}
                    value={zoomSlider}
                    aria-label="확대"
                    className="w-full accent-accent"
                    onChange={(e) => {
                      const z = Math.pow(P.MAX_ZOOM, Number(e.target.value) / 1000);
                      update((p) => ({ ...p, zoom: z }));
                    }}
                  />
                </Field>
                <Field label={`기울기 보정 ${t.straighten > 0 ? "+" : ""}${t.straighten.toFixed(1)}°`}>
                  <input
                    type="range"
                    min={-P.MAX_STRAIGHTEN}
                    max={P.MAX_STRAIGHTEN}
                    step={0.1}
                    value={t.straighten}
                    aria-label="기울기 보정"
                    className="w-full accent-accent"
                    onChange={(e) => update((p) => ({ ...p, straighten: Number(e.target.value) }))}
                  />
                </Field>
                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm" onClick={() => update((p) => ({ ...p, quarter: p.quarter - 1 }))}>
                    <RotateCcw size={15} /> 왼쪽 90°
                  </Button>
                  <Button size="sm" onClick={() => update((p) => ({ ...p, quarter: p.quarter + 1 }))}>
                    <RotateCw size={15} /> 오른쪽 90°
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setT(P.IDENTITY_TRANSFORM)}>
                    <Undo2 size={15} /> 처음대로
                  </Button>
                  <label className="ml-auto flex items-center gap-1.5 text-[13px]">
                    <input type="checkbox" checked={guides} onChange={(e) => setGuides(e.target.checked)} />
                    얼굴 안내선
                  </label>
                </div>
                {guides ? (
                  <p className="text-xs text-muted">
                    안내선(머리 위 {GUIDE_HEAD_TOP * 100}% · 턱 {GUIDE_CHIN * 100}%)은 맞추기 쉽게 그린 참고선입니다.
                    양식이 얼굴 비율을 정했다면 그 안내를 따르세요.
                  </p>
                ) : null}
              </div>
            </Section>

            <div className="space-y-10">
              <Section
                title={fitView ? "결과 (화면에 맞춤)" : "결과 (실제 크기 1:1)"}
                action={
                  <div className="flex items-center gap-2">
                    {busy ? <span className="text-xs text-muted">만드는 중…</span> : null}
                    <Button size="sm" variant="ghost" onClick={() => setFitView((v) => !v)}>
                      {fitView ? "1:1로 보기" : "화면에 맞추기"}
                    </Button>
                  </div>
                }
              >
                <div
                  className="overflow-auto rounded-lg border border-line p-3"
                  style={{
                    backgroundImage:
                      "linear-gradient(45deg, var(--app-subtle) 25%, transparent 25%, transparent 75%, var(--app-subtle) 75%), linear-gradient(45deg, var(--app-subtle) 25%, transparent 25%, transparent 75%, var(--app-subtle) 75%)",
                    backgroundSize: "16px 16px",
                    backgroundPosition: "0 0, 8px 8px",
                  }}
                >
                  {out ? (
                    <img
                      src={out.url}
                      alt="변환한 사진 미리보기"
                      data-testid="photo-output"
                      width={out.w}
                      height={out.h}
                      style={
                        fitView
                          ? { width: "auto", height: "auto", maxWidth: "100%", maxHeight: 420 }
                          : { width: out.w, height: out.h, maxWidth: "none" }
                      }
                      className="mx-auto block shadow-[0_0_0_1px_var(--app-line)]"
                    />
                  ) : (
                    <div className="py-10 text-center text-[13px] text-muted">만드는 중…</div>
                  )}
                </div>
                {outError ? (
                  <p role="alert" className="text-[13px] text-danger">
                    {outError}
                  </p>
                ) : null}
                {out ? (
                  <div className="space-y-2" data-testid="photo-info">
                    <dl className="divide-y divide-line border-y border-line text-[13px]">
                      <InfoRow label="크기">
                        {out.w} × {out.h} px
                      </InfoRow>
                      <InfoRow label="형식">
                        {P.FORMAT_LABEL[out.format]}
                        {out.quality !== null ? ` · 품질 ${Math.round(out.quality * 100)}%` : ""}
                      </InfoRow>
                      <InfoRow label="용량">
                        {P.formatKB(out.bytes)} ({out.bytes.toLocaleString("ko-KR")}바이트)
                        {out.fits === true ? <span className="font-medium"> · {maxKBText.trim()}KB 이하</span> : null}
                      </InfoRow>
                      <InfoRow label="인쇄 크기">
                        {P.pxToCm(out.w, out.dpi).toFixed(2)} × {P.pxToCm(out.h, out.dpi).toFixed(2)} cm ({out.dpi} dpi
                        {out.format === "webp" ? ", 파일에는 적지 않음" : ", 파일에 기록"})
                      </InfoRow>
                    </dl>
                    {out.fits === false ? (
                      <p role="alert" className="text-[13px] text-danger">
                        {out.format === "png"
                          ? `PNG는 품질을 낮출 수 없어 ${maxKBText.trim()}KB 이하로 줄이지 못합니다. 용량 제한이 있으면 JPG나 WebP를 고르세요.`
                          : `가장 낮은 품질(${Math.round((out.quality ?? 0) * 100)}%)로도 ${P.formatKB(out.bytes)}라 ${maxKBText.trim()}KB 이하로 줄이지 못합니다. 픽셀 크기를 줄여 보세요.`}
                      </p>
                    ) : null}
                    <SaveToProfile
                      key={out.url}
                      blob={out.blob}
                      filename={P.photoFilename(out.w, out.h, out.format)}
                      spec={spec}
                      disabled={busy || !!outError}
                      download={
                        <a
                          href={outError ? undefined : out.url}
                          download={P.photoFilename(out.w, out.h, out.format)}
                          aria-disabled={!!outError}
                          data-testid="photo-download"
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-md bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent/90",
                            outError && "pointer-events-none opacity-50",
                          )}
                        >
                          <Download size={16} /> 내려받기 ({P.photoFilename(out.w, out.h, out.format)})
                        </a>
                      }
                    />
                  </div>
                ) : null}
              </Section>

              <Section title="저장 형식">
                <div className="flex flex-wrap gap-1.5">
                  {(["jpeg", "png", "webp"] as const).map((f) => (
                    <Chip key={f} active={format === f} onClick={() => setFormat(f)}>
                      {P.FORMAT_LABEL[f]}
                    </Chip>
                  ))}
                </div>
                {P.hasQuality(format) ? (
                  <Field
                    label={`품질 ${Math.round(quality * 100)}%${maxBytes !== null ? " (용량 제한이 있으면 이 값 이하에서 찾음)" : ""}`}
                  >
                    <input
                      type="range"
                      min={10}
                      max={100}
                      value={Math.round(quality * 100)}
                      aria-label="품질"
                      className="w-full accent-accent"
                      onChange={(e) => setQuality(Number(e.target.value) / 100)}
                    />
                  </Field>
                ) : (
                  <p className="text-[13px] text-muted">PNG는 손실 없는 형식이라 품질 설정이 없고 파일이 큽니다.</p>
                )}
                <div className="flex flex-wrap items-end gap-3">
                  <Field label="최대 용량 (KB, 선택)" className="w-40">
                    <Input
                      inputMode="numeric"
                      placeholder="예: 100"
                      value={maxKBText}
                      onChange={(e) => setMaxKBText(e.target.value)}
                    />
                  </Field>
                  <div className="space-y-1">
                    <Label>배경색</Label>
                    <div className="flex items-center gap-1.5">
                      {BACKGROUNDS.map(([c, name]) => (
                        <button
                          key={c}
                          type="button"
                          title={name}
                          aria-label={`배경 ${name}`}
                          aria-pressed={bg === c}
                          onClick={() => setBg(c)}
                          className={cn(
                            "h-9 w-9 rounded-md border",
                            bg === c ? "border-fg ring-2 ring-fg/30" : "border-line",
                          )}
                          style={{ background: c }}
                        />
                      ))}
                      <input
                        type="color"
                        value={bg}
                        aria-label="배경색 직접 고르기"
                        onChange={(e) => setBg(e.target.value)}
                        className="h-9 w-11 cursor-pointer rounded-md border border-line bg-surface p-0.5"
                      />
                    </div>
                  </div>
                </div>
                {maxBytes !== null ? (
                  <p className="text-xs text-muted">
                    양식마다 1KB를 1,000바이트 또는 1,024바이트로 세므로 더 작은 {maxBytes.toLocaleString("ko-KR")}
                    바이트 이하로 맞춥니다.
                  </p>
                ) : null}
                {format !== "jpeg" ? (
                  <label className="flex items-center gap-1.5 text-[13px]">
                    <input type="checkbox" checked={keepAlpha} onChange={(e) => setKeepAlpha(e.target.checked)} />
                    투명한 부분은 투명하게 두기 (끄면 배경색으로 채움)
                  </label>
                ) : (
                  <p className="text-xs text-muted">JPG는 투명을 담지 못해 투명한 부분을 배경색으로 채웁니다.</p>
                )}
                {format === "webp" ? (
                  <p className="text-xs text-muted">
                    WebP는 지원서 양식에서 받지 않는 경우가 많습니다. 양식이 허용하는 형식을 확인하세요.
                  </p>
                ) : null}
              </Section>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
