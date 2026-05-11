"""Object storage provider — local filesystem.

Public surface:

    class StorageClient:
        def upload(bucket: str, key: str, data: bytes, content_type: str | None) -> str
        def download(bucket: str, key: str) -> bytes
        def delete(bucket: str, key: str) -> None
        def signed_url(bucket: str, key: str, expires_in: int) -> str
        def exists(bucket: str, key: str) -> bool

Layout on disk: `<root>/<bucket>/<key>`. Default root is `/local-storage`.

Path-traversal protection: keys are rejected if they are absolute or contain
`..` components. The real cloud-storage equivalent enforces key shape
differently; for a local-filesystem backing we have to be sure a malicious
key cannot escape the storage root.

Side effects: every write/delete hits the filesystem.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

# Resolution order: ``STORAGE_ROOT`` env var (tests + local dev), then the
# Docker bind-mount default.
LOCAL_STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/local-storage"))


class StorageKeyError(ValueError):
    """Raised when a key would escape the bucket root or is otherwise invalid."""


def _validate_key(key: str) -> None:
    """Raise StorageKeyError if the key is empty, absolute, or escape-prone."""
    if not key:
        raise StorageKeyError("Key must be non-empty")
    if os.path.isabs(key):
        raise StorageKeyError(f"Key {key!r} must not be absolute")
    parts = PurePosixPath(key).parts
    if any(p == ".." for p in parts):
        raise StorageKeyError(f"Key {key!r} must not contain '..'")


class StorageClient:
    """Local filesystem object store.

    Mirrors the public surface of a typical cloud object-storage client so
    higher layers can remain cloud-agnostic. Accepts **kwargs and ignores
    them so construction is drop-in simple.
    """

    def __init__(self, root: Path | str | None = None, **_: Any) -> None:
        # Look up LOCAL_STORAGE_ROOT at call time (not as a default arg),
        # so `monkeypatch.setattr("providers.storage.LOCAL_STORAGE_ROOT", ...)`
        # in tests actually takes effect.
        self.root = Path(root) if root is not None else LOCAL_STORAGE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        _validate_key(key)
        return self.root / bucket / key

    def upload(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        """Write `data` at `<root>/<bucket>/<key>`. Returns a local-storage URI."""
        p = self._path(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if content_type:
            p.with_suffix(p.suffix + ".meta").write_text(
                f"content-type: {content_type}\n"
            )
        return f"local-storage://{bucket}/{key}"

    def download(self, bucket: str, key: str) -> bytes:
        """Read and return the bytes at `<root>/<bucket>/<key>`."""
        return self._path(bucket, key).read_bytes()

    def delete(self, bucket: str, key: str) -> None:
        """Remove the object if it exists; no-op if missing."""
        p = self._path(bucket, key)
        if p.exists():
            p.unlink()

    def signed_url(
        self,
        bucket: str,
        key: str,
        expires_in: int = 3600,
    ) -> str:
        """Return a URL the app can use to serve the object directly.

        `expires_in` is echoed as a query param so callers relying on it
        (logs, tests) see the value they passed.
        """
        return f"/local-storage/{quote(bucket)}/{quote(key)}?expires_in={expires_in}"

    def exists(self, bucket: str, key: str) -> bool:
        """Return True if the object exists at the key."""
        return self._path(bucket, key).exists()
