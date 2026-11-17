/**
 * 지원서 사진 변환(`/tools/photo`)의 순수 로직. 화면(`pages/PhotoTool.tsx`)과 나눠 둔다.
 *
 * - 크기 계산: cm ↔ px (dpi 기준), 미리 정한 크기(300 dpi).
 * - 자르기 기하: 틀(출력 크기)을 늘 채우는 최소 배율, 틀이 사진 밖으로 나가지 않게 위치 제한.
 *   회전(90° 단위 + 기울기 보정)이 있어도 정확하다 (회전한 틀의 네 귀퉁이가 사진 안에 있을 조건).
 * - 용량 맞추기: 품질 이분 탐색 (인코더는 주입).
 * - 파일 머리 고치기: JPEG JFIF APP0 밀도(dpi), PNG pHYs. 다시 인코딩한 파일이라 EXIF·GPS는 없다.
 *
 * DOM 객체를 만들지 않는다 (`drawPhoto`는 받은 2D context에 그리기만).
 * Node에서 타입만 지우고 바로 돌릴 수 있게 enum·parameter property 같은 문법을 쓰지 않는다.
 */

export const CM_PER_INCH = 2.54;
export const PRESET_DPI = 300;
export const MIN_PX = 16;
export const MAX_PX = 4000;
export const MAX_ZOOM = 8;
export const MAX_STRAIGHTEN = 10;

export function cmToPx(cm: number, dpi: number): number {
  return Math.round((cm / CM_PER_INCH) * dpi);
}

export function pxToCm(px: number, dpi: number): number {
  return (px / dpi) * CM_PER_INCH;
}

export function clampPx(px: number): number {
  if (!Number.isFinite(px)) return MIN_PX;
  return Math.min(MAX_PX, Math.max(MIN_PX, Math.round(px)));
}

export interface SizePreset {
  id: string;
  label: string;
  widthPx: number;
  heightPx: number;
  dpi: number;
}

function cmPreset(w: number, h: number): SizePreset {
  const widthPx = cmToPx(w, PRESET_DPI);
  const heightPx = cmToPx(h, PRESET_DPI);
  return { id: `cm-${w}x${h}`, label: `${w} × ${h} cm · ${widthPx} × ${heightPx} px`, widthPx, heightPx, dpi: PRESET_DPI };
}

/** 흔히 보는 크기. cm는 300 dpi로 계산한 값이고 어느 기관의 공식 규격도 아니다. */
export const SIZE_PRESETS: SizePreset[] = [
  cmPreset(3, 4),
  cmPreset(3.5, 4.5),
  cmPreset(2.5, 3),
  { id: "px-150x200", label: "150 × 200 px", widthPx: 150, heightPx: 200, dpi: PRESET_DPI },
];

/**
 * "프로필에 저장"의 규격 (서버 `domain.profiles.PhotoSpec`): 미리 정한 크기면 그 id,
 * cm로 넣은 크기가 미리 정한 cm와 같으면 그 id (dpi는 자유), px로 150×200이면 그 id, 나머지는 custom.
 */
export function photoSpecFor(
  mode: "preset" | "px" | "cm",
  presetId: string,
  size: { w: number; h: number; cmW?: number; cmH?: number },
): string {
  if (mode === "preset" && SIZE_PRESETS.some((p) => p.id === presetId)) return presetId;
  if (mode === "cm" && size.cmW !== undefined && size.cmH !== undefined) {
    const id = `cm-${size.cmW}x${size.cmH}`;
    if (SIZE_PRESETS.some((p) => p.id === id)) return id;
  }
  if (mode === "px" && size.w === 150 && size.h === 200) return "px-150x200";
  return "custom";
}

// ---------------------------------------------------------------- 형식

export type PhotoFormat = "jpeg" | "png" | "webp";

export const FORMAT_MIME: Record<PhotoFormat, string> = {
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
};
export const FORMAT_EXT: Record<PhotoFormat, string> = { jpeg: "jpg", png: "png", webp: "webp" };
export const FORMAT_LABEL: Record<PhotoFormat, string> = { jpeg: "JPG", png: "PNG", webp: "WebP" };

export function hasQuality(format: PhotoFormat): boolean {
  return format !== "png";
}

export function photoFilename(widthPx: number, heightPx: number, format: PhotoFormat): string {
  return `photo_${widthPx}x${heightPx}.${FORMAT_EXT[format]}`;
}

