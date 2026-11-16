"""내보내기 묶음 암호화 (age 형식, https://age-encryption.org/v1).

- 수신자(공개 키 ``age1…``)로 암호화하고, 신원 파일(``AGE-SECRET-KEY-1…``)로 푼다. 비밀번호 방식은 두지 않는다
  (명령행·환경변수에 비밀번호가 남기 쉽다).
- 구현은 둘 중 하나: ``age`` 명령이 PATH에 있으면 그것(파일을 흘려 보낸다), 없으면 ``pyrage``
  (선택 설치 ``career-core[age]``). ``CAREER_AGE_BACKEND=binary|pyrage``로 고를 수 있다.
- 키는 이 모듈이 만들거나 저장하지 않는다. ``age-keygen -o key.txt``로 만들어 서버 밖에 둔다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

AGE_MAGIC = b"age-encryption.org/v1"
AGE_ARMOR = b"-----BEGIN AGE ENCRYPTED FILE-----"
_RECIPIENT = re.compile(r"age1[02-9ac-hj-np-z]{58}")
_IDENTITY = re.compile(r"AGE-SECRET-KEY-1[02-9AC-HJ-NP-Z]{58}")


class AgeError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def is_encrypted(path: str | os.PathLike[str]) -> bool:
    with open(path, "rb") as f:
        head = f.read(len(AGE_ARMOR))
    return head.startswith((AGE_MAGIC, AGE_ARMOR))


def check_recipients(recipients: Sequence[str]) -> list[str]:
    out = [r.strip() for r in recipients if r and r.strip()]
    if not out:
        raise AgeError("no_recipients")
    for r in out:
        if not _RECIPIENT.fullmatch(r):
            raise AgeError("invalid_recipient", r[:16])
    return out


def _backend() -> str:
    choice = os.environ.get("CAREER_AGE_BACKEND", "").strip().lower()
    if choice in ("binary", "pyrage"):
        return choice
    return "binary" if shutil.which("age") else "pyrage"


def _pyrage():
    try:
        import pyrage
    except ImportError as exc:
        raise AgeError(
            "age_unavailable", "install the `age` command or career-core[age] (pyrage)"
        ) from exc
    return pyrage


def _read_identities(paths: Sequence[str | os.PathLike[str]]) -> list[str]:
    keys: list[str] = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if not _IDENTITY.fullmatch(line):
                    raise AgeError("invalid_identity", str(p))
                keys.append(line)
    if not keys:
        raise AgeError("no_identities")
    return keys


def encrypt_file(
    src: str | os.PathLike[str], dst: str | os.PathLike[str], recipients: Sequence[str]
) -> str:
    """``src``를 ``recipients``로 암호화해 ``dst``에 쓴다. 쓴 구현 이름을 돌려준다."""
    recipients = check_recipients(recipients)
    backend = _backend()
    if backend == "binary":
        args = ["age", "--encrypt"]
        for r in recipients:
            args += ["-r", r]
        args += ["-o", str(dst), str(src)]
        done = subprocess.run(args, capture_output=True, check=False)
        if done.returncode != 0:
            raise AgeError("encrypt_failed", done.stderr.decode(errors="replace")[:200])
        return backend
    pyrage = _pyrage()
    parsed = [pyrage.x25519.Recipient.from_str(r) for r in recipients]
    try:
        pyrage.encrypt_file(str(src), str(dst), parsed)
    except Exception as exc:  # 라이브러리 예외를 사유 코드로
        raise AgeError("encrypt_failed", type(exc).__name__) from exc
    return backend


def decrypt_file(
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str],
    identity_files: Sequence[str | os.PathLike[str]],
) -> str:
    """``identity_files``(age 신원 파일)로 ``src``를 풀어 ``dst``에 쓴다."""
    if not identity_files:
        raise AgeError("no_identities")
    backend = _backend()
    if backend == "binary":
        args = ["age", "--decrypt"]
        for p in identity_files:
            args += ["-i", str(p)]
        args += ["-o", str(dst), str(src)]
        done = subprocess.run(args, capture_output=True, check=False)
        if done.returncode != 0:
            raise AgeError("decrypt_failed", done.stderr.decode(errors="replace")[:200])
        return backend
    pyrage = _pyrage()
    identities = [pyrage.x25519.Identity.from_str(k) for k in _read_identities(identity_files)]
    try:
        pyrage.decrypt_file(str(src), str(dst), identities)
    except Exception as exc:
        raise AgeError("decrypt_failed", type(exc).__name__) from exc
    return backend


__all__ = [
    "AGE_MAGIC",
    "AgeError",
    "check_recipients",
    "decrypt_file",
    "encrypt_file",
    "is_encrypted",
]
