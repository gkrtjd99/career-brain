"""스캔 PDF의 쪽을 이미지(PNG)로 바꾼다 — 선택 설치 ``career-core[pdf-render]`` (pypdfium2).

- pypdfium2(BSD-3/Apache-2.0)는 PDFium 바이너리를 품은 휠 하나다 (~4MB, 풀면 ~8MB). Pillow 같은
  이미지 라이브러리는 쓰지 않는다: 회색조 비트맵을 표준 라이브러리(zlib)로 PNG로 쓴다.
- 상한: 앞쪽 ``max_pages``쪽만, 쪽마다 긴 변 ``MAX_EDGE_PIXELS``·전체 ``MAX_PAGE_PIXELS`` 이하로
  줄여 그린다 (비전 모델도 이보다 크면 줄여 읽는다). PNG 하나가 ``max_image_bytes``를 넘으면 거기서 멈춘다.
- PDFium은 스레드 안전하지 않다. 이벤트 루프를 막지 않게 ``asyncio.to_thread``로 부르되 프로세스 안에서
  한 번에 하나만 그리도록 잠금을 둔다.
- 신뢰할 수 없는 파일이다. 폼·주석 그리기는 끄고 글꼴·이미지 캐시는 제한한다. 실패는 ``PdfRenderError``.
- 쪽 고르기 (c0025, ``pick_pages``): 긴 스캔 공고는 앞쪽만 읽으면 마감·지원 방법(보통 끝쪽)을 놓친다.
  쪽마다 작은 회색조 미리보기(긴 변 ``THUMB_EDGE``px)의 잉크 비율(어두운 픽셀 비율)을 재서 빈 쪽을 빼고,
  남은 쪽이 ``max_pages``보다 많으면 **앞쪽 ``max_pages - 1``쪽 + 마지막 쪽**을 고른다. 미리보기는 앞
  ``SCAN_PAGES_LIMIT``쪽과 마지막 쪽만 그린다 (쪽 수가 많은 파일에서 시간 상한).
"""

from __future__ import annotations

import math
import struct
import threading
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field

RENDER_DPI = 150
MAX_RENDER_PAGES = 4
MAX_EDGE_PIXELS = 1600
MAX_PAGE_PIXELS = 1600 * 1600
# 쪽 고르기: 미리보기 크기, 들여다보는 최대 쪽 수, 빈 쪽으로 보는 잉크 비율, 어두운 픽셀 기준
THUMB_EDGE = 96
SCAN_PAGES_LIMIT = 60
BLANK_INK_RATIO = 0.004
DARK_LEVEL = 200
_LOCK = threading.Lock()
_DARK = bytes(1 if v < DARK_LEVEL else 0 for v in range(256))


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
    pages: tuple[int, ...] = ()  # 그린 쪽 번호 (0부터)
    sizes: tuple[tuple[int, int], ...] = ()  # 이미지마다 (가로, 세로) 픽셀


@dataclass(frozen=True, slots=True)
class PagePick:
    pages: tuple[int, ...]  # 고른 쪽 번호 (0부터, 쪽 순서)
    pages_total: int
    # 잰 쪽의 잉크 비율 {쪽 번호: 비율}. 재지 않은 쪽(앞 SCAN_PAGES_LIMIT 밖)은 없다
    ink: dict[int, float] = field(default_factory=dict)
    blank: tuple[int, ...] = ()  # 빈 쪽으로 보고 뺀 쪽


