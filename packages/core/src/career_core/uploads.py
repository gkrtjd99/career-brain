"""업로드 검사: 크기, 실제 내용 기반 MIME 판별, 파일명 정리.

선언된 Content-Type을 그대로 믿지 않는다. 바이트로 판별한 형식과 선언이 어긋나면 거부한다.
DOCX(c0023)는 zip 안에 본문 부분(``word/document.xml``)이 있어야 하고, 항목 수가 많으면 판별하지 않는다
(압축 해제 상한은 파서·검사가 ``domain.docx_zip``으로 다시 지킨다). 다른 zip은 받지 않는다.
"""

from __future__ import annotations

import posixpath
import unicodedata

from career_core.domain.docx_zip import is_docx

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/markdown",
        "text/html",
        DOCX_MIME,
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
        return DOCX_MIME if is_docx(data) else None
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
    if sniffed == "text/plain":
        # 선언이 텍스트가 아닌 형식이면 확장자와 상관없이 거부한다 (확장자로 검사를 우회하지 못하게).
        if decl not in _GENERIC and not decl.startswith("text/"):
            raise UploadRejected("mime_mismatch", 415)
        if decl == "text/markdown" or filename.lower().endswith((".md", ".markdown")):
            return "text/markdown"
        return "text/plain"
    if decl not in _GENERIC and decl != sniffed:
        raise UploadRejected("mime_mismatch", 415)
    return sniffed


def clean_filename(name: str | None) -> str:
    """경로 성분·제어 문자를 제거한 표시용 이름. 저장 경로에는 쓰지 않는다."""
    base = posixpath.basename((name or "").replace("\\", "/"))
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    return (base or "upload")[:255]
