from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterable, Iterator
from urllib.parse import unquote

NORMALIZATION_VERSION = "spark-normalizer-v1"
DICTIONARY_VERSION = "hh-source-v1"
VACANCY_SCHEMA_VERSION = "vacancy-v1"
RESUME_SCHEMA_VERSION = "resume-v1"

_RAW_KEY_RE = re.compile(
    r"^v2/(?P<kind>vacancy|resume)/date=[^/]+/account=(?P<account>[^/]+)/"
    r"profile=(?P<profile>[^/]+)/(?P<identity_kind>vacancy_id|resume_id)="
    r"(?P<entity>[^/]+)/observed_at=(?P<observed>[^/]+)/observation_id=[^/]+\.json$"
)


@dataclass(frozen=True)
class Settings:
    endpoint_url: str
    access_key: str
    secret_key: str
    region: str
    raw_bucket: str
    lake_bucket: str
    raw_prefix: str
    postgres_dsn: str | None
    partitions: int

    @classmethod
    def from_env(cls) -> "Settings":
        access_key = os.environ.get("CAREEROPS_S3_ACCESS_KEY")
        secret_key = os.environ.get("CAREEROPS_S3_SECRET_KEY")
        if not access_key or not secret_key:
            raise RuntimeError("CAREEROPS_S3_ACCESS_KEY and CAREEROPS_S3_SECRET_KEY are required")
        partitions = int(os.environ.get("CAREEROPS_NORMALIZER_PARTITIONS", "8"))
        if partitions <= 0:
            raise ValueError("CAREEROPS_NORMALIZER_PARTITIONS must be > 0")
        return cls(
            endpoint_url=os.environ.get("CAREEROPS_S3_ENDPOINT", "http://127.0.0.1:8333"),
            access_key=access_key,
            secret_key=secret_key,
            region=os.environ.get("CAREEROPS_S3_REGION", "us-east-1"),
            raw_bucket=os.environ.get("CAREEROPS_NORMALIZER_RAW_BUCKET", "careerops-raw"),
            lake_bucket=os.environ.get("CAREEROPS_NORMALIZER_LAKE_BUCKET", "careerops-lake"),
            raw_prefix=os.environ.get("CAREEROPS_NORMALIZER_RAW_PREFIX", "v2").strip("/"),
            postgres_dsn=os.environ.get("CAREEROPS_NORMALIZER_POSTGRES_DSN"),
            partitions=partitions,
        )


@dataclass(frozen=True)
class RawIdentity:
    kind: str
    account_key: str
    profile_key: str
    source_entity_id: str
    observed_at: str


@dataclass(frozen=True)
class NormalizedResult:
    kind: str
    account_key: str
    profile_key: str
    source_entity_id: str
    raw_key: str
    raw_sha256: str
    observed_at: str
    normalized_key: str
    normalized_sha256: str
    semantic_content_hash: str
    materialization_key: str
    dq_status: str
    processing_ready: bool
    payload_json: str
    db_projection_json: str


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_value(value: Any, *, present: bool = True) -> dict[str, Any]:
    if not present:
        return {"state": "not_provided", "value": None}
    if value is None:
        return {"state": "explicit_none", "value": None}
    return {"state": "known", "value": value}


def source_label(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str) and value.strip():
        text = value.strip()
        return {"key": text, "label": text, "source_code": text}
    if not isinstance(value, dict):
        return None
    code = value.get("id") or value.get("code") or value.get("name")
    if not isinstance(code, str) or not code.strip():
        return None
    label = value.get("name")
    return {
        "key": code.strip(),
        "label": label.strip() if isinstance(label, str) and label.strip() else None,
        "source_code": code.strip(),
    }


