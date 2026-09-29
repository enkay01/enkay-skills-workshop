"""Own transient images for one controller run."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path


class ImageStore:
    def __init__(self, root: Path | None = None, *, stale_after_seconds: int = 86400):
        self.root = root or Path(tempfile.gettempdir()) / "codex-desktop-session"
        if self.root.is_symlink():
            raise ValueError("image-store root must not be a symbolic link")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_uid != os.getuid():
            raise PermissionError("image-store root is not owned by this user")
        os.chmod(self.root, 0o700)
        self._sweep(stale_after_seconds)
        self.run = Path(tempfile.mkdtemp(prefix=f"run-{os.getpid()}-", dir=self.root))
        os.chmod(self.run, 0o700)
        self._pending: Path | None = None

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def _sweep(self, age: int) -> None:
        now = time.time()
        for path in self.root.glob("run-*"):
            if not path.is_dir() or path.is_symlink():
                continue
            parts = path.name.split("-", 2)
            pid = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None
            if pid is not None and self._alive(pid):
                continue
            if pid is not None or now - path.stat().st_mtime > age:
                shutil.rmtree(path)

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def offer(self, image: bytes) -> Path:
        self.ack()
        fd, name = tempfile.mkstemp(prefix="inspection-", suffix=".png", dir=self.run)
        with os.fdopen(fd, "wb") as output:
            output.write(image)
        self._pending = Path(name)
        return self._pending

    def ack(self) -> None:
        if self._pending:
            self._pending.unlink(missing_ok=True)
            self._pending = None

    def close(self) -> None:
        self.ack()
        shutil.rmtree(self.run, ignore_errors=True)

    def __enter__(self) -> "ImageStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