/** 표시용 KB (1KB = 1024바이트, 파일 탐색기와 같은 셈) */
export function formatKB(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

/**
 * "최대 N KB" 제한을 바이트로. 양식이 1KB를 1000바이트로 셀지 1024바이트로 셀지 모르므로
 * 더 작은 1000바이트로 맞춘다 (어느 쪽으로 세도 통과).
 */
export function maxKBToBytes(kb: number): number {
  return Math.floor(kb * 1000);
}

// ---------------------------------------------------------------- 자르기 기하

/**
 * 틀에 대한 사진의 배치.
 * - quarter: 90° 회전 횟수(시계 방향), straighten: 기울기 보정(°, ±10)
 * - zoom: 틀을 딱 채우는 배율에 곱하는 값 (1 = 빈틈없이 채우는 가장 작은 크기)
 * - offsetX/offsetY: 틀 가운데에서 사진 가운데까지 거리, **틀 너비** 단위 (출력 px = offset × 틀 너비).
 *   틀 너비 단위라 같은 비율에서 출력 px를 바꿔도 구도가 그대로다.
 */
export interface PhotoTransform {
  quarter: number;
  straighten: number;
  zoom: number;
  offsetX: number;
  offsetY: number;
}

export const IDENTITY_TRANSFORM: PhotoTransform = { quarter: 0, straighten: 0, zoom: 1, offsetX: 0, offsetY: 0 };

export function rotationRad(t: Pick<PhotoTransform, "quarter" | "straighten">): number {
  return (((((t.quarter % 4) + 4) % 4) * 90 + t.straighten) * Math.PI) / 180;
}

/** 사진 축으로 본 틀(fw×fh, theta 회전)의 외접 사각형 */
function frameBox(fw: number, fh: number, theta: number): { bw: number; bh: number } {
  const c = Math.abs(Math.cos(theta));
  const s = Math.abs(Math.sin(theta));
  return { bw: fw * c + fh * s, bh: fw * s + fh * c };
}

/** 틀을 빈틈없이 덮는 가장 작은 배율 (사진 px → 출력 px) */
export function coverScale(iw: number, ih: number, fw: number, fh: number, theta: number): number {
  const { bw, bh } = frameBox(fw, fh, theta);
  return Math.max(bw / iw, bh / ih);
}

/**
 * 배율을 [1, MAX_ZOOM]으로, 위치를 틀이 사진 안에 남는 범위로 자른다.
 * 회전한 틀의 네 귀퉁이가 사진 안에 있다 ⇔ 사진 축으로 본 틀의 외접 사각형이 사진 안에 있다.
 */
export function clampTransform(
  t: PhotoTransform,
  iw: number,
  ih: number,
  fw: number,
  fh: number,
): PhotoTransform {
  const quarter = ((Math.round(t.quarter) % 4) + 4) % 4;
  const straighten = Math.max(-MAX_STRAIGHTEN, Math.min(MAX_STRAIGHTEN, t.straighten || 0));
  const zoom = Math.max(1, Math.min(MAX_ZOOM, Number.isFinite(t.zoom) ? t.zoom : 1));
  const theta = rotationRad({ quarter, straighten });
  const k = coverScale(iw, ih, fw, fh, theta) * zoom;
  const { bw, bh } = frameBox(fw, fh, theta);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  // 사진 가운데 → 틀 가운데 (출력 px, 화면 축)
  const cx = -(t.offsetX || 0) * fw;
  const cy = -(t.offsetY || 0) * fw;
  // 사진 축으로 (−theta 회전)
  let u = cx * cos + cy * sin;
  let v = -cx * sin + cy * cos;
  const mu = Math.max(0, (k * iw) / 2 - bw / 2);
  const mv = Math.max(0, (k * ih) / 2 - bh / 2);
  u = Math.max(-mu, Math.min(mu, u));
  v = Math.max(-mv, Math.min(mv, v));
  const ncx = u * cos - v * sin;
  const ncy = u * sin + v * cos;
  return { quarter, straighten, zoom, offsetX: -ncx / fw || 0, offsetY: -ncy / fw || 0 };
}

/** 틀의 비율 그대로 (maxW × maxH) 안에 들어가는 가장 큰 크기 */
export function fitBox(fw: number, fh: number, maxW: number, maxH: number): { w: number; h: number } {
  const s = Math.min(maxW / fw, maxH / fh);
  return { w: fw * s, h: fh * s };
}

/** 2D context의 최소 모양 (Node 확인 스크립트에서도 가짜로 넣을 수 있게) */
export interface Ctx2D {
  save(): void;
  restore(): void;
  translate(x: number, y: number): void;
  rotate(a: number): void;
  scale(x: number, y: number): void;
  drawImage(img: never, dx: number, dy: number): void;
}

/**
 * 사진을 그린다. 틀 가운데가 (cx, cy), 출력 1px이 context에서 `scale` px.
 * 편집 화면(scale = 화면 틀 너비 / 출력 너비)과 출력 파일(scale 1, 가운데 = 출력 가운데)이 같은 함수를 쓴다.
 */
export function drawPhoto(
  ctx: Ctx2D,
  img: unknown,
  iw: number,
  ih: number,
  fw: number,
  fh: number,
  t: PhotoTransform,
  view: { cx: number; cy: number; scale: number },
): void {
  const theta = rotationRad(t);
  const k = coverScale(iw, ih, fw, fh, theta) * t.zoom;
  ctx.save();
  ctx.translate(view.cx + t.offsetX * fw * view.scale, view.cy + t.offsetY * fw * view.scale);
  ctx.rotate(theta);
  ctx.scale(k * view.scale, k * view.scale);
  ctx.drawImage(img as never, -iw / 2, -ih / 2);
  ctx.restore();
}

// ---------------------------------------------------------------- 용량 맞추기

export interface FitResult<B> {
  blob: B;
  quality: number;
  fits: boolean;
}

/**
 * maxBytes 이하가 되는 가장 높은 품질을 이분 탐색으로 찾는다 (상한 = 사용자가 고른 품질).
 * 가장 낮은 품질에서도 넘으면 그 결과와 fits=false. 인코더가 완전히 단조롭지 않아도
 * 돌려주는 결과는 실제로 잰 크기가 제한 이하인 것뿐이다.
 */
export async function fitToBytes<B extends { size: number }>(
  encode: (quality: number) => Promise<B>,
  maxBytes: number,
  opts: { min?: number; max?: number; iterations?: number } = {},
): Promise<FitResult<B>> {
  const min = opts.min ?? 0.05;
  const max = Math.max(min, opts.max ?? 0.92);
  const iterations = opts.iterations ?? 7;
  const top = await encode(max);
  if (top.size <= maxBytes) return { blob: top, quality: max, fits: true };
  const bottom = await encode(min);
  if (bottom.size > maxBytes) return { blob: bottom, quality: min, fits: false };
  let lo = min;
  let hi = max;
  let best: FitResult<B> = { blob: bottom, quality: min, fits: true };
  for (let i = 0; i < iterations; i++) {
    const mid = Math.round(((lo + hi) / 2) * 1000) / 1000;
    if (mid <= lo || mid >= hi) break;
    const b = await encode(mid);
    if (b.size <= maxBytes) {
      lo = mid;
      best = { blob: b, quality: mid, fits: true };
    } else {
      hi = mid;
    }
  }
  return best;
}

// ---------------------------------------------------------------- 파일 머리: JPEG JFIF 밀도

function clampDpi(dpi: number): number {
  return Math.max(1, Math.min(65535, Math.round(dpi)));
}

function isJfifApp0(b: Uint8Array): boolean {
  return (
    b[2] === 0xff &&
    b[3] === 0xe0 &&
    b[6] === 0x4a && // J
    b[7] === 0x46 && // F
    b[8] === 0x49 && // I
    b[9] === 0x46 && // F
    b[10] === 0x00
  );
}

/**
 * JPEG의 JFIF APP0 밀도를 dpi(단위 1 = 인치당 점)로 쓴다. SOI 바로 뒤에 APP0가 없으면 새로 넣는다 (18바이트).
 * 인쇄·문서 프로그램이 이 값으로 cm 크기를 정한다.
 */
export function setJpegDpi(bytes: Uint8Array, dpi: number): Uint8Array<ArrayBuffer> {
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error("not_jpeg");
  const d = clampDpi(dpi);
  if (isJfifApp0(bytes)) {
    const out = bytes.slice();
    out[13] = 1;
    out[14] = d >> 8;
    out[15] = d & 0xff;
    out[16] = d >> 8;
    out[17] = d & 0xff;
    return out;
  }
  // FFE0, 길이 16, "JFIF\0", 버전 1.01, 단위 1, X·Y 밀도, 썸네일 0×0
  const seg = [0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x01, d >> 8, d & 0xff, d >> 8, d & 0xff, 0, 0];
  const out = new Uint8Array(bytes.length + seg.length);
  out.set(bytes.subarray(0, 2), 0);
  out.set(seg, 2);
  out.set(bytes.subarray(2), 2 + seg.length);
  return out;
}

/** JFIF 밀도 읽기 (확인용). 없으면 null */
export function readJpegDpi(bytes: Uint8Array): { unit: number; x: number; y: number } | null {
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8 || !isJfifApp0(bytes)) return null;
  return {
    unit: bytes[13] ?? 0,
    x: ((bytes[14] ?? 0) << 8) | (bytes[15] ?? 0),
    y: ((bytes[16] ?? 0) << 8) | (bytes[17] ?? 0),
  };
}

