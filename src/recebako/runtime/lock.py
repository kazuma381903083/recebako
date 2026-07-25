from __future__ import annotations

import fcntl
import os
from types import TracebackType
from typing import Self

from recebako.runtime.layout import RuntimePaths


class InboxLockError(RuntimeError):
    """inbox処理の排他ロックを取得できなかったことを表す。"""


class InboxLock:
    def __init__(self, paths: RuntimePaths) -> None:
        self._paths = paths
        self._descriptor: int | None = None

    def __enter__(self) -> Self:
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._paths.lock_file, flags, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise InboxLockError("inbox処理はすでに実行中です") from exc
        except OSError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise InboxLockError("inbox排他ロックを取得できません") from exc
        self._descriptor = descriptor
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._descriptor is not None:
            try:
                fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self._descriptor)
                self._descriptor = None
