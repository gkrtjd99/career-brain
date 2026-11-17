"""작은 비밀 상자: 화면에서 넣은 모델 키를 DB에 암호화해 둔다 (Fernet, ``cryptography``).

- 암호 키: ``CAREER_SECRET_KEY`` 환경변수(urlsafe base64 Fernet 키), 없으면 ``key_file``.
  파일이 없으면 처음 암호화할 때 만든다 (권한 0600). 복호화만 할 때는 만들지 않는다 (풀 것이 없다).
- 키 값·암호 키는 로그·예외·repr에 넣지 않는다. 복호화에 실패하면(다른 암호 키, 손상) ``None``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SECRET_KEY_ENV = "CAREER_SECRET_KEY"


class SecretBoxError(RuntimeError):
    """암호 키 설정 문제. 메시지에 키 값을 넣지 않는다."""


class SecretBox:
    def __init__(
        self,
        *,
        key: str | bytes | None = None,
        key_file: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if env is None else env
        raw = key if key is not None else env.get(SECRET_KEY_ENV) or None
        self._fernet: Fernet | None = None
        if raw is not None:
            self._fernet = _fernet(raw, SECRET_KEY_ENV)
        self._file = Path(key_file) if key_file is not None else None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "SecretBox(<hidden>)"

    def _load(self, *, create: bool) -> Fernet | None:
        if self._fernet is not None:
            return self._fernet
        if self._file is None:
            if create:
                raise SecretBoxError(f"set {SECRET_KEY_ENV} or a secret key file")
            return None
        with self._lock:
            if self._fernet is not None:
                return self._fernet
            if self._file.exists():
                self._fernet = _fernet(self._file.read_bytes().strip(), str(self._file))
            elif create:
                self._fernet = _fernet(_create_key_file(self._file), str(self._file))
            return self._fernet

    def encrypt(self, value: str) -> bytes:
        return self._load(create=True).encrypt(value.encode("utf-8"))  # type: ignore[union-attr]

    def decrypt(self, token: bytes) -> str | None:
        fernet = self._load(create=False)
        if fernet is None:
            return None
        try:
            return fernet.decrypt(bytes(token)).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError):
            return None


def _fernet(raw: str | bytes, where: str) -> Fernet:
    try:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)
    except (ValueError, TypeError):
        # 값은 적지 않는다
        raise SecretBoxError(f"{where}: not a Fernet key (32 bytes, urlsafe base64)") from None


def _create_key_file(path: Path) -> bytes:
    """새 암호 키를 0600 파일로. 다른 프로세스가 먼저 만들었으면 그 키를 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(key + b"\n")
    return key


def last4(value: str) -> str:
    """화면에 보여 줄 끝 4자리. 짧은 키는 다 보이지 않게 줄인다."""
    value = value.strip()
    if len(value) < 12:
        return value[-2:] if len(value) >= 8 else ""
    return value[-4:]