// ---------------------------------------------------------------- 파일 머리: PNG pHYs

const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

let crcTable: Uint32Array | null = null;
export function crc32(data: Uint8Array, start = 0, end = data.length): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = start; i < end; i++) crc = (crcTable[(crc ^ (data[i] ?? 0)) & 0xff] ?? 0) ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function u32(b: Uint8Array, i: number): number {
  return (((b[i] ?? 0) << 24) | ((b[i + 1] ?? 0) << 16) | ((b[i + 2] ?? 0) << 8) | (b[i + 3] ?? 0)) >>> 0;
}

function chunkType(b: Uint8Array, i: number): string {
  return String.fromCharCode(b[i + 4] ?? 0, b[i + 5] ?? 0, b[i + 6] ?? 0, b[i + 7] ?? 0);
}

/** PNG 덩어리 목록 [시작, 끝) (길이·종류·데이터·CRC 포함) */
function pngChunks(b: Uint8Array): { type: string; start: number; end: number }[] {
  if (PNG_SIG.some((v, i) => b[i] !== v)) throw new Error("not_png");
  const out: { type: string; start: number; end: number }[] = [];
  let i = 8;
  while (i + 12 <= b.length) {
    const len = u32(b, i);
    const end = i + 12 + len;
    if (end > b.length) throw new Error("bad_png");
    const type = chunkType(b, i);
    out.push({ type, start: i, end });
    i = end;
    if (type === "IEND") break;
  }
  return out;
}

