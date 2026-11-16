"""로컬 `.env` 읽기 (git 제외 파일). 이미 설정된 환경변수는 덮어쓰지 않는다.

형식: `KEY=VALUE` 한 줄씩. `#` 주석, 빈 줄, `export ` 접두어, 따옴표로 감싼 값을 허용한다.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_env_file(
    path: Path | str = ".env", environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """읽어서 비어 있는 변수만 채운다. 채운 변수 이름을 돌려준다 (값은 돌려주지 않는다)."""
    env = os.environ if environ is None else environ
    p = Path(path)
    if not p.is_file():
        return []
    loaded = []
    for key, value in parse_env(p.read_text("utf-8")).items():
        if key not in env:
            env[key] = value
            loaded.append(key)
    return loaded
