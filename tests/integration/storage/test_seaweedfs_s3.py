from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import aioboto3
import pytest
import pytest_asyncio
from botocore.config import Config

from careerops_adapter.hh.raw import (
    HHRawContext,
    HHRawPublisher,
    RawObjectCollisionError,
)
from careerops_application.infrastructure.audit import S3ApplicationAuditStore
from careerops_processing.contracts.artifacts import ProcessingArtifactKind
from careerops_processing.infrastructure.artifacts import (
    ProcessingArtifactStore,
    ProcessingArtifactStoreSettings,
)
from careerops_storage.s3 import S3JsonStore, S3Settings

pytestmark = pytest.mark.integration_storage

ENDPOINT_ENV = "CAREEROPS_TEST_S3_ENDPOINT"
ACCESS_KEY = os.getenv("CAREEROPS_TEST_S3_ACCESS_KEY", "careerops-ci")
SECRET_KEY = os.getenv("CAREEROPS_TEST_S3_SECRET_KEY", "careerops-ci-secret")
RAW_BUCKET = "careerops-raw-ci"
ARTIFACTS_BUCKET = "careerops-artifacts-ci"


def _endpoint() -> str:
    value = os.getenv(ENDPOINT_ENV, "").strip()
    if not value:
        pytest.skip(f"{ENDPOINT_ENV} is not configured")
    return value


def _config() -> Config:
    return Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        retries={"max_attempts": 3, "mode": "standard"},
    )


@pytest_asyncio.fixture(autouse=True)
async def _clean_buckets() -> AsyncIterator[None]:
    endpoint = _endpoint()
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
        config=_config(),
    ) as client:
        for bucket in (RAW_BUCKET, ARTIFACTS_BUCKET):
            try:
                await client.create_bucket(Bucket=bucket)
            except client.exceptions.BucketAlreadyOwnedByYou:
                pass
            listed = await client.list_objects_v2(Bucket=bucket)
            contents = listed.get("Contents", [])
            if contents:
                await client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": item["Key"]} for item in contents]},
                )
    yield


@pytest.mark.asyncio
async def test_raw_observation_is_idempotent_and_collision_safe() -> None:
    settings = S3Settings(
        endpoint_url=_endpoint(),
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=RAW_BUCKET,
        prefix="hh",
    )
    observed_at = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    context = HHRawContext(
        account_key="primary",
        profile_key="resume-main",
        observed_at=observed_at,
        observation_id=uuid4(),
    )

    async with S3JsonStore(settings) as store:
        publisher = HHRawPublisher(store)
        first = await publisher.publish_vacancy(
            context=context,
            vacancy_id="123",
            payload={"id": "123", "name": "Data Engineer"},
        )
        repeated = await publisher.publish_vacancy(
            context=context,
            vacancy_id="123",
            payload={"id": "123", "name": "Data Engineer"},
        )
        assert repeated.ref.uri == first.ref.uri
        assert repeated.ref.sha256 == first.ref.sha256

        with pytest.raises(RawObjectCollisionError):
            await publisher.publish_vacancy(
                context=context,
                vacancy_id="123",
                payload={"id": "123", "name": "Different content"},
            )

        persisted, persisted_ref = await store.get_json_with_metadata(first.ref.uri)
        assert persisted == {"id": "123", "name": "Data Engineer"}
        assert persisted_ref.sha256 == first.ref.sha256
        head = await store.head(first.ref.uri)
        assert head["Metadata"]["sha256"] == first.ref.sha256
        assert head["Metadata"]["producer"] == "careerops"


@pytest.mark.asyncio
async def test_processing_artifact_round_trips_by_content_address() -> None:
    settings = ProcessingArtifactStoreSettings(
        endpoint_url=_endpoint(),
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=ARTIFACTS_BUCKET,
        prefix="processing-ci",
    )
    payload = {
        "schema_version": "careerops.test.v1",
        "input_fingerprint": "a" * 64,
        "decision": "review",
    }

    async with ProcessingArtifactStore(settings) as store:
        first = await store.put_contract(
            kind=ProcessingArtifactKind.MATCH_DECISION,
            schema_version="careerops.test.v1",
            payload=payload,
        )
        second = await store.put_contract(
            kind=ProcessingArtifactKind.MATCH_DECISION,
            schema_version="careerops.test.v1",
            payload=payload,
        )
        assert second == first
        assert await store.get_contract_json(first) == payload


@pytest.mark.asyncio
async def test_application_audit_writes_distinct_persistent_events() -> None:
    settings = S3Settings(
        endpoint_url=_endpoint(),
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=ARTIFACTS_BUCKET,
        prefix="application-owner-ci",
    )
    audit = S3ApplicationAuditStore(settings)
    application_id = uuid4()

    first_uri = await audit.write_event(
        application_id=application_id,
        event="precheck",
        payload={"state": "ready"},
    )
    second_uri = await audit.write_event(
        application_id=application_id,
        event="submit",
        payload={"state": "submitted"},
    )
    assert first_uri != second_uri

    async with S3JsonStore(settings) as store:
        first = await store.get_json(first_uri)
        second = await store.get_json(second_uri)
    assert first["application_id"] == str(application_id)
    assert first["event"] == "precheck"
    assert second["event"] == "submit"
