"""E2E tests for the storage provider — full round-trip against real filesystem."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest

from providers.storage import StorageClient, StorageKeyError


class TestUploadDownload:
    def test_upload_then_download_round_trips_bytes(self, storage_root) -> None:
        c = StorageClient()
        c.upload("bucket-a", "path/to/file.txt", b"hello world")
        assert c.download("bucket-a", "path/to/file.txt") == b"hello world"

    def test_upload_stores_under_bucket_and_key(self, storage_root) -> None:
        c = StorageClient()
        c.upload("b", "k", b"x")
        assert (storage_root / "b" / "k").read_bytes() == b"x"

    def test_content_type_written_as_sidecar(self, storage_root) -> None:
        c = StorageClient()
        c.upload("b", "file.pdf", b"%PDF-1.4", content_type="application/pdf")
        meta = (storage_root / "b" / "file.pdf.meta").read_text()
        assert "application/pdf" in meta

    def test_download_missing_raises(self, storage_root) -> None:
        c = StorageClient()
        with pytest.raises(FileNotFoundError):
            c.download("no-bucket", "no-key")


class TestExistsDelete:
    def test_exists_false_before_upload(self, storage_root) -> None:
        c = StorageClient()
        assert c.exists("b", "k") is False

    def test_exists_true_after_upload(self, storage_root) -> None:
        c = StorageClient()
        c.upload("b", "k", b"x")
        assert c.exists("b", "k") is True

    def test_delete_removes_file(self, storage_root) -> None:
        c = StorageClient()
        c.upload("b", "k", b"x")
        c.delete("b", "k")
        assert c.exists("b", "k") is False

    def test_delete_missing_is_no_op(self, storage_root) -> None:
        c = StorageClient()
        # delete of missing file is documented as no-op; this is not a silent
        # drop because there's no downstream contract that requires it to
        # have existed.
        c.delete("b", "k")


class TestSignedUrl:
    def test_signed_url_includes_expires_in(self, storage_root) -> None:
        c = StorageClient()
        url = c.signed_url("b", "k", expires_in=600)
        assert "expires_in=600" in url

    def test_signed_url_url_encodes_special_chars(self, storage_root) -> None:
        c = StorageClient()
        url = c.signed_url("b", "path with spaces", expires_in=60)
        assert "path%20with%20spaces" in url


class TestKeyValidation:
    """Path-traversal protection — a malicious key must never escape the bucket."""

    def test_empty_key_raises(self, storage_root) -> None:
        c = StorageClient()
        with pytest.raises(StorageKeyError):
            c.upload("b", "", b"x")

    def test_absolute_key_raises(self, storage_root) -> None:
        c = StorageClient()
        with pytest.raises(StorageKeyError):
            c.upload("b", "/etc/passwd", b"x")

    def test_parent_dir_traversal_raises(self, storage_root) -> None:
        c = StorageClient()
        with pytest.raises(StorageKeyError):
            c.upload("b", "../../etc/passwd", b"x")

    def test_embedded_parent_raises(self, storage_root) -> None:
        c = StorageClient()
        with pytest.raises(StorageKeyError):
            c.upload("b", "valid/../../escape", b"x")

    def test_validation_also_runs_on_download(self, storage_root) -> None:
        c = StorageClient()
        with pytest.raises(StorageKeyError):
            c.download("b", "../escape")
