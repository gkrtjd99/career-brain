"""작업 lease 소유 확인 (c0023).

worker가 작업 하나를 집으면(``core.claim_job``) 그 집기마다 새 ``lease_token``이 생긴다. worker는 처리하는
동안 그 값(작업 ID·``attempts``·``lease_token``)을 문맥 변수에 둔다. 핸들러 안에서 결과를 저장하는
트랜잭션(예: 실행 단계의 위치·결과)은 같은 트랜잭션에서 작업 행을 잠그고 아직 이 worker가 lease를 쥐고
있는지 확인한다:

- 상태가 ``running``이고
- ``attempts``가 집을 때와 같고
- ``lease_token``이 집을 때와 같고 (``attempts``만으로는 부족하다: 외부 한도 대기는 ``attempts``를 0으로
  되돌리므로 다른 worker가 나중에 같은 ``attempts``로 다시 집을 수 있다)
- ``locked_until``이 아직 지나지 않았다.

아니면 ``LeaseLost``를 던져 그 트랜잭션을 되돌린다. worker는 그 작업 행을 건드리지 않고 넘어간다 (새 주인이
있으면 그쪽이, lease가 지났을 뿐이면 다음에 집는 worker가 저장된 위치부터 다시 한다).

작업 행을 먼저 잠그고 그 다음 대상 행(실행 등)을 잠근다. 같은 대상을 다루는 두 worker가 언제나 같은 순서로
잠그므로 서로 기다리다 막히지 않는다.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db.models import Job


@dataclass(frozen=True, slots=True)
class Lease:
    job_id: UUID
    target_id: UUID
    attempts: int
    # c0023 전 DB의 ``claim_job``은 돌려주지 않는다 (None이면 attempts만 비교)
    token: UUID | None


class LeaseLost(Exception):
    """이 worker가 더 이상 작업의 lease를 쥐고 있지 않다. 결과를 저장하지 않는다."""


_CURRENT: ContextVar[Lease | None] = ContextVar("career_job_lease", default=None)


def current_lease() -> Lease | None:
    return _CURRENT.get()


def set_lease(lease: Lease | None):
    """문맥 변수에 지금 처리 중인 작업의 lease를 둔다. 되돌릴 때 ``reset_lease(token)``."""
    return _CURRENT.set(lease)


def reset_lease(token) -> None:
    _CURRENT.reset(token)


async def lease_held(s: AsyncSession, lease: Lease) -> bool:
    """같은 트랜잭션에서 작업 행을 잠그고(FOR UPDATE) 아직 이 lease인지 본다 (시각은 DB 시계)."""
    q = (
        select(Job.id)
        .where(
            Job.id == lease.job_id,
            Job.target_id == lease.target_id,
            Job.status == "running",
            Job.attempts == lease.attempts,
            Job.locked_until.isnot(None),
            Job.locked_until > func.now(),
        )
        .with_for_update()
    )
    if lease.token is not None:
        q = q.where(Job.lease_token == lease.token)
    return await s.scalar(q) is not None


async def guard_lease(s: AsyncSession, target_id: UUID) -> None:
    """worker 안에서 부르면 lease를 확인하고, 잃었으면 ``LeaseLost``. worker 밖(직접 호출)이면 아무것도 안 한다."""
    lease = current_lease()
    if lease is None or lease.target_id != target_id:
        return
    if not await lease_held(s, lease):
        raise LeaseLost(str(lease.job_id))


__all__ = [
    "Lease",
    "LeaseLost",
    "current_lease",
    "guard_lease",
    "lease_held",
    "reset_lease",
    "set_lease",
]
