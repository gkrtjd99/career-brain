/**
 * 브라우저에서 이미지 열기·줄이기 (지원서 사진 변환과 포트폴리오 이미지가 같이 쓴다).
 *
 * - 열기: EXIF 방향을 따라 연다. createImageBitmap이 못 열면 <img>로 한 번 더 (Safari는 HEIC를 이쪽으로 연다).
 * - 줄이기: 긴 변을 상한에 맞추고 canvas에 다시 그려 인코딩한다. 다시 인코딩한 파일이라 EXIF·GPS·카메라 정보가 없다.
 *   PNG로 상한 안에 들면 PNG(글씨가 많은 화면에 깨끗하다), 아니면 JPEG 품질을 낮춰 용량 상한에 맞춘다
 *   (`lib/photo.fitToBytes`). 그래도 넘으면 크기를 조금씩 줄인다.
 * - 서버가 형식·용량·크기를 다시 보고 메타데이터를 한 번 더 지운다. 여기서 못 줄이면 서버도 받지 않는다.
 */
import { fitBox, fitToBytes } from "./photo";

export interface DecodedImage {
  img: CanvasImageSource;
  width: number;
  height: number;
  name: string;
  close: () => void;
}

export function isHeic(file: File) {
  return /hei[cf]/i.test(file.type) || /\.(heic|heif)$/i.test(file.name);
}

export async function decodeFile(file: File): Promise<DecodedImage> {
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

export function decodeErrorText(file: File): string {
  if (isHeic(file))
    return (
      "이 브라우저는 HEIC/HEIF 사진을 열지 못합니다. 아이폰이면 사진 앱에서 JPG로 내보내거나 " +
      "설정 → 카메라 → 포맷을 '높은 호환성'으로 바꿔 찍은 뒤 다시 올려 주세요. Safari에서는 열릴 수 있습니다."
    );
  return "이 파일을 사진으로 읽지 못했습니다. JPG·PNG·WebP 파일인지 확인하세요.";
}

export interface ShrinkResult {
  blob: Blob;
  width: number;
  height: number;
  mime: "image/png" | "image/jpeg";
  /** 원본 크기 */
  from: { width: number; height: number; bytes: number };
}

export class ShrinkFailed extends Error {}

async function encode(canvas: HTMLCanvasElement, mime: string, q?: number): Promise<Blob> {
  const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, mime, q));
  if (!blob || blob.type !== mime) throw new ShrinkFailed("encode");
  return blob;
}

/** 긴 변 ``maxEdge`` px·``maxBytes`` 이하로 다시 인코딩한다. 열지 못하면 decodeFile의 오류를 그대로 던진다. */
export async function shrinkImage(
  file: File,
  opts: { maxEdge: number; maxBytes: number; minEdge?: number },
): Promise<ShrinkResult> {
  const src = await decodeFile(file);
  try {
    const minEdge = opts.minEdge ?? 16;
    if (Math.min(src.width, src.height) < minEdge) throw new ShrinkFailed("too_small");
    let edge = Math.min(opts.maxEdge, Math.max(src.width, src.height));
    for (let round = 0; round < 6; round++) {
      const box = fitBox(src.width, src.height, edge, edge);
      const w = Math.max(minEdge, Math.round(box.w));
      const h = Math.max(minEdge, Math.round(box.h));
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new ShrinkFailed("canvas");
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(src.img, 0, 0, w, h);
      const from = { width: src.width, height: src.height, bytes: file.size };
      const png = await encode(canvas, "image/png");
      if (png.size <= opts.maxBytes) return { blob: png, width: w, height: h, mime: "image/png", from };
      // JPEG는 투명을 흰 바탕으로
      const flat = document.createElement("canvas");
      flat.width = w;
      flat.height = h;
      const fctx = flat.getContext("2d");
      if (!fctx) throw new ShrinkFailed("canvas");
      fctx.fillStyle = "#ffffff";
      fctx.fillRect(0, 0, w, h);
      fctx.drawImage(canvas, 0, 0);
      const r = await fitToBytes((q) => encode(flat, "image/jpeg", q), opts.maxBytes, { min: 0.4, max: 0.9 });
      if (r.fits) return { blob: r.blob, width: w, height: h, mime: "image/jpeg", from };
      edge = Math.round(edge * 0.8);
    }
    throw new ShrinkFailed("too_large");
  } finally {
    src.close();
  }
}
