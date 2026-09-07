from io import BytesIO

import pytest

from app.services.storage import store_stream


class BoundedStream(BytesIO):
    def read(self, size=-1):
        assert 0 < size <= 1024 * 1024, "Must not reserve the 2 GiB upload limit in memory"
        return super().read(size)


def test_stream_upload_accepts_exact_limit_and_preserves_all_bytes(tmp_path):
    content = b"synthetic" * 140_000
    path = store_stream(stream=BoundedStream(content), directory=tmp_path, max_bytes=len(content), suffix=".zip")
    assert path.read_bytes() == content
    assert path.parent == tmp_path


def test_oversized_stream_is_rejected_without_truncation_or_partial_file(tmp_path):
    with pytest.raises(ValueError, match="超过大小限制"):
        store_stream(stream=BoundedStream(b"a" * (1024 * 1024 + 1)), directory=tmp_path, max_bytes=1024 * 1024)
    assert list(tmp_path.iterdir()) == []


def test_interrupted_stream_removes_partial_upload(tmp_path):
    class InterruptedStream(BoundedStream):
        def read(self, size=-1):
            if self.tell():
                raise OSError("synthetic interrupted upload")
            return super().read(size)
    with pytest.raises(OSError, match="interrupted upload"):
        store_stream(stream=InterruptedStream(b"x"), directory=tmp_path, max_bytes=10)
    assert list(tmp_path.iterdir()) == []
