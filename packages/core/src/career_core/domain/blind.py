"""블라인드 채용에서 빼는 칸 (plan §19.5 이력서 블라인드 모드 준비).

공공기관 표준 이력서처럼 출신·인적 사항을 받지 않는 지원에서, 이력서 판본(O17)이 출력에서 뺄 칸의 목록이다.
이력서 판본의 출력(``domain.resume.build_render``)이 이 목록대로 칸을 뺀다 (``test_resumes.py``). 칸 이름은
``표.칸``이고, 표 전체를 빼면 ``표.*``. 성별·가족 사항은 지금 저장하는 칸이 없다 — 칸이 생기면 여기에 더한다.

- 사진: ``profile_photos.*``, ``profiles.default_photo_id``
- 생년월일: ``profiles.birth_date``
- 출신 학교: ``education.school``·``campus`` (전공·학위 과정은 직무와 이어져 남긴다), 자격·이수의 옛 학력
  기록(``credentials`` 중 ``kind = degree``)의 이름·발급 기관
- 주소: ``profiles.address`` (출신 지역을 드러낸다)
- 병역·보훈·장애: 남기는 것이 기본이다 (가점·법정 우대 확인에 쓰인다). 지원이 받지 않으면 판본에서 끈다.
"""

from __future__ import annotations

# 늘 빼는 칸
BLIND_FIELDS: tuple[str, ...] = (
    "profile_photos.*",
    "profiles.default_photo_id",
    "profiles.birth_date",
    "profiles.address",
    "education.school",
    "education.campus",
    "credentials[kind=degree].name",
    "credentials[kind=degree].issuer",
)

# 지원에 따라 판본에서 고르는 칸 (기본은 남김)
BLIND_OPTIONAL_FIELDS: tuple[str, ...] = (
    "profiles.military_status",
    "profiles.military_branch",
    "profiles.military_rank",
    "profiles.military_started_on",
    "profiles.military_ended_on",
    "profiles.military_exemption_reason",
    "profiles.veteran",
    "profiles.disability",
)


def blind_sensitive_fields(*, include_optional: bool = False) -> tuple[str, ...]:
    """블라인드 판본에서 뺄 칸. ``include_optional``이면 지원에 따라 고르는 칸까지."""
    return BLIND_FIELDS + (BLIND_OPTIONAL_FIELDS if include_optional else ())
