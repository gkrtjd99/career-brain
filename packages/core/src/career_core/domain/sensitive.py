"""제출·내보내기 전 민감 정보·회사 기밀 재검사 (plan §11). 순수 코드, 경고만 한다.

- 주민등록번호, 전화번호, 이메일: 본인 연락처(문서 설정 ``own_contacts``)는 뺀다.
- "대외비·기밀·사내 한정·confidential" 같은 회사 기밀 표시.

찾은 값을 지우거나 고치지 않는다. 사용자가 보고 직접 고치거나, 그대로 둔다고 확인(acknowledge)한다.
결과의 ``detail.masked``는 가린 값이다 (응답·로그에 원래 값을 다시 싣지 않는다).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from career_core.domain.documents import Finding, split_sentences

_RRN = re.compile(
    r"(?<![\d-])(\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))\s?-?\s?([1-8])(\d{6})(?![\d-])"
)
_MOBILE = re.compile(r"(?<![\d+])(?:\+82[-\s.]?|0)1[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")
# 유선 번호는 구분자가 있을 때만 (아무 숫자열을 번호로 보지 않게)
_LANDLINE = re.compile(
    r"(?<![\d+])(?:\+82[-\s.]?|0)(?:2|[3-6][1-5]|70)[-.)]\s?\d{3,4}[-.]\d{4}(?!\d)"
)
_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
_CONFIDENTIAL = re.compile(
    r"(대외비|기밀(?!성)|극비|사내\s*한정|사내\s*전용|내부\s*(?:용|자료|문서)|영업\s*비밀|비밀\s*유지\s*(?:계약|서약)|"
    r"\bconfidential\b|\binternal\s+(?:use\s+)?only\b|\bproprietary\b|\bNDA\b|\bdo\s+not\s+distribute\b)",
    re.IGNORECASE,
)


def _digits(value: str) -> str:
    d = re.sub(r"\D", "", value or "")
    return "0" + d[2:] if d.startswith("82") else d


def _mask(value: str, keep: int = 3) -> str:
    v = value.strip()
    return v[:keep] + "*" * max(0, len(v) - keep)


def _mask_email(value: str) -> str:
    name, _, domain = value.partition("@")
    return f"{name[:1]}***@{domain}"


@dataclass(frozen=True, slots=True)
class OwnContacts:
    emails: frozenset[str] = frozenset()
    phones: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, data: dict | None) -> OwnContacts:
        data = data or {}
        emails = data.get("emails") or []
        phones = data.get("phones") or []
        return cls(
            frozenset(e.strip().lower() for e in emails if isinstance(e, str) and e.strip()),
            frozenset(_digits(p) for p in phones if isinstance(p, str) and _digits(p)),
        )

    def as_dict(self) -> dict[str, list[str]]:
        return {"emails": sorted(self.emails), "phones": sorted(self.phones)}


def sensitive_findings(
    text: str, *, own: OwnContacts | None = None, block_id: str | None = None
) -> list[Finding]:
    own = own or OwnContacts()
    out: list[Finding] = []
    for sentence in split_sentences(text):
        for m in _RRN.finditer(sentence):
            out.append(
                Finding(
                    "resident_registration_number",
                    "주민등록번호로 보이는 값이 있습니다. 지원 문서에는 넣지 않습니다.",
                    block_id,
                    sentence,
                    {"masked": f"{m.group(1)}-{m.group(2)}******"},
                    severity="error",
                )
            )
        rrn_spans = [m.span() for m in _RRN.finditer(sentence)]
        phones = [*_MOBILE.finditer(sentence), *_LANDLINE.finditer(sentence)]
        seen: set[str] = set()
        for m in phones:
            if any(a <= m.start() < b for a, b in rrn_spans):
                continue
            digits = _digits(m.group(0))
            if digits in own.phones or digits in seen:
                continue
            seen.add(digits)
            out.append(
                Finding(
                    "phone_number",
                    "본인 연락처가 아닌 전화번호가 있습니다. 다른 사람·회사의 번호인지 확인합니다.",
                    block_id,
                    sentence,
                    {"masked": _mask(m.group(0), 4)},
                )
            )
        for m in _EMAIL.finditer(sentence):
            if m.group(0).lower() in own.emails:
                continue
            out.append(
                Finding(
                    "email_address",
                    "본인 연락처가 아닌 이메일 주소가 있습니다. 다른 사람·회사의 주소인지 확인합니다.",
                    block_id,
                    sentence,
                    {"masked": _mask_email(m.group(0))},
                )
            )
        hits = sorted({m.group(0) for m in _CONFIDENTIAL.finditer(sentence)})
        if hits:
            out.append(
                Finding(
                    "company_confidential",
                    "회사 기밀·대외비 표시가 있습니다. 공개해도 되는 내용인지 확인합니다.",
                    block_id,
                    sentence,
                    {"terms": hits},
                )
            )
    return out


def sensitive_hash(items: Iterable[tuple[str, Sequence[Finding]]]) -> str | None:
    """확인(acknowledge) 대상의 지문. 경고가 없으면 None. 내용·위치가 바뀌면 값이 달라진다."""
    rows = sorted(
        [doc, f.block_id or "", f.code, f.sentence or ""] for doc, fs in items for f in fs
    )
    if not rows:
        return None
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["OwnContacts", "sensitive_findings", "sensitive_hash"]
