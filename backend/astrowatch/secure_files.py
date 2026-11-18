"""비밀 파일(Google 토큰 등) 안전 저장 · 읽기 전 점검 (NF3001).

- write_private: 같은 디렉터리에 0o600 임시 파일을 O_EXCL 로 만들어 쓰고 os.replace 로 원자 교체한다.
  대상이 심볼릭 링크면 거부한다(링크를 따라가 다른 파일을 덮어쓰는 공격 방지).
- check_private: 읽기 전에 호출한다. 심볼릭 링크면 사용 거부(AuthError),
  POSIX 에서 그룹/타인 권한이 있으면 경고 후 0o600 으로 조정한다.
Windows 에선 chmod 가 의미 없으므로 권한 조정은 건너뛰되 심볼릭 링크 거부는 유지한다.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

from astrowatch.clients.http import AuthError

logger = logging.getLogger("astrowatch.secure_files")

IS_POSIX = os.name == "posix"


def write_private(path: str | Path, text: str) -> None:
    target = Path(path)
    if target.is_symlink():
        raise OSError(f"심볼릭 링크에는 쓰지 않음: {target.name}")
    parent = target.parent
    if not parent.exists():
        if IS_POSIX:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".{target.name}.{secrets.token_hex(6)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        if IS_POSIX:
            os.chmod(tmp, 0o600)  # umask 와 무관하게
        if target.is_symlink():  # 쓰는 사이 바뀐 경우
            raise OSError(f"심볼릭 링크에는 쓰지 않음: {target.name}")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_private(path: str | Path) -> None:
    target = Path(path)
    if target.is_symlink():
        raise AuthError("비밀 파일이 심볼릭 링크여서 사용하지 않음")
    if not IS_POSIX or not target.exists():
        return
    mode = stat.S_IMODE(target.stat().st_mode)
    if mode & 0o077:
        logger.warning("비밀 파일 권한이 넓음(%o) — 0600 으로 조정: %s", mode, target.name)
        try:
            os.chmod(target, 0o600)
        except OSError:
            logger.warning("비밀 파일 권한 조정 실패: %s", target.name)
