"""프로필·증명사진 규칙 (O21, plan §20 "나 › 프로필").

- 프로필은 공간마다 하나. 인적 사항(이름·생년월일)·연락처·링크·한 줄 소개. 자료 등급은 ``restricted``
  (개인 식별 정보): 모델로 보내는 경로가 없고, 검색 색인에 넣지 않으며, MCP에는 이름·한 줄 소개·링크만
  준다 (``PROFILE_SUMMARY_FIELDS``).
- 사진은 사진 변환 도구(웹 ``/tools/photo``)가 만든 **변환본만** 받는다. 원본은 받지 않는다.
  서버에서도 메타데이터를 지운다 (``strip_metadata``): JPEG는 APP1–APP15(EXIF·XMP·ICC·IPTC…, Adobe
  APP14 색 변환 표시만 남김)·COM·JFIF 썸네일·EOI 뒤 바이트, PNG는 아래 목록 밖의 덩어리(tEXt·zTXt·iTXt·
  eXIf·tIME·iCCP …). 화소 데이터는 다시 인코딩하지 않으므로 바이트 그대로다.
  색 프로필(ICC)도 뺀다: 사진 변환 도구의 canvas 출력은 sRGB이고, 프로필이 없는 그림은 sRGB로 보인다.
- 규격(``spec``)은 사진 변환 도구의 미리 정한 크기(``apps/personal-web/src/lib/photo.ts``의
  ``SIZE_PRESETS`` id)와 같다. 규격이 있으면 가로·세로 비율이 그 규격과 맞아야 한다 (dpi는 자유).
  규격별로 여러 장, 공간에 기본 사진 하나.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


class ProfileRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PhotoSpec(StrEnum):
    """사진 변환 도구의 ``SIZE_PRESETS`` id + 직접 정한 크기."""

    CM_3X4 = "cm-3x4"
    CM_35X45 = "cm-3.5x4.5"
    CM_25X3 = "cm-2.5x3"
    PX_150X200 = "px-150x200"
    CUSTOM = "custom"


# 규격의 (가로, 세로) 비율 기준. 사진 변환 도구는 cm를 300 dpi로 px로 바꾼다 (354×472 등).
SPEC_RATIO: dict[PhotoSpec, tuple[float, float]] = {
    PhotoSpec.CM_3X4: (3.0, 4.0),
    PhotoSpec.CM_35X45: (3.5, 4.5),
    PhotoSpec.CM_25X3: (2.5, 3.0),
    PhotoSpec.PX_150X200: (150.0, 200.0),
}
SPEC_LABEL: dict[PhotoSpec, str] = {
    PhotoSpec.CM_3X4: "3 × 4 cm",
    PhotoSpec.CM_35X45: "3.5 × 4.5 cm",
    PhotoSpec.CM_25X3: "2.5 × 3 cm",
    PhotoSpec.PX_150X200: "150 × 200 px",
    PhotoSpec.CUSTOM: "직접 정한 크기",
}
# 반올림(cm → px)을 견디는 비율 차이
RATIO_TOLERANCE = 0.015

MAX_PHOTO_BYTES = 2 * 1024 * 1024
MIN_PX = 16  # 사진 변환 도구의 MIN_PX·MAX_PX와 같다
MAX_PX = 4000
MAX_PHOTOS = 30  # 공간마다

MIME_JPEG = "image/jpeg"
MIME_PNG = "image/png"
PHOTO_MIMES = (MIME_JPEG, MIME_PNG)

# 프로필 칸 길이
MAX_NAME = 100
MAX_EMAIL = 254
MAX_PHONE = 40
MAX_ADDRESS = 200
MAX_HEADLINE = 200
MAX_LINKS = 10
MAX_LINK_LABEL = 40
MAX_LINK_URL = 500

# MCP(외부 에이전트)에 주는 프로필 칸. 생년월일·연락처·주소·사진은 주지 않는다.
PROFILE_SUMMARY_FIELDS = ("name", "headline", "links")


def photo_spec(value: str) -> PhotoSpec:
    try:
        return PhotoSpec(value)
    except ValueError as exc:
        raise ProfileRuleError("invalid_spec") from exc


def check_spec_size(spec: PhotoSpec, width: int, height: int) -> None:
    if not (MIN_PX <= width <= MAX_PX and MIN_PX <= height <= MAX_PX):
        raise ProfileRuleError("invalid_dimensions")
    ratio = SPEC_RATIO.get(spec)
    if ratio is None:
        return
    want = ratio[0] / ratio[1]
    if abs(width / height - want) / want > RATIO_TOLERANCE:
        raise ProfileRuleError("spec_mismatch")


# ---------------------------------------------------------------- 메타데이터 지우기


@dataclass(frozen=True, slots=True)
class CleanImage:
    data: bytes
    mime: str
    width: int
    height: int
    removed: tuple[str, ...]  # 지운 조각 이름 (APP1, COM, tEXt …)


def sniff_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return MIME_JPEG
    if data[:8] == _PNG_SIG:
        return MIME_PNG
    return None


def strip_metadata(data: bytes) -> CleanImage:
    """JPEG·PNG에서 메타데이터 조각을 빼고 화소 데이터는 그대로 둔다. 그 밖의 형식·깨진 파일은 거부."""
    mime = sniff_mime(data)
    if mime == MIME_JPEG:
        return _strip_jpeg(data)
    if mime == MIME_PNG:
        return _strip_png(data)
    raise ProfileRuleError("unsupported_type")


# SOFn (DHT C4, JPG C8, DAC CC 제외): 높이·너비가 들어 있다
_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_RST = frozenset(range(0xD0, 0xD8))


def _bad() -> ProfileRuleError:
    return ProfileRuleError("invalid_image")


def _jfif_app0(payload: bytes) -> bytes | None:
    """JFIF APP0를 썸네일 없이 다시 쓴다 (버전·밀도 단위·밀도만). JFIF가 아니면 None (버린다)."""
    if len(payload) < 12 or payload[:5] != b"JFIF\x00":
        return None
    body = payload[:12] + b"\x00\x00"  # "JFIF\0", 버전 2, 단위 1, X 2, Y 2 + 썸네일 0×0
    return b"\xff\xe0" + struct.pack(">H", len(body) + 2) + body


def _strip_jpeg(data: bytes) -> CleanImage:
    out = bytearray(b"\xff\xd8")
    removed: list[str] = []
    size: tuple[int, int] | None = None
    seen_sos = False
    i = 2
    n = len(data)
    while True:
        if i >= n or data[i] != 0xFF:
            raise _bad()
        while i < n and data[i] == 0xFF:  # 채움 바이트
            i += 1
        if i >= n:
            raise _bad()
        marker = data[i]
        i += 1
        if marker == 0xD9:  # EOI: 뒤에 붙은 바이트는 버린다
            out += b"\xff\xd9"
            if i < n:
                removed.append("trailer")
            break
        if marker in _RST or marker in (0x01, 0xD8):
            raise _bad()
        if i + 2 > n:
            raise _bad()
        (length,) = struct.unpack(">H", data[i : i + 2])
        if length < 2 or i + length > n:
            raise _bad()
        payload = data[i + 2 : i + length]
        segment = data[i - 2 : i + length]
        i += length
        if marker == 0xE0:
            app0 = _jfif_app0(payload)
            if app0 is None:
                removed.append("APP0")
            else:
                if len(payload) > 14:
                    removed.append("APP0 thumbnail")
                out += app0
            continue
        if 0xE1 <= marker <= 0xEF:
            # Adobe APP14는 색 변환 표시뿐 (YCCK·CMYK 해석에 필요). 나머지 APPn은 모두 메타데이터
            if marker == 0xEE and payload[:5] == b"Adobe" and len(payload) <= 12:
                out += segment
            else:
                removed.append(f"APP{marker - 0xE0}")
            continue
        if marker == 0xFE:
            removed.append("COM")
            continue
        if marker in _SOF:
            if len(payload) < 5:
                raise _bad()
            height, width = struct.unpack(">HH", payload[1:5])
            size = (width, height)
        out += segment
        if marker == 0xDA:  # SOS: 다음 표시까지 부호화된 데이터를 그대로 옮긴다
            seen_sos = True
            j = i
            while True:
                k = data.find(b"\xff", j)
                if k < 0 or k + 1 >= n:
                    raise _bad()
                nxt = data[k + 1]
                if nxt == 0x00 or nxt in _RST:
                    j = k + 2
                    continue
                if nxt == 0xFF:
                    j = k + 1
                    continue
                break
            out += data[i:k]
            i = k
    if size is None or not seen_sos or size[0] == 0 or size[1] == 0:
        raise _bad()
    return CleanImage(bytes(out), MIME_JPEG, size[0], size[1], tuple(removed))


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# 남기는 덩어리: 화소·팔레트·투명·색 공간 표시·인쇄 밀도. 나머지(글·시각·EXIF·ICC·사설 덩어리)는 뺀다.
_PNG_KEEP = frozenset(
    {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"pHYs"}
)


def _strip_png(data: bytes) -> CleanImage:
    out = bytearray(_PNG_SIG)
    removed: list[str] = []
    size: tuple[int, int] | None = None
    i = 8
    n = len(data)
    first = True
    while True:
        if i + 12 > n:
            raise _bad()
        (length,) = struct.unpack(">I", data[i : i + 4])
        ctype = data[i + 4 : i + 8]
        end = i + 12 + length
        if end > n or not ctype.isalpha():
            raise _bad()
        body = data[i + 8 : i + 8 + length]
        (crc,) = struct.unpack(">I", data[i + 8 + length : end])
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise _bad()
        if first:
            if ctype != b"IHDR" or length != 13:
                raise _bad()
            size = struct.unpack(">II", body[:8])
            first = False
        if ctype in _PNG_KEEP:
            out += data[i:end]
        elif ctype[:1].isupper():  # 모르는 필수 덩어리: 해석할 수 없는 파일
            raise _bad()
        else:
            removed.append(ctype.decode("ascii"))
        i = end
        if ctype == b"IEND":
            if i < n:
                removed.append("trailer")
            break
    if size is None or size[0] == 0 or size[1] == 0 or b"IDAT" not in out:
        raise _bad()
    return CleanImage(bytes(out), MIME_PNG, size[0], size[1], tuple(removed))


def has_metadata(data: bytes) -> bool:
    """확인용: 지울 조각이 남아 있나 (JPEG APP1–15·COM, PNG 목록 밖 덩어리)."""
    try:
        return bool(strip_metadata(data).removed)
    except ProfileRuleError:
        return False


# ---------------------------------------------------------------- 프로필 칸


@dataclass(frozen=True, slots=True)
class ProfileLink:
    label: str
    url: str


def clean_text(value: str | None, limit: int, code: str) -> str | None:
    if value is None:
        return None
    v = " ".join(value.split())
    if not v:
        return None
    if len(v) > limit:
        raise ProfileRuleError(code)
    return v


def clean_email(value: str | None) -> str | None:
    v = clean_text(value, MAX_EMAIL, "invalid_email")
    if v is None:
        return None
    local, at, domain = v.partition("@")
    if not at or not local or "." not in domain or " " in v or domain.startswith("."):
        raise ProfileRuleError("invalid_email")
    return v


_PHONE_CHARS = frozenset("0123456789+-() .")


def clean_phone(value: str | None) -> str | None:
    v = clean_text(value, MAX_PHONE, "invalid_phone")
    if v is None:
        return None
    if not set(v) <= _PHONE_CHARS or sum(c.isdigit() for c in v) < 4:
        raise ProfileRuleError("invalid_phone")
    return v


def clean_links(links: list[tuple[str | None, str | None]]) -> list[ProfileLink]:
    out: list[ProfileLink] = []
    for label, url in links:
        u = (url or "").strip()
        lab = clean_text(label, MAX_LINK_LABEL, "invalid_link")
        if not u and not lab:
            continue  # 빈 줄은 무시
        if not u or len(u) > MAX_LINK_URL or " " in u:
            raise ProfileRuleError("invalid_link")
        if not u.lower().startswith(("http://", "https://")):
            if "." not in u:
                raise ProfileRuleError("invalid_link")
            u = "https://" + u
        out.append(ProfileLink(lab or "", u))
    if len(out) > MAX_LINKS:
        raise ProfileRuleError("too_many_links")
    return out


# ---------------------------------------------------------------- 병역·보훈·장애·희망 조건 (c0041)
# 모두 선택 칸이다. restricted: 모델·검색 색인·MCP(profile_summary 포함)로 나가지 않는다.
# 희망 직무도 MCP에 넣지 않는다 — 에이전트가 직무를 알아야 하면 공고·지원에서 본다 (docs/architecture/mcp.md).


class MilitaryStatus(StrEnum):
    SERVED = "served"  # 군필
    NOT_SERVED = "not_served"  # 미필
    EXEMPT = "exempt"  # 면제
    NOT_APPLICABLE = "not_applicable"  # 비대상
    SERVING = "serving"  # 복무 중


class EmploymentType(StrEnum):
    REGULAR = "regular"  # 정규직
    CONTRACT = "contract"  # 계약직
    INTERN = "intern"  # 인턴


MAX_MILITARY_TEXT = 20  # 군별·계급
MAX_EXEMPTION_REASON = 200
MAX_PREFERENCE_ITEMS = 10
MAX_PREFERENCE_ITEM = 50
MAX_SALARY = 100
MAX_AVAILABLE_FROM = 50

# 복무 기간·군별·계급을 두는 상태 (그 밖의 상태로 바꾸면 그 칸을 비운다)
SERVICE_STATUSES = frozenset({MilitaryStatus.SERVED, MilitaryStatus.SERVING})

DETAIL_FIELDS = (
    "military_status",
    "military_branch",
    "military_rank",
    "military_started_on",
    "military_ended_on",
    "military_exemption_reason",
    "veteran",
    "disability",
    "desired_roles",
    "desired_locations",
    "desired_salary",
    "available_from",
    "employment_types",
)


def clean_items(values: list[str] | None, code: str) -> list[str]:
    """목록 칸: 앞뒤·겹친 공백 정리, 빈 줄 빼기, 대소문자 무시 중복 빼기, 10개·항목 50자."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        s = " ".join(str(v or "").split())
        if not s:
            continue
        if len(s) > MAX_PREFERENCE_ITEM:
            raise ProfileRuleError(code)
        if s.casefold() in seen:
            continue
        seen.add(s.casefold())
        out.append(s)
    if len(out) > MAX_PREFERENCE_ITEMS:
        raise ProfileRuleError(code)
    return out


