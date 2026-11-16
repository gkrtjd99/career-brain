"""스캔 PDF의 쪽을 이미지(PNG)로 바꾼다 — 선택 설치 ``career-core[pdf-render]`` (pypdfium2).

- pypdfium2(BSD-3/Apache-2.0)는 PDFium 바이너리를 품은 휠 하나다 (~4MB, 풀면 ~8MB). Pillow 같은
  이미지 라이브러리는 쓰지 않는다: 회색조 비트맵을 표준 라이브러리(zlib)로 PNG로 쓴다.
- 상한: 앞쪽 ``max_pages``쪽만, 쪽마다 긴 변 ``MAX_EDGE_PIXELS``·전체 ``MAX_PAGE_PIXELS`` 이하로
  줄여 그린다 (비전 모델도 이보다 크면 줄여 읽는다). PNG 하나가 ``max_image_bytes``를 넘으면 거기서 멈춘다.
- PDFium은 스레드 안전하지 않다. 이벤트 루프를 막지 않게 ``asyncio.to_thread``로 부르되 프로세스 안에서
  한 번에 하나만 그리도록 잠금을 둔다.
- 신뢰할 수 없는 파일이다. 폼·주석 그리기는 끄고 글꼴·이미지 캐시는 제한한다. 실패는 ``PdfRenderError``.
"""

from __future__ import annotations

import math
import struct
import threading
import zlib
from dataclasses import dataclass

RENDER_DPI = 150
MAX_RENDER_PAGES = 4
MAX_EDGE_PIXELS = 1600
MAX_PAGE_PIXELS = 1600 * 1600
_LOCK = threading.Lock()


class PdfRenderUnavailable(Exception):
    """렌더러(pypdfium2)가 설치되지 않음."""


class PdfRenderError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RenderedPages:
    images: tuple[bytes, ...]  # PNG, 쪽 순서
    pages_total: int


def renderer_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def png_gray(width: int, height: int, stride: int, pixels: bytes) -> bytes:
    """8비트 회색조 비트맵 → PNG (필터 없음 + zlib)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride : y * stride + width]
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _scale(width_pt: float, height_pt: float) -> float:
    if width_pt <= 0 or height_pt <= 0:
        raise PdfRenderError("bad_page_size")
    scale = RENDER_DPI / 72
    scale = min(scale, MAX_EDGE_PIXELS / max(width_pt, height_pt))
    return min(scale, math.sqrt(MAX_PAGE_PIXELS / (width_pt * height_pt)))


def render_pages(
    data: bytes, *, max_pages: int = MAX_RENDER_PAGES, max_image_bytes: int = 5 * 1024 * 1024
) -> RenderedPages:
    """앞쪽 ``max_pages``쪽을 회색조 PNG로. 동기 CPU 작업이다: ``asyncio.to_thread``로 부른다."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PdfRenderUnavailable from exc
    images: list[bytes] = []
    with _LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("malformed") from exc
        try:
            total = len(pdf)
            for index in range(min(total, max_pages)):
                page = pdf[index]
                try:
                    w, h = page.get_size()
                    bitmap = page.render(
                        scale=_scale(w, h),
                        grayscale=True,
                        may_draw_forms=False,
                        draw_annots=False,
                        limit_image_cache=True,
                    )
                    try:
                        if bitmap.n_channels != 1:
                            raise PdfRenderError("unexpected_format")
                        png = png_gray(
                            bitmap.width, bitmap.height, bitmap.stride, bytes(bitmap.buffer)
                        )
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                if len(png) > max_image_bytes:
                    break
                images.append(png)
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("render_failed") from exc
        finally:
            pdf.close()
    return RenderedPages(images=tuple(images), pages_total=total)
