"""로컬 파일 저장소. 공간별 디렉터리 + 내용 주소(sha256) 저장."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

_KEY = re.compile(r"[0-9a-f]{64}")


class LocalBlobStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def _path(self, workspace_id: UUID, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("blob key must be a sha256 hex digest")
        return self.root / str(UUID(str(workspace_id))) / key[:2] / key

    async def put(self, workspace_id: UUID, key: str, data: bytes, content_type: str) -> str:
        if hashlib.sha256(data).hexdigest() != key:
            raise ValueError("blob key does not match content")
        path = self._path(workspace_id, key)

        def write() -> None:
            if path.exists():
                return
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_name(f".{key}.{uuid4().hex}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)

        await asyncio.to_thread(write)
        return key

    async def get(self, workspace_id: UUID, key: str) -> bytes:
        return await asyncio.to_thread(self._path(workspace_id, key).read_bytes)

    async def disk_usage(self, workspace_id: UUID) -> tuple[int, int]:
        """공간 디렉터리의 (파일 수, 바이트). 쓰다 만 임시 파일은 세지 않는다."""
        base = self.root / str(UUID(str(workspace_id)))

        def walk() -> tuple[int, int]:
            files = size = 0
            if not base.is_dir():
                return 0, 0
            for path in base.rglob("*"):
                if path.is_file() and _KEY.fullmatch(path.name):
                    files += 1
                    size += path.stat().st_size
            return files, size

        return await asyncio.to_thread(walk)