def choose_pages(ink: dict[int, float], max_pages: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """잉크 비율로 쪽 고르기 (순수 함수). (고른 쪽, 빈 쪽).

    빈 쪽(``BLANK_INK_RATIO`` 미만)을 빼고, 남은 쪽이 ``max_pages`` 이하면 모두, 넘으면 앞쪽
    ``max_pages - 1``쪽 + 마지막 쪽. 모두 빈 쪽이면 앞쪽부터 ``max_pages``쪽 (모델이 NO_TEXT로 답한다).
    """
    if max_pages < 1:
        return (), ()
    order = sorted(ink)
    blank = tuple(i for i in order if ink[i] < BLANK_INK_RATIO)
    kept = [i for i in order if ink[i] >= BLANK_INK_RATIO]
    if not kept:
        return tuple(order[:max_pages]), blank
    if len(kept) <= max_pages:
        return tuple(kept), blank
    if max_pages == 1:
        return (kept[0],), blank
    return (*kept[: max_pages - 1], kept[-1]), blank


def _ink_ratio(page) -> float:
    w, h = page.get_size()
    if w <= 0 or h <= 0:
        raise PdfRenderError("bad_page_size")
    bitmap = page.render(
        scale=THUMB_EDGE / max(w, h),
        grayscale=True,
        may_draw_forms=False,
        draw_annots=False,
        limit_image_cache=True,
    )
    try:
        if bitmap.n_channels != 1:
            raise PdfRenderError("unexpected_format")
        buf = bytes(bitmap.buffer)
        width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    finally:
        bitmap.close()
    if not width or not height:
        return 0.0
    dark = sum(
        buf[y * stride : y * stride + width].translate(_DARK).count(1) for y in range(height)
    )
    return dark / (width * height)


def pick_pages(data: bytes, *, max_pages: int = MAX_RENDER_PAGES) -> PagePick:
    """쪽마다 작은 미리보기의 잉크 비율을 재서 읽을 쪽을 고른다. 동기 CPU 작업 (``asyncio.to_thread``)."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PdfRenderUnavailable from exc
    with _LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("malformed") from exc
        try:
            total = len(pdf)
            measured = sorted(
                {*range(min(total, SCAN_PAGES_LIMIT)), *([total - 1] if total else [])}
            )
            ink: dict[int, float] = {}
            for index in measured:
                page = pdf[index]
                try:
                    ink[index] = _ink_ratio(page)
                finally:
                    page.close()
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("render_failed") from exc
        finally:
            pdf.close()
    pages, blank = choose_pages(ink, max_pages)
    return PagePick(pages=pages, pages_total=total, ink=ink, blank=blank)


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
    data: bytes,
    *,
    max_pages: int = MAX_RENDER_PAGES,
    max_image_bytes: int = 5 * 1024 * 1024,
    pages: Sequence[int] | None = None,
) -> RenderedPages:
    """앞쪽 ``max_pages``쪽(또는 ``pages``로 고른 쪽, 최대 ``max_pages``개)을 회색조 PNG로.

    동기 CPU 작업이다: ``asyncio.to_thread``로 부른다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PdfRenderUnavailable from exc
    images: list[bytes] = []
    drawn: list[int] = []
    sizes: list[tuple[int, int]] = []
    with _LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("malformed") from exc
        try:
            total = len(pdf)
            wanted = (
                range(min(total, max_pages))
                if pages is None
                else [i for i in pages if 0 <= i < total][:max_pages]
            )
            for index in wanted:
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
                        size = (bitmap.width, bitmap.height)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                if len(png) > max_image_bytes:
                    break
                images.append(png)
                drawn.append(index)
                sizes.append(size)
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("render_failed") from exc
        finally:
            pdf.close()
    return RenderedPages(
        images=tuple(images), pages_total=total, pages=tuple(drawn), sizes=tuple(sizes)
    )


def page_texts(data: bytes, pages: Sequence[int]) -> dict[int, str]:
    """PDFium이 읽은 쪽 글 (0부터 쪽 번호 → 글). 텍스트 PDF에서 pypdf 글이 깨져 보일 때만 부른다
    (``adapters.parsers.PdfTextParser``). 동기 CPU 작업. 설치가 없으면 ``PdfRenderUnavailable``."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PdfRenderUnavailable from exc
    out: dict[int, str] = {}
    with _LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("malformed") from exc
        try:
            total = len(pdf)
            for index in pages:
                if not 0 <= index < total:
                    continue
                page = pdf[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        out[index] = textpage.get_text_range()
                    finally:
                        textpage.close()
                finally:
                    page.close()
        except pdfium.PdfiumError as exc:
            raise PdfRenderError("text_failed") from exc
        finally:
            pdf.close()
    return out
