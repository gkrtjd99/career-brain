"""읽은 글의 제어 문자 정리 (2026-11-18 사용 점검).

Chrome으로 인쇄한 한국어 PDF는 pypdf가 띄어쓰기를 ``\\x01``(글꼴에 따라 ``\\t``)로 읽는다. 이런 제어 문자가
블록 글에 남으면 인용·검색 조각·내보내기·경험 기간 글에 그대로 나간다. 블록을 만들 때(모든 파서·OCR 글)
한 번 지운다. **글자 수를 바꾸지 않는다** (한 글자 → 공백 한 칸): OCR 글의 좌표·원문 위치가 어긋나지 않게.
줄 바꿈(``\\n``)과 탭(``\\t``)은 남긴다 (파서가 따로 다룬다).
"""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x0d\x0e-\x1f\x7f-\x9f]")


def clean_control(text: str | None) -> str:
    """제어 문자(줄 바꿈·탭 제외)를 공백으로. 길이는 그대로."""
    return _CONTROL.sub(" ", text or "")


def clean_inline(text: str | None) -> str:
    """한 줄 값(제목·기간·문장): 제어 문자·탭을 공백으로 바꾸고 겹친 공백을 하나로."""
    return " ".join(clean_control(text).replace("\t", " ").split())
