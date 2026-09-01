from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from careerops_storage.s3 import S3JsonStore, S3Settings


class AsyncBody:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def read(self) -> bytes:
        return self.body


class FakePaginator:
    def __init__(self, client: FakeS3Client) -> None:
        self.client = client

    async def paginate(self, *, Bucket: str, Prefix: str):
        self.client.list_calls.append((Bucket, Prefix))
        yield {
            "Contents": [
                {
                    "Key": (
                        "_lab/hh/batches/date=2026-08-30/"
                        "run_id=abc/run.json"
                    )
                },
                {
                    "Key": (
                        "_lab/hh/batches/date=2026-08-30/"
                        "run_id=abc/summary.json"
                    )
                },
            ]
        }
        yield {
            "Contents": [
                {
                    "Key": (
                        "_lab/hh/batches/date=2026-08-31/"
                        "run_id=def/run.json"
                    )
                }
            ]
        }


class FakeS3Client:
    def __init__(self) -> None:
        self.list_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.put_calls: list[dict[str, Any]] = []
        self.response: dict[str, Any] | None = None

    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "list_objects_v2"
        return FakePaginator(self)

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.get_calls.append((Bucket, Key))
        assert self.response is not None
        return self.response

    async def put_object(self, **kwargs: Any) -> None:
        self.put_calls.append(kwargs)


@pytest.fixture
def settings() -> S3Settings:
    return S3Settings(
        endpoint_url="http://localhost:8333",
        access_key="test",
        secret_key="test",
        bucket="careerops-raw",
        prefix="_lab/hh",
    )


@pytest.mark.asyncio
async def test_iter_keys_uses_pagination_and_returns_relative_keys(
    settings: S3Settings,
) -> None:
    client = FakeS3Client()
    store = S3JsonStore(settings, client=client)

    keys = [key async for key in store.iter_keys("batches")]

    assert client.list_calls == [("careerops-raw", "_lab/hh/batches")]
    assert keys == [
        "batches/date=2026-08-30/run_id=abc/run.json",
        "batches/date=2026-08-30/run_id=abc/summary.json",
        "batches/date=2026-08-31/run_id=def/run.json",
    ]


@pytest.mark.asyncio
async def test_full_prefix_is_not_duplicated(settings: S3Settings) -> None:
    client = FakeS3Client()
    store = S3JsonStore(settings, client=client)

    _ = [key async for key in store.iter_keys("_lab/hh/batches")]

    assert client.list_calls == [("careerops-raw", "_lab/hh/batches")]
    assert store.relative_key("_lab/hh/batches/run.json") == "batches/run.json"
    assert (
        store.relative_key("s3://careerops-raw/_lab/hh/batches/run.json")
        == "batches/run.json"
    )


@pytest.mark.asyncio
async def test_get_json_with_metadata_prefers_collected_at_over_last_modified(
    settings: S3Settings,
) -> None:
    client = FakeS3Client()
    body = json.dumps({"id": "1"}).encode()
    digest = hashlib.sha256(body).hexdigest()
    client.response = {
        "Body": AsyncBody(body),
        "Metadata": {
            "sha256": digest,
            "collected_at": "2026-08-30T14:00:00+03:00",
        },
        "LastModified": datetime(
            2026,
            8,
            30,
            13,
            0,
            tzinfo=timezone(timedelta(hours=3)),
        ),
    }
    store = S3JsonStore(settings, client=client)

    payload, ref = await store.get_json_with_metadata(
        "s3://careerops-raw/_lab/hh/batches/run.json"
    )

    assert payload == {"id": "1"}
    assert client.get_calls == [("careerops-raw", "_lab/hh/batches/run.json")]
    assert ref.uri == "s3://careerops-raw/_lab/hh/batches/run.json"
    assert ref.sha256 == digest
    assert ref.size_bytes == len(body)
    assert ref.collected_at == datetime(2026, 8, 30, 11, 0, tzinfo=UTC)
    assert ref.last_modified == datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_get_json_uses_last_modified_for_legacy_metadata(
    settings: S3Settings,
) -> None:
    client = FakeS3Client()
    body = b'{"id":"legacy"}'
    client.response = {
        "Body": AsyncBody(body),
        "Metadata": {"sha256": hashlib.sha256(body).hexdigest()},
        "LastModified": datetime(2026, 8, 30, 10, 0, tzinfo=UTC),
    }
    store = S3JsonStore(settings, client=client)

    payload, ref = await store.get_json_with_metadata("batches/legacy.json")

    assert payload == {"id": "legacy"}
    assert ref.collected_at is None
    assert ref.last_modified == datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_put_json_writes_hash_metadata_and_correct_full_key(
    settings: S3Settings,
) -> None:
    client = FakeS3Client()
    store = S3JsonStore(settings, client=client)

    payload = {"id": "1", "name": "ML Engineer"}
    observed_at = datetime(
        2026,
        8,
        30,
        14,
        30,
        tzinfo=timezone(timedelta(hours=3)),
    )
    ref = await store.put_json(
        "batches/run.json",
        payload,
        collected_at=observed_at,
    )

    call = client.put_calls[0]
    assert call["Bucket"] == "careerops-raw"
    assert call["Key"] == "_lab/hh/batches/run.json"
    assert json.loads(call["Body"].decode("utf-8")) == payload
    assert "collected_at" not in json.loads(call["Body"].decode("utf-8"))
    assert call["Metadata"]["sha256"] == hashlib.sha256(call["Body"]).hexdigest()
    assert call["Metadata"]["collected_at"] == "2026-08-30T11:30:00+00:00"
    assert ref.uri == "s3://careerops-raw/_lab/hh/batches/run.json"
    assert ref.collected_at == datetime(2026, 8, 30, 11, 30, tzinfo=UTC)
    assert ref.last_modified is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata_value",
    ["not-a-timestamp", "2026-08-30T11:30:00"],
)
async def test_rejects_invalid_collected_at_metadata(
    settings: S3Settings,
    metadata_value: str,
) -> None:
    client = FakeS3Client()
    body = b"{}"
    client.response = {
        "Body": AsyncBody(body),
        "Metadata": {
            "sha256": hashlib.sha256(body).hexdigest(),
            "collected_at": metadata_value,
        },
        "LastModified": datetime.now(UTC),
    }
    store = S3JsonStore(settings, client=client)

    with pytest.raises(ValueError, match="collected_at metadata"):
        await store.get_json_with_metadata("batches/run.json")


@pytest.mark.asyncio
async def test_rejects_bad_sha256_metadata(settings: S3Settings) -> None:
    client = FakeS3Client()
    client.response = {
        "Body": AsyncBody(b"{}"),
        "Metadata": {"sha256": "0" * 64},
        "LastModified": datetime.now(UTC),
    }
    store = S3JsonStore(settings, client=client)

    with pytest.raises(ValueError, match="sha256 metadata mismatch"):
        await store.get_json_with_metadata("batches/run.json")


def test_rejects_uri_outside_bucket_or_prefix(settings: S3Settings) -> None:
    store = S3JsonStore(settings, client=FakeS3Client())

    with pytest.raises(ValueError, match="bucket does not match"):
        store.relative_key("s3://other/_lab/hh/batches/run.json")
    with pytest.raises(ValueError, match="outside configured prefix"):
        store.relative_key("s3://careerops-raw/other/run.json")
