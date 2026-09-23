from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Iterable, Iterator

import normalizer as core


def raw_storage_prefix() -> str:
    """Physical S3 prefix used by the HH adapter's S3JsonStore."""

    return os.environ.get("CAREEROPS_NORMALIZER_RAW_STORAGE_PREFIX", "_lab/hh").strip("/")


def logical_raw_key(physical_key: str, *, storage_prefix: str) -> str:
    prefix = storage_prefix.strip("/")
    if not prefix:
        logical = physical_key.strip("/")
    else:
        expected = f"{prefix}/"
        if not physical_key.startswith(expected):
            raise ValueError(
                f"RAW key {physical_key!r} is outside configured storage prefix {prefix!r}"
            )
        logical = physical_key[len(expected) :]
    if not logical.startswith("v2/"):
        raise ValueError(f"unsupported CareerOPS RAW logical key: {logical!r}")
    return logical


def list_raw_entity_keys(settings: core.Settings, *, storage_prefix: str) -> list[str]:
    client = core._s3_client(settings)
    keys: list[str] = []
    prefix_root = storage_prefix.strip("/")
    for kind in ("vacancy", "resume"):
        logical_prefix = f"{settings.raw_prefix}/{kind}/"
        physical_prefix = (
            f"{prefix_root}/{logical_prefix}" if prefix_root else logical_prefix
        )
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.raw_bucket, Prefix=physical_prefix):
            for item in page.get("Contents", []):
                physical_key = str(item["Key"])
                logical_key = logical_raw_key(
                    physical_key,
                    storage_prefix=storage_prefix,
                )
                if core._RAW_KEY_RE.fullmatch(logical_key):
                    keys.append(physical_key)
    return sorted(keys)


def normalize_physical_raw_object(
    physical_key: str,
    body: bytes,
    metadata: dict[str, Any],
    *,
    raw_bucket: str,
    storage_prefix: str,
) -> core.NormalizedResult:
    logical_key = logical_raw_key(physical_key, storage_prefix=storage_prefix)
    identity = core.parse_raw_identity(logical_key)
    raw_sha256 = core.sha256_bytes(body)
    stored_sha = str(metadata.get("sha256", "")).lower()
    if stored_sha and stored_sha != raw_sha256:
        raise ValueError(f"RAW sha256 metadata mismatch: s3://{raw_bucket}/{physical_key}")

    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("full HH entity RAW payload must be a JSON object")
    raw_uri = f"s3://{raw_bucket}/{physical_key}"
    if identity.kind == "vacancy":
        normalized, projection = core.normalize_vacancy(
            payload,
            identity=identity,
            raw_uri=raw_uri,
            raw_sha256=raw_sha256,
        )
    else:
        normalized, projection = core.normalize_resume(
            payload,
            identity=identity,
            raw_uri=raw_uri,
            raw_sha256=raw_sha256,
        )

    normalized_body = core.canonical_json_bytes(normalized)
    normalized_sha = core.sha256_bytes(normalized_body)
    normalized_key = (
        f"normalized/{identity.kind}/source=hh/entity={identity.source_entity_id}/"
        f"raw_sha256={raw_sha256}/{core.NORMALIZATION_VERSION}.json"
    )
    return core.NormalizedResult(
        kind=identity.kind,
        account_key=identity.account_key,
        profile_key=identity.profile_key,
        source_entity_id=identity.source_entity_id,
        raw_key=physical_key,
        raw_sha256=raw_sha256,
        observed_at=identity.observed_at,
        normalized_key=normalized_key,
        normalized_sha256=normalized_sha,
        semantic_content_hash=normalized["semantic_content_hash"],
        materialization_key=normalized["materialization_key"],
        dq_status=normalized["dq"]["status"],
        processing_ready=normalized["dq"]["status"] != "blocked",
        payload_json=normalized_body.decode("utf-8"),
        db_projection_json=json.dumps(projection, ensure_ascii=False, default=str),
    )


def normalize_partition(
    keys: Iterable[str],
    settings_dict: dict[str, Any],
    storage_prefix: str,
) -> Iterator[core.NormalizedResult]:
    settings = core.Settings(**settings_dict)
    client = core._s3_client(settings)
    for physical_key in keys:
        response = client.get_object(Bucket=settings.raw_bucket, Key=physical_key)
        body = response["Body"].read()
        metadata = response.get("Metadata") or {}
        yield normalize_physical_raw_object(
            physical_key,
            body,
            metadata,
            raw_bucket=settings.raw_bucket,
            storage_prefix=storage_prefix,
        )


def main() -> int:
    from pyspark.sql import SparkSession

    args = core.parse_args()
    settings = core.Settings.from_env()
    if args.no_postgres:
        settings = core.Settings(**{**settings.__dict__, "postgres_dsn": None})
    storage_prefix = raw_storage_prefix()
    keys = list_raw_entity_keys(settings, storage_prefix=storage_prefix)
    if args.limit > 0:
        keys = keys[: args.limit]
    if not keys:
        print("no immutable HH vacancy/resume RAW objects found")
        return 0

    spark = SparkSession.builder.appName("careerops-normalizer-spark").getOrCreate()
    settings_dict = settings.__dict__.copy()
    rdd = spark.sparkContext.parallelize(keys, min(settings.partitions, len(keys)))
    normalized = rdd.mapPartitions(
        lambda part: normalize_partition(part, settings_dict, storage_prefix)
    )

    manifest_rows: list[dict[str, Any]] = []
    try:
        for result in normalized.toLocalIterator():
            normalized_uri = core.put_normalized(settings, result)
            core.publish_postgres(settings, result, normalized_uri)
            manifest_rows.append(
                {
                    "entity_type": result.kind,
                    "source_key": "hh",
                    "source_entity_id": result.source_entity_id,
                    "account_key": result.account_key if result.kind == "resume" else None,
                    "raw_uri": f"s3://{settings.raw_bucket}/{result.raw_key}",
                    "raw_sha256": result.raw_sha256,
                    "normalized_uri": normalized_uri,
                    "normalized_sha256": result.normalized_sha256,
                    "semantic_content_hash": result.semantic_content_hash,
                    "materialization_key": result.materialization_key,
                    "processing_ready": result.processing_ready,
                    "dq_status": result.dq_status,
                }
            )
    finally:
        spark.stop()

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    manifest_uri = core.write_manifest(settings, manifest_rows, run_id)
    print(
        json.dumps(
            {"processed": len(manifest_rows), "manifest_uri": manifest_uri},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
