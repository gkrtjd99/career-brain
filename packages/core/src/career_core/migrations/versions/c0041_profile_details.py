"""core: 프로필 더하기 — 병역·보훈·장애·희망 조건 (plan §20 "나 › 프로필", 한국 지원서 칸)

Revision ID: c0041
Revises: c0040

``core.profiles``에 칸을 더한다. 모두 선택이고, 프로필의 다른 칸처럼 restricted로 다룬다 — 모델로 보내는
경로·검색 색인이 없고 MCP ``profile_summary``에도 넣지 않는다 (희망 직무 포함).

- 병역: ``military_status``(``served`` 군필·``not_served`` 미필·``exempt`` 면제·``not_applicable`` 비대상·
  ``serving`` 복무 중), 군별·계급(글), 복무 시작·끝 달(그 달 1일, 끝 ≥ 시작), 면제 사유(면제일 때만).
- 보훈 대상·장애: ``veteran``·``disability`` bool NULL (NULL = 적지 않음).
- 희망 조건: 직무·근무지(``text[]``, 10개 이하), 희망 연봉·입사 가능일(글), 고용 형태(``regular``·
  ``contract``·``intern`` 중 고른 것).

기존 행은 새 칸이 비어 있다(배열은 서버 기본값 ``'{}'``): 행을 다시 쓰지 않으므로 RLS 문맥이 필요 없다.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c0041"
down_revision = "c0040"
branch_labels = None
depends_on = None

S = "core"
MILITARY = ("served", "not_served", "exempt", "not_applicable", "serving")
EMPLOYMENT = ("regular", "contract", "intern")

_ARRAYS = ("desired_roles", "desired_locations", "employment_types")
_CHECKS = (
    "ck_profiles_military",
    "ck_profiles_military_period",
    "ck_profiles_military_months",
    "ck_profiles_preferences",
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    for name in (
        "military_status",
        "military_branch",
        "military_rank",
        "military_exemption_reason",
        "desired_salary",
        "available_from",
    ):
        op.add_column("profiles", sa.Column(name, sa.Text(), nullable=True), schema=S)
    op.add_column("profiles", sa.Column("military_started_on", sa.Date(), nullable=True), schema=S)
    op.add_column("profiles", sa.Column("military_ended_on", sa.Date(), nullable=True), schema=S)
    op.add_column("profiles", sa.Column("veteran", sa.Boolean(), nullable=True), schema=S)
    op.add_column("profiles", sa.Column("disability", sa.Boolean(), nullable=True), schema=S)
    for name in _ARRAYS:
        op.add_column(
            "profiles",
            sa.Column(
                name, ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
            ),
            schema=S,
        )
    op.create_check_constraint(
        "ck_profiles_military",
        "profiles",
        f"(military_status IS NULL OR military_status IN ({_in(MILITARY)})) "
        "AND (military_branch IS NULL OR length(military_branch) <= 20) "
        "AND (military_rank IS NULL OR length(military_rank) <= 20) "
        "AND (military_exemption_reason IS NULL OR "
        "(length(military_exemption_reason) <= 200 AND military_status = 'exempt'))",
        schema=S,
    )
    op.create_check_constraint(
        "ck_profiles_military_period",
        "profiles",
        "military_ended_on IS NULL OR military_started_on IS NULL "
        "OR military_ended_on >= military_started_on",
        schema=S,
    )
    op.create_check_constraint(
        "ck_profiles_military_months",
        "profiles",
        "(military_started_on IS NULL OR extract(day FROM military_started_on) = 1) "
        "AND (military_ended_on IS NULL OR extract(day FROM military_ended_on) = 1)",
        schema=S,
    )
    op.create_check_constraint(
        "ck_profiles_preferences",
        "profiles",
        "cardinality(desired_roles) <= 10 AND cardinality(desired_locations) <= 10 "
        f"AND employment_types <@ ARRAY[{_in(EMPLOYMENT)}]::text[] "
        "AND array_position(desired_roles, NULL) IS NULL "
        "AND array_position(desired_locations, NULL) IS NULL "
        "AND (desired_salary IS NULL OR length(desired_salary) <= 100) "
        "AND (available_from IS NULL OR length(available_from) <= 50)",
        schema=S,
    )


def downgrade() -> None:
    for name in _CHECKS:
        op.drop_constraint(name, "profiles", schema=S, type_="check")
    for name in (
        *_ARRAYS,
        "disability",
        "veteran",
        "military_ended_on",
        "military_started_on",
        "available_from",
        "desired_salary",
        "military_exemption_reason",
        "military_rank",
        "military_branch",
        "military_status",
    ):
        op.drop_column("profiles", name, schema=S)