def first_of_month(value: date | None, code: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, date) or not (1900 <= value.year <= 2100):
        raise ProfileRuleError(code)
    return date(value.year, value.month, 1)


def clean_details(values: dict[str, Any]) -> dict[str, Any]:
    """준 칸만 검사·정리한다 (PATCH). 칸 사이 규칙은 ``settle_details``."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key == "military_status":
            try:
                out[key] = None if value is None else MilitaryStatus(value).value
            except ValueError as exc:
                raise ProfileRuleError("invalid_military_status") from exc
        elif key in ("military_branch", "military_rank"):
            out[key] = clean_text(value, MAX_MILITARY_TEXT, f"invalid_{key}")
        elif key == "military_exemption_reason":
            out[key] = clean_text(value, MAX_EXEMPTION_REASON, "invalid_exemption_reason")
        elif key in ("military_started_on", "military_ended_on"):
            out[key] = first_of_month(value, "invalid_military_period")
        elif key in ("veteran", "disability"):
            if value is not None and not isinstance(value, bool):
                raise ProfileRuleError(f"invalid_{key}")
            out[key] = value
        elif key in ("desired_roles", "desired_locations"):
            out[key] = clean_items(value, f"invalid_{key}")
        elif key == "desired_salary":
            out[key] = clean_text(value, MAX_SALARY, "invalid_desired_salary")
        elif key == "available_from":
            out[key] = clean_text(value, MAX_AVAILABLE_FROM, "invalid_available_from")
        elif key == "employment_types":
            try:
                types = {EmploymentType(v).value for v in value or []}
            except ValueError as exc:
                raise ProfileRuleError("invalid_employment_types") from exc
            out[key] = [t.value for t in EmploymentType if t.value in types]
        else:
            raise ProfileRuleError("unknown_field")
    return out


def settle_details(row: Any) -> None:
    """한 행의 병역 칸 사이 규칙. 복무하지 않은 상태면 복무 칸을, 면제가 아니면 면제 사유를 비운다."""
    status = row.military_status
    if status not in SERVICE_STATUSES:
        row.military_branch = row.military_rank = None
        row.military_started_on = row.military_ended_on = None
    if status != MilitaryStatus.EXEMPT:
        row.military_exemption_reason = None
    if (
        row.military_started_on
        and row.military_ended_on
        and row.military_ended_on < row.military_started_on
    ):
        raise ProfileRuleError("invalid_military_period")
