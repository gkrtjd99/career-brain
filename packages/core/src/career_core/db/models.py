"""공간·actor·membership.

기본값은 Python 쪽에서 채운다(server default 미사용). RLS 아래에서 INSERT ... RETURNING이
아직 보이지 않는 행을 다시 읽으려다 실패하지 않게 하기 위해서다.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from career_core.db.base import CORE_SCHEMA, Base, utcnow


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Actor(Base):
    """코어가 아는 작성자. 로그인 계정과의 매핑은 각 실행 앱(개인/서비스)이 소유한다."""

    __tablename__ = "actors"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role in ('owner', 'editor', 'viewer')", name="ck_memberships_role"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.actors.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
