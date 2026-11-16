"""pgvector `vector` 컬럼. 별도 패키지 없이 텍스트 표현('[1,2,3]')으로 주고받는다."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.types import UserDefinedType


def to_pgvector(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "vector"

    def bind_processor(self, dialect):
        def process(value):
            return None if value is None else to_pgvector(value)

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or isinstance(value, list | tuple):
                return value
            return [float(x) for x in value.strip("[]").split(",") if x]

        return process