/** dpi → 미터당 점 (pHYs 단위 1) */
export function dpiToPpm(dpi: number): number {
  return Math.round(dpi / 0.0254);
}

/** PNG에 pHYs(미터당 점)를 IHDR 바로 뒤에 넣는다. 있던 pHYs는 뺀다. */
export function setPngDpi(bytes: Uint8Array, dpi: number): Uint8Array<ArrayBuffer> {
  const chunks = pngChunks(bytes);
  if (chunks[0]?.type !== "IHDR") throw new Error("bad_png");
  const ppm = dpiToPpm(clampDpi(dpi));
  const phys = new Uint8Array(21);
  const dv = new DataView(phys.buffer);
  dv.setUint32(0, 9);
  phys.set([0x70, 0x48, 0x59, 0x73], 4); // pHYs
  dv.setUint32(8, ppm);
  dv.setUint32(12, ppm);
  phys[16] = 1;
  dv.setUint32(17, crc32(phys, 4, 17));
  const kept = chunks.filter((c) => c.type !== "pHYs");
  const size = 8 + phys.length + kept.reduce((n, c) => n + (c.end - c.start), 0);
  const out = new Uint8Array(size);
  out.set(bytes.subarray(0, 8), 0);
  let p = 8;
  kept.forEach((c, idx) => {
    out.set(bytes.subarray(c.start, c.end), p);
    p += c.end - c.start;
    if (idx === 0) {
      out.set(phys, p);
      p += phys.length;
    }
  });
  return out;
}

/** pHYs 읽기 (확인용). 없으면 null */
export function readPngDpi(bytes: Uint8Array): { unit: number; x: number; y: number; dpi: number; crcOk: boolean } | null {
  const c = pngChunks(bytes).find((ch) => ch.type === "pHYs");
  if (!c) return null;
  const x = u32(bytes, c.start + 8);
  const y = u32(bytes, c.start + 12);
  const unit = bytes[c.start + 16] ?? 0;
  const crcOk = crc32(bytes, c.start + 4, c.end - 4) === u32(bytes, c.end - 4);
  return { unit, x, y, dpi: unit === 1 ? x * 0.0254 : 0, crcOk };
}

/** 확인용: JPEG SOFn·PNG IHDR에서 가로·세로 px */
export function readImageSize(bytes: Uint8Array): { width: number; height: number } | null {
  if (PNG_SIG.every((v, i) => bytes[i] === v)) return { width: u32(bytes, 16), height: u32(bytes, 20) };
  if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    let i = 2;
    while (i + 9 < bytes.length) {
      if (bytes[i] !== 0xff) return null;
      const marker = bytes[i + 1] ?? 0;
      const len = ((bytes[i + 2] ?? 0) << 8) | (bytes[i + 3] ?? 0);
      if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
        return {
          height: ((bytes[i + 5] ?? 0) << 8) | (bytes[i + 6] ?? 0),
          width: ((bytes[i + 7] ?? 0) << 8) | (bytes[i + 8] ?? 0),
        };
      }
      i += 2 + len;
    }
  }
  return null;
}