def labels(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [item for raw in items if (item := source_label(raw)) is not None]


def parse_raw_identity(key: str) -> RawIdentity:
    match = _RAW_KEY_RE.fullmatch(key)
    if match is None:
        raise ValueError(f"unsupported CareerOPS RAW key: {key}")
    observed = datetime.strptime(match.group("observed"), "%Y%m%dT%H%M%S.%fZ").replace(
        tzinfo=UTC
    )
    return RawIdentity(
        kind=match.group("kind"),
        account_key=unquote(match.group("account")),
        profile_key=unquote(match.group("profile")),
        source_entity_id=unquote(match.group("entity")),
        observed_at=observed.isoformat(),
    )


def _experience_range(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    code = value.get("id") if isinstance(value, dict) else value
    if not isinstance(code, str) or not code:
        return None
    mapping: dict[str, tuple[str | None, str | None]] = {
        "noExperience": ("0", "0"),
        "between1And3": ("1", "3"),
        "between3And6": ("3", "6"),
        "moreThan6": ("6", None),
    }
    minimum, maximum = mapping.get(code, (None, None))
    return {
        "source_code": code,
        "minimum_years": minimum,
        "maximum_years": maximum,
    }


def _location(payload: dict[str, Any]) -> dict[str, Any] | None:
    area = payload.get("area")
    address = payload.get("address")
    if not isinstance(area, dict) and not isinstance(address, dict):
        return None
    metro: list[str] = []
    if isinstance(address, dict):
        metro_value = address.get("metro") or address.get("metro_stations")
        metro_items = metro_value if isinstance(metro_value, list) else [metro_value]
        for item in metro_items:
            if isinstance(item, dict) and isinstance(item.get("station_name"), str):
                metro.append(item["station_name"])
            elif isinstance(item, str) and item.strip():
                metro.append(item.strip())
    address_text = None
    if isinstance(address, dict):
        raw = address.get("raw")
        if isinstance(raw, str) and raw.strip():
            address_text = raw.strip()
    return {
        "area_id": area.get("id") if isinstance(area, dict) else None,
        "area_name": area.get("name") if isinstance(area, dict) else None,
        "address": address_text,
        "metro": metro,
        "source_facts": [],
    }


def _salary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    currency = value.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        return None
    return {
        "amount_from": value.get("from"),
        "amount_to": value.get("to"),
        "currency": currency,
        "gross": value.get("gross") if isinstance(value.get("gross"), bool) else None,
    }


def _text_block(block_id: str, text: Any, source_path: str, ordinal: int) -> dict[str, Any] | None:
    if not isinstance(text, str) or not text.strip():
        return None
    clean = text.strip()
    quote = clean[:500]
    return {
        "block_id": block_id,
        "text": clean,
        "ordinal": ordinal,
        "heading": None,
        "section_hint": source_path,
        "source_ref": {
            "source_path": source_path,
            "locator": source_path,
            "quote": quote,
        },
    }


def _dq(*, warnings: list[str], blocked: bool = False) -> dict[str, Any]:
    return {
        "status": "blocked" if blocked else ("warning" if warnings else "clean"),
        "full_entity_available": True,
        "parse_complete": not blocked,
        "omitted_fields": [],
        "unknown_enums": [],
        "invalid_dates": [],
        "conflicting_fields": [],
        "unresolved_sections": [],
        "source_truncated": False,
        "normalization_warnings": warnings,
    }


def normalize_vacancy(
    payload: dict[str, Any],
    *,
    identity: RawIdentity,
    raw_uri: str,
    raw_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    warnings: list[str] = []
    payload_id = payload.get("id")
    if payload_id is not None and str(payload_id) != identity.source_entity_id:
        raise ValueError("vacancy id does not match immutable RAW key")

    description = _text_block("description", payload.get("description"), "description", 0)
    if description is None:
        warnings.append("vacancy.description_missing")

    employer_value = payload.get("employer")
    employer = None
    if isinstance(employer_value, dict):
        employer = {
            "source_employer_id": str(employer_value["id"])
            if employer_value.get("id") is not None
            else None,
            "name": employer_value.get("name"),
        }

    semantic = {
        "title": source_value(payload.get("name"), present="name" in payload),
        "employer": source_value(employer, present="employer" in payload),
        "professional_roles": labels(payload.get("professional_roles")),
        "key_skills": labels(payload.get("key_skills")),
        "experience": source_value(
            _experience_range(payload.get("experience")), present="experience" in payload
        ),
        "employment": labels(payload.get("employment")),
        "schedules": labels(payload.get("schedule") or payload.get("schedules")),
        "work_formats": labels(payload.get("work_format") or payload.get("work_formats")),
        "location": source_value(_location(payload), present="area" in payload or "address" in payload),
        "relocation_facts": [],
        "salary": source_value(_salary(payload.get("salary")), present="salary" in payload),
        "archived": source_value(payload.get("archived"), present="archived" in payload),
        "closed_for_applicants": source_value(
            payload.get("closed_for_applicants"), present="closed_for_applicants" in payload
        ),
        "published_at": source_value(
            payload.get("published_at"), present="published_at" in payload
        ),
        "text_blocks": [description] if description is not None else [],
    }
    semantic_hash = sha256_bytes(canonical_json_bytes(semantic))
    materialization_key = f"hh:vacancy:{identity.source_entity_id}:{raw_sha256}:{NORMALIZATION_VERSION}"
    dq = _dq(warnings=warnings)
    normalized = {
        "schema_version": VACANCY_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "dictionary_version": DICTIONARY_VERSION,
        "materialization_key": materialization_key,
        "source_key": "hh",
        "source_entity_id": identity.source_entity_id,
        "raw": {
            "raw_uri": raw_uri,
            "raw_sha256": raw_sha256,
            "observed_at": identity.observed_at,
        },
        "semantic_content_hash": semantic_hash,
        **semantic,
        "dq": dq,
    }
    projection = {
        "title": payload.get("name"),
        "description": payload.get("description"),
        "location": (_location(payload) or {}).get("area_name"),
        "remote": None,
        "employment_type": (source_label(payload.get("employment")) or {}).get("key"),
        "experience": (_experience_range(payload.get("experience")) or {}).get("source_code"),
        "skill_keys": [item["key"] for item in semantic["key_skills"]],
        "salary_from": (_salary(payload.get("salary")) or {}).get("amount_from"),
        "salary_to": (_salary(payload.get("salary")) or {}).get("amount_to"),
        "salary_currency": (_salary(payload.get("salary")) or {}).get("currency"),
        "archived": payload.get("archived") if isinstance(payload.get("archived"), bool) else None,
        "closed_for_applicants": payload.get("closed_for_applicants")
        if isinstance(payload.get("closed_for_applicants"), bool)
        else None,
        "published_at": payload.get("published_at"),
    }
    return normalized, projection


def _date_value(value: Any) -> dict[str, Any]:
    return source_value(value, present=value is not None)


def normalize_resume(
    payload: dict[str, Any],
    *,
    identity: RawIdentity,
    raw_uri: str,
    raw_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    warnings: list[str] = []
    payload_id = payload.get("id")
    if payload_id is not None and str(payload_id) != identity.source_entity_id:
        raise ValueError("resume id does not match immutable RAW key")

    experience_entries: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("experience") or []):
        if not isinstance(item, dict):
            warnings.append("resume.experience_item_unparsed")
            continue
        block = _text_block(
            f"experience-{index}-description",
            item.get("description"),
            f"experience[{index}].description",
            0,
        )
        experience_entries.append(
            {
                "entry_id": str(item.get("id") or f"experience-{index}"),
                "employer_name": source_value(item.get("company"), present="company" in item),
                "position": source_value(item.get("position"), present="position" in item),
                "start_date": _date_value(item.get("start")),
                "end_date": _date_value(item.get("end")),
                "currently_active": source_value(
                    item.get("end") is None,
                    present=True,
                ),
                "description_blocks": [block] if block is not None else [],
            }
        )

    education_entries: list[dict[str, Any]] = []
    education = payload.get("education")
    primary = education.get("primary") if isinstance(education, dict) else None
    for index, item in enumerate(primary or []):
        if not isinstance(item, dict):
            continue
        education_entries.append(
            {
                "entry_id": str(item.get("id") or f"education-{index}"),
                "organization": source_value(item.get("name"), present="name" in item),
                "degree": source_value(None, present=False),
                "field": source_value(
                    item.get("result") or item.get("organization"),
                    present="result" in item or "organization" in item,
                ),
                "graduation_year": source_value(item.get("year"), present="year" in item),
            }
        )

    language_entries: list[dict[str, Any]] = []
    for item in payload.get("language") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        level = item.get("level")
        level_value = level.get("name") if isinstance(level, dict) else level
        language_entries.append(
            {
                "language": name,
                "level": source_value(level_value, present=level is not None),
            }
        )

    area = payload.get("area")
    resume_location = None
    if isinstance(area, dict):
        resume_location = {
            "area_id": area.get("id"),
            "area_name": area.get("name"),
            "address": None,
            "metro": [],
            "source_facts": [],
        }

    total_months = None
    total_experience = payload.get("total_experience")
    if isinstance(total_experience, dict) and isinstance(total_experience.get("months"), int):
        total_months = total_experience["months"]
    total_years = str((Decimal(total_months) / Decimal(12)).quantize(Decimal("0.01"))) if total_months is not None else None

    skill_values = payload.get("skill_set") or []
    skill_labels = labels(skill_values)
    work_preferences = {
        "locations": [area.get("name")]
        if isinstance(area, dict) and isinstance(area.get("name"), str)
        else [],
        "work_formats": [item["key"] for item in labels(payload.get("work_format"))],
        "employment": [item["key"] for item in labels(payload.get("employment"))],
        "schedules": [item["key"] for item in labels(payload.get("schedule"))],
    }
    semantic = {
        "headline": source_value(payload.get("title"), present="title" in payload),
        "skill_set": skill_labels,
        "about": source_value(payload.get("skills"), present="skills" in payload),
        "experience_entries": experience_entries,
        "projects": [],
        "education": education_entries,
        "languages": language_entries,
        "location": source_value(resume_location, present="area" in payload),
        "work_preferences": source_value(work_preferences, present=True),
        "relocation": source_value(None, present=False),
        "business_trips": source_value(None, present=False),
        "total_experience_years": source_value(total_years, present=total_years is not None),
    }
    semantic_hash = sha256_bytes(canonical_json_bytes(semantic))
    materialization_key = f"hh:resume:{identity.account_key}:{identity.source_entity_id}:{raw_sha256}:{NORMALIZATION_VERSION}"
    dq = _dq(warnings=warnings)
    normalized = {
        "schema_version": RESUME_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "dictionary_version": DICTIONARY_VERSION,
        "materialization_key": materialization_key,
        "source_key": "hh",
        "account_key": identity.account_key,
        "source_entity_id": identity.source_entity_id,
        "raw": {
            "raw_uri": raw_uri,
            "raw_sha256": raw_sha256,
            "observed_at": identity.observed_at,
        },
        "semantic_content_hash": semantic_hash,
        **semantic,
        "dq": dq,
    }
    searchable_text = "\n".join(
        str(value)
        for value in [payload.get("title"), payload.get("skills")]
        if isinstance(value, str) and value.strip()
    )
    projection = {
        "title": payload.get("title"),
        "normalized_text": searchable_text or None,
        "skill_keys": [item["key"] for item in skill_labels],
    }
    return normalized, projection


def normalize_raw_object(key: str, body: bytes, metadata: dict[str, Any], raw_bucket: str) -> NormalizedResult:
    identity = parse_raw_identity(key)
    raw_sha256 = sha256_bytes(body)
    stored_sha = str(metadata.get("sha256", "")).lower()
    if stored_sha and stored_sha != raw_sha256:
        raise ValueError(f"RAW sha256 metadata mismatch: s3://{raw_bucket}/{key}")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("full HH entity RAW payload must be a JSON object")
    raw_uri = f"s3://{raw_bucket}/{key}"
    if identity.kind == "vacancy":
        normalized, projection = normalize_vacancy(
            payload,
            identity=identity,
            raw_uri=raw_uri,
            raw_sha256=raw_sha256,
        )
    else:
        normalized, projection = normalize_resume(
            payload,
            identity=identity,
            raw_uri=raw_uri,
            raw_sha256=raw_sha256,
        )
    normalized_body = canonical_json_bytes(normalized)
    normalized_sha = sha256_bytes(normalized_body)
    normalized_key = (
        f"normalized/{identity.kind}/source=hh/entity={identity.source_entity_id}/"
        f"raw_sha256={raw_sha256}/{NORMALIZATION_VERSION}.json"
    )
    return NormalizedResult(
        kind=identity.kind,
        account_key=identity.account_key,
        profile_key=identity.profile_key,
        source_entity_id=identity.source_entity_id,
        raw_key=key,
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


def _s3_client(settings: Settings) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name=settings.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def list_raw_entity_keys(settings: Settings) -> list[str]:
    client = _s3_client(settings)
    keys: list[str] = []
    for kind in ("vacancy", "resume"):
        prefix = f"{settings.raw_prefix}/{kind}/"
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.raw_bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item["Key"])
                if _RAW_KEY_RE.fullmatch(key):
                    keys.append(key)
    return sorted(keys)


def normalize_partition(keys: Iterable[str], settings_dict: dict[str, Any]) -> Iterator[NormalizedResult]:
    settings = Settings(**settings_dict)
    client = _s3_client(settings)
    for key in keys:
        response = client.get_object(Bucket=settings.raw_bucket, Key=key)
        body = response["Body"].read()
        metadata = response.get("Metadata") or {}
        yield normalize_raw_object(key, body, metadata, settings.raw_bucket)


def put_normalized(settings: Settings, result: NormalizedResult) -> str:
    from botocore.exceptions import ClientError

    client = _s3_client(settings)
    body = result.payload_json.encode("utf-8")
    try:
        existing = client.get_object(Bucket=settings.lake_bucket, Key=result.normalized_key)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) not in {"404", "NoSuchKey", "NotFound"}:
            raise
    else:
        existing_body = existing["Body"].read()
        if sha256_bytes(existing_body) != result.normalized_sha256:
            raise RuntimeError(
                "normalized content-addressed key collision: "
                f"s3://{settings.lake_bucket}/{result.normalized_key}"
            )
        return f"s3://{settings.lake_bucket}/{result.normalized_key}"

    client.put_object(
        Bucket=settings.lake_bucket,
        Key=result.normalized_key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        Metadata={
            "sha256": result.normalized_sha256,
            "raw-sha256": result.raw_sha256,
            "semantic-sha256": result.semantic_content_hash,
            "producer": "careerops-normalizer-spark",
        },
    )
    return f"s3://{settings.lake_bucket}/{result.normalized_key}"


def _upsert_source(cursor: Any) -> int:
    cursor.execute(
        """
        INSERT INTO careerops_v2.sources (source_key)
        VALUES ('hh')
        ON CONFLICT (source_key) DO UPDATE SET updated_at = now()
        RETURNING id
        """
    )
    return int(cursor.fetchone()[0])


def publish_postgres(settings: Settings, result: NormalizedResult, normalized_uri: str) -> None:
    if settings.postgres_dsn is None:
        return
    import psycopg

    projection = json.loads(result.db_projection_json)
    with psycopg.connect(settings.postgres_dsn) as connection, connection.cursor() as cursor:
        source_id = _upsert_source(cursor)
        common = (
            result.observed_at,
            f"s3://{settings.raw_bucket}/{result.raw_key}",
            result.semantic_content_hash,
            NORMALIZATION_VERSION,
            result.materialization_key,
            result.raw_sha256,
            normalized_uri,
            result.normalized_sha256,
            result.semantic_content_hash,
            VACANCY_SCHEMA_VERSION if result.kind == "vacancy" else RESUME_SCHEMA_VERSION,
            DICTIONARY_VERSION,
            result.dq_status,
            result.processing_ready,
        )
        if result.kind == "vacancy":
            cursor.execute(
                """
                INSERT INTO careerops_v2.vacancies (
                    source_id, source_vacancy_id, title, description, location, remote,
                    employment_type, experience, skill_keys, salary_from, salary_to,
                    salary_currency, archived, closed_for_applicants, published_at,
                    materialization_state, observed_at, raw_uri, content_hash,
                    normalization_version, materialization_key, raw_sha256, normalized_uri,
                    normalized_sha256, semantic_content_hash, normalized_schema_version,
                    dictionary_version, dq_status, processing_ready
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'current', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (source_id, source_vacancy_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    location = EXCLUDED.location,
                    remote = EXCLUDED.remote,
                    employment_type = EXCLUDED.employment_type,
                    experience = EXCLUDED.experience,
                    skill_keys = EXCLUDED.skill_keys,
                    salary_from = EXCLUDED.salary_from,
                    salary_to = EXCLUDED.salary_to,
                    salary_currency = EXCLUDED.salary_currency,
                    archived = EXCLUDED.archived,
                    closed_for_applicants = EXCLUDED.closed_for_applicants,
                    published_at = EXCLUDED.published_at,
                    materialization_state = EXCLUDED.materialization_state,
                    observed_at = EXCLUDED.observed_at,
                    raw_uri = EXCLUDED.raw_uri,
                    content_hash = EXCLUDED.content_hash,
                    normalization_version = EXCLUDED.normalization_version,
                    materialization_key = EXCLUDED.materialization_key,
                    raw_sha256 = EXCLUDED.raw_sha256,
                    normalized_uri = EXCLUDED.normalized_uri,
                    normalized_sha256 = EXCLUDED.normalized_sha256,
                    semantic_content_hash = EXCLUDED.semantic_content_hash,
                    normalized_schema_version = EXCLUDED.normalized_schema_version,
                    dictionary_version = EXCLUDED.dictionary_version,
                    dq_status = EXCLUDED.dq_status,
                    processing_ready = EXCLUDED.processing_ready,
                    updated_at = now()
                """,
                (
                    source_id,
                    result.source_entity_id,
                    projection.get("title"),
                    projection.get("description"),
                    projection.get("location"),
                    projection.get("remote"),
                    projection.get("employment_type"),
                    projection.get("experience"),
                    projection.get("skill_keys") or [],
                    projection.get("salary_from"),
                    projection.get("salary_to"),
                    projection.get("salary_currency"),
                    projection.get("archived"),
                    projection.get("closed_for_applicants"),
                    projection.get("published_at"),
                    *common,
                ),
            )
            return

        cursor.execute(
            """
            INSERT INTO careerops_v2.accounts (source_id, account_key)
            VALUES (%s, %s)
            ON CONFLICT (source_id, account_key) DO UPDATE SET updated_at = now()
            RETURNING id
            """,
            (source_id, result.account_key),
        )
        account_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO careerops_v2.profiles (source_id, account_id, profile_key)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_id, profile_key) DO UPDATE SET
                account_id = EXCLUDED.account_id,
                updated_at = now()
            RETURNING id
            """,
            (source_id, account_id, result.profile_key),
        )
        profile_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO careerops_v2.resumes (
                source_id, account_id, profile_id, source_resume_id,
                title, normalized_text, skill_keys, lifecycle, present_in_upstream,
                materialization_state, observed_at, raw_uri, content_hash,
                normalization_version, materialization_key, raw_sha256, normalized_uri,
                normalized_sha256, semantic_content_hash, normalized_schema_version,
                dictionary_version, dq_status, processing_ready
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, 'active', true,
                'current', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (account_id, source_resume_id) DO UPDATE SET
                profile_id = EXCLUDED.profile_id,
                title = EXCLUDED.title,
                normalized_text = EXCLUDED.normalized_text,
                skill_keys = EXCLUDED.skill_keys,
                lifecycle = 'active',
                present_in_upstream = true,
                inactive_at = NULL,
                materialization_state = EXCLUDED.materialization_state,
                observed_at = EXCLUDED.observed_at,
                raw_uri = EXCLUDED.raw_uri,
                content_hash = EXCLUDED.content_hash,
                normalization_version = EXCLUDED.normalization_version,
                materialization_key = EXCLUDED.materialization_key,
                raw_sha256 = EXCLUDED.raw_sha256,
                normalized_uri = EXCLUDED.normalized_uri,
                normalized_sha256 = EXCLUDED.normalized_sha256,
                semantic_content_hash = EXCLUDED.semantic_content_hash,
                normalized_schema_version = EXCLUDED.normalized_schema_version,
                dictionary_version = EXCLUDED.dictionary_version,
                dq_status = EXCLUDED.dq_status,
                processing_ready = EXCLUDED.processing_ready,
                updated_at = now()
            """,
            (
                source_id,
                account_id,
                profile_id,
                result.source_entity_id,
                projection.get("title"),
                projection.get("normalized_text"),
                projection.get("skill_keys") or [],
                *common,
            ),
        )


