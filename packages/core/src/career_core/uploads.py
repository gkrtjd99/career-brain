"""업로드 검사: 크기, 실제 내용 기반 MIME 판별, 파일명 정리.

선언된 Content-Type을 그대로 믿지 않는다. 바이트로 판별한 형식과 선언이 어긋나면 거부한다.
DOCX(c0023)는 zip 안에 본문 부분(``word/document.xml``)이 있어야 하고, 항목 수가 많으면 판별하지 않는다
(압축 해제 상한은 파서·검사가 ``domain.docx_zip``으로 다시 지킨다). 다른 zip은 받지 않는다.
PPTX·XLSX·HWPX(2026-11-17)도 zip 안 본문 부분 이름으로 판별한다 (``docx_zip.office_kind``).
옛 한글(.hwp, OLE 복합 문서 또는 한글 3.0)은 받아 두되 읽지 않는다 (파서가 ``hwp_unsupported``).
다른 OLE 문서(.doc·.xls·.ppt)는 받지 않는다.
"""

from __future__ import annotations

import posixpath
import unicodedata

from career_core.domain.docx_zip import office_kind

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# HWPX zip 첫 항목 ``mimetype``에 적힌 값
HWPX_MIME = "application/hwp+zip"
HWP_MIME = "application/x-hwp"
_ZIP_MIMES = {"docx": DOCX_MIME, "pptx": PPTX_MIME, "xlsx": XLSX_MIME, "hwpx": HWPX_MIME}
# 브라우저·OS가 한글 파일에 붙이는 다른 이름 → 위 이름
_ALIASES = {
    "application/haansofthwp": HWP_MIME,
    "application/vnd.hancom.hwp": HWP_MIME,
    "application/hwp": HWP_MIME,
    "application/haansofthwpx": HWPX_MIME,
    "application/vnd.hancom.hwpx": HWPX_MIME,
    "application/x-hwpx": HWPX_MIME,
}
# zip으로 된 형식에 OS가 붙이기도 하는 일반 zip 이름 (내용으로 판별한 형식을 따른다)
_ZIP_GENERIC = {"application/zip", "application/x-zip-compressed"}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# 한글 5.x는 OLE 안 ``FileHeader`` 스트림, 한글 3.0은 파일 맨 앞에 이 서명이 있다
_HWP_SIGNATURE = b"HWP Document File"
ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/markdown",
        "text/html",
        DOCX_MIME,
        PPTX_MIME,
        XLSX_MIME,
        HWPX_MIME,
        HWP_MIME,
    }
)
_GENERIC = {"", "application/octet-stream"}


class UploadRejected(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"PK\x03\x04"):
        return _ZIP_MIMES.get(office_kind(data) or "")
    if data.startswith(_HWP_SIGNATURE):
        return HWP_MIME
    if data.startswith(_OLE_MAGIC):
        return HWP_MIME if _HWP_SIGNATURE in data else None
    if b"\x00" in data[:8192]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    head = text.lstrip("﻿ \t\r\n")[:64].lower()
    if head.startswith(("<!doctype html", "<html")):
        return "text/html"
    return "text/plain"


def resolve_mime(data: bytes, declared: str | None, filename: str) -> str:
    sniffed = sniff_mime(data)
    if sniffed is None:
        raise UploadRejected("unsupported_type", 415)
    decl = (declared or "").split(";")[0].strip().lower()
    decl = _ALIASES.get(decl, decl)
    if sniffed == "text/plain":
        # 선언이 텍스트가 아닌 형식이면 확장자와 상관없이 거부한다 (확장자로 검사를 우회하지 못하게).
        if decl not in _GENERIC and not decl.startswith("text/"):
            raise UploadRejected("mime_mismatch", 415)
        if decl == "text/markdown" or filename.lower().endswith((".md", ".markdown")):
            return "text/markdown"
        return "text/plain"
    if decl in _ZIP_GENERIC and sniffed in _ZIP_MIMES.values():
        return sniffed
    if decl not in _GENERIC and decl != sniffed:
        raise UploadRejected("mime_mismatch", 415)
    return sniffed


def clean_filename(name: str | None) -> str:
    """경로 성분·제어 문자를 제거한 표시용 이름. 저장 경로에는 쓰지 않는다."""
    base = posixpath.basename((name or "").replace("\\", "/"))
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    return (base or "upload")[:255]
