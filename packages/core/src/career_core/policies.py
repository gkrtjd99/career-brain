"""기본 정책 구현."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from career_core.domain.sources import DataClass
from career_core.ports import ActorContext


class ProviderEgressPolicy:
    """제공자별로 허용한 자료 등급만 외부로 보낸다.

    - 설정하지 않은 제공자는 거부.
    - ``restricted`` 등급은 설정에 들어 있어도 거부.
    """

    def __init__(self, allowed: Mapping[str, Iterable[str]] | None = None) -> None:
        self._allowed = {
            provider: frozenset(DataClass(c) for c in classes) - {DataClass.RESTRICTED}
            for provider, classes in (allowed or {}).items()
        }

    def allows(self, actor: ActorContext, provider: str, data_class: str, purpose: str) -> bool:
        try:
            dc = DataClass(data_class)
        except ValueError:
            return False
        return dc in self._allowed.get(provider, frozenset())