def write_manifest(settings: Settings, rows: list[dict[str, Any]], run_id: str) -> str:
    client = _s3_client(settings)
    key = f"normalized/manifests/run={run_id}.json"
    payload = {
        "schema_version": "careerops.normalizer-publication-manifest.v1",
        "normalization_version": NORMALIZATION_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "objects": rows,
    }
    body = canonical_json_bytes(payload)
    client.put_object(
        Bucket=settings.lake_bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        Metadata={"sha256": sha256_bytes(body), "producer": "careerops-normalizer-spark"},
    )
    return f"s3://{settings.lake_bucket}/{key}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="careerops-normalizer-spark")
    parser.add_argument("--limit", type=int, default=0, help="process at most N RAW entity objects")
    parser.add_argument(
        "--no-postgres",
        action="store_true",
        help="write lake objects only; useful for contract smoke tests",
    )
    return parser.parse_args()


def main() -> int:
    from pyspark.sql import SparkSession

    args = parse_args()
    settings = Settings.from_env()
    if args.no_postgres:
        settings = Settings(**{**settings.__dict__, "postgres_dsn": None})
    keys = list_raw_entity_keys(settings)
    if args.limit > 0:
        keys = keys[: args.limit]
    if not keys:
        print("no immutable HH vacancy/resume RAW objects found")
        return 0

    spark = SparkSession.builder.appName("careerops-normalizer-spark").getOrCreate()
    settings_dict = settings.__dict__.copy()
    rdd = spark.sparkContext.parallelize(keys, min(settings.partitions, len(keys)))
    normalized = rdd.mapPartitions(lambda part: normalize_partition(part, settings_dict))

    manifest_rows: list[dict[str, Any]] = []
    try:
        for result in normalized.toLocalIterator():
            normalized_uri = put_normalized(settings, result)
            publish_postgres(settings, result, normalized_uri)
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
    manifest_uri = write_manifest(settings, manifest_rows, run_id)
    print(
        json.dumps(
            {"processed": len(manifest_rows), "manifest_uri": manifest_uri},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
