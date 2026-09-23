from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
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


def parquet_batch_fingerprint(results: Iterable[core.NormalizedResult]) -> str:
    identity = [
        {
            "entity_type": result.kind,
            "source_entity_id": result.source_entity_id,
            "normalized_sha256": result.normalized_sha256,
        }
        for result in results
    ]
    identity.sort(
        key=lambda row: (
            str(row["entity_type"]),
            str(row["source_entity_id"]),
            str(row["normalized_sha256"]),
        )
    )
    return core.sha256_bytes(
        core.canonical_json_bytes(
            {
                "normalization_version": core.NORMALIZATION_VERSION,
                "objects": identity,
            }
        )
    )


def parquet_rows(results: Iterable[core.NormalizedResult]) -> list[dict[str, Any]]:
    return [
        {
            "entity_type": result.kind,
            "source_entity_id": result.source_entity_id,
            "account_key": result.account_key if result.kind == "resume" else None,
            "profile_key": result.profile_key if result.kind == "resume" else None,
            "observed_at": result.observed_at,
            "raw_sha256": result.raw_sha256,
            "normalized_sha256": result.normalized_sha256,
            "semantic_content_hash": result.semantic_content_hash,
            "materialization_key": result.materialization_key,
            "dq_status": result.dq_status,
            "processing_ready": result.processing_ready,
            "payload_json": result.payload_json,
        }
        for result in results
    ]


def _is_missing_s3_object(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def _existing_parquet_manifest(
    client: Any,
    *,
    bucket: str,
    key: str,
    batch_sha256: str,
) -> str | None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _is_missing_s3_object(exc):
            return None
        raise
    payload = json.loads(response["Body"].read().decode("utf-8"))
    if payload.get("batch_sha256") != batch_sha256:
        raise RuntimeError(f"Parquet manifest collision at s3://{bucket}/{key}")
    return f"s3://{bucket}/{key}"


def publish_parquet(
    spark: Any,
    settings: core.Settings,
    results: Iterable[core.NormalizedResult],
) -> list[str]:
    """Publish immutable batch Parquet snapshots without requiring Spark S3A jars.

    Spark writes Parquet to the container's /tmp tmpfs. The driver then uploads the
    generated part files through the same S3-compatible boto3 client used for JSON.
    A content-addressed manifest makes replay of the exact same normalized input set
    idempotent: a second run reuses the existing Parquet snapshot instead of creating
    another copy.
    """

    from pyspark.sql.types import BooleanType, StringType, StructField, StructType

    grouped: dict[str, list[core.NormalizedResult]] = defaultdict(list)
    for result in results:
        grouped[result.kind].append(result)

    schema = StructType(
        [
            StructField("entity_type", StringType(), nullable=False),
            StructField("source_entity_id", StringType(), nullable=False),
            StructField("account_key", StringType(), nullable=True),
            StructField("profile_key", StringType(), nullable=True),
            StructField("observed_at", StringType(), nullable=False),
            StructField("raw_sha256", StringType(), nullable=False),
            StructField("normalized_sha256", StringType(), nullable=False),
            StructField("semantic_content_hash", StringType(), nullable=False),
            StructField("materialization_key", StringType(), nullable=False),
            StructField("dq_status", StringType(), nullable=False),
            StructField("processing_ready", BooleanType(), nullable=False),
            StructField("payload_json", StringType(), nullable=False),
        ]
    )

    client = core._s3_client(settings)
    manifest_uris: list[str] = []
    for kind in sorted(grouped):
        kind_results = grouped[kind]
        batch_sha256 = parquet_batch_fingerprint(kind_results)
        prefix = (
            f"silver/{kind}/normalization_version={core.NORMALIZATION_VERSION}/"
            f"batch_sha256={batch_sha256}"
        )
        manifest_key = f"{prefix}/_manifest.json"
        existing_uri = _existing_parquet_manifest(
            client,
            bucket=settings.lake_bucket,
            key=manifest_key,
            batch_sha256=batch_sha256,
        )
        if existing_uri is not None:
            manifest_uris.append(existing_uri)
            continue

        rows = parquet_rows(kind_results)
        with tempfile.TemporaryDirectory(prefix="careerops-normalizer-parquet-") as temp_dir:
            output_dir = Path(temp_dir) / kind
            frame = spark.createDataFrame(rows, schema=schema)
            part_count = max(1, min(settings.partitions, len(rows)))
            frame.coalesce(part_count).write.mode("overwrite").parquet(str(output_dir))

            parquet_objects: list[dict[str, Any]] = []
            for index, part in enumerate(sorted(output_dir.glob("part-*.parquet"))):
                body = part.read_bytes()
                part_sha256 = core.sha256_bytes(body)
                object_key = f"{prefix}/part-{index:05d}-{part_sha256}.parquet"
                client.put_object(
                    Bucket=settings.lake_bucket,
                    Key=object_key,
                    Body=body,
                    ContentType="application/vnd.apache.parquet",
                    Metadata={
                        "sha256": part_sha256,
                        "batch-sha256": batch_sha256,
                        "producer": "careerops-normalizer-spark",
                    },
                )
                parquet_objects.append(
                    {
                        "uri": f"s3://{settings.lake_bucket}/{object_key}",
                        "sha256": part_sha256,
                        "size_bytes": len(body),
                    }
                )

        manifest = {
            "schema_version": "careerops.normalized-parquet-manifest.v1",
            "normalization_version": core.NORMALIZATION_VERSION,
            "entity_type": kind,
            "batch_sha256": batch_sha256,
            "row_count": len(rows),
            "normalized_sha256": sorted(result.normalized_sha256 for result in kind_results),
            "objects": parquet_objects,
        }
        manifest_body = core.canonical_json_bytes(manifest)
        client.put_object(
            Bucket=settings.lake_bucket,
            Key=manifest_key,
            Body=manifest_body,
            ContentType="application/json; charset=utf-8",
            Metadata={
                "sha256": core.sha256_bytes(manifest_body),
                "batch-sha256": batch_sha256,
                "producer": "careerops-normalizer-spark",
            },
        )
        manifest_uris.append(f"s3://{settings.lake_bucket}/{manifest_key}")

    return manifest_uris


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
    materialized_results: list[core.NormalizedResult] = []
    parquet_manifests: list[str] = []
    try:
        for result in normalized.toLocalIterator():
            normalized_uri = core.put_normalized(settings, result)
            core.publish_postgres(settings, result, normalized_uri)
            materialized_results.append(result)
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
        parquet_manifests = publish_parquet(spark, settings, materialized_results)
    finally:
        spark.stop()

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    manifest_uri = core.write_manifest(settings, manifest_rows, run_id)
    print(
        json.dumps(
            {
                "processed": len(manifest_rows),
                "manifest_uri": manifest_uri,
                "parquet_manifests": parquet_manifests,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
