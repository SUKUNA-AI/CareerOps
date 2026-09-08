from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from careerops_processing.contracts import (
    DataQualityReport,
    DataQualityStatus,
    Employer,
    ExperienceRange,
    NormalizedVacancy,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    RawObservationRef,
    Requirement,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSet,
    SemanticPolarity,
    SemanticSourceRef,
    SourceTextRef,
    SourceValue,
    TextBlock,
    ValueState,
)
from careerops_processing.core import extract_requirements
from careerops_processing.core.text_semantics import subjects_from_statement
from careerops_processing.semantic_cache import (
    P204SemanticArtifactResolver,
    SemanticArtifactIntegrityError,
    SemanticArtifactKey,
)

HASH_A = "a" * 64
HASH_C = "c" * 64


def _known(value: Any) -> SourceValue[Any]:
    return SourceValue(state=ValueState.KNOWN, value=value)


def _missing() -> SourceValue[Any]:
    return SourceValue(state=ValueState.NOT_PROVIDED)


def _vacancy(
    *,
    text: str = "",
    experience: SourceValue[ExperienceRange] | None = None,
) -> NormalizedVacancy:
    blocks = ()
    if text:
        blocks = (
            TextBlock(
                block_id="requirements",
                text=text,
                ordinal=0,
                heading="Требования",
                section_hint="requirements",
                source_ref=SourceTextRef(
                    source_path="description",
                    locator="block:requirements",
                    quote=text,
                ),
            ),
        )
    return NormalizedVacancy(
        schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="spark-hh-normalizer-1",
        dictionary_version="careerops-dictionary-2026-09",
        materialization_key="vacancy:42",
        source_key="hh",
        source_entity_id="vacancy-42",
        raw=RawObservationRef(
            raw_uri="s3://careerops-raw/test/a.json",
            raw_sha256=HASH_A,
            observed_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
        ),
        semantic_content_hash=HASH_C,
        title=_known("Data Engineer"),
        employer=_known(Employer(name="Example")),
        professional_roles=(),
        key_skills=(),
        experience=experience or _missing(),
        employment=(),
        schedules=(),
        work_formats=(),
        location=_missing(),
        relocation_facts=(),
        salary=_missing(),
        archived=_known(False),
        closed_for_applicants=_known(False),
        published_at=_missing(),
        text_blocks=blocks,
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )


def test_technology_with_experience_threshold_stays_technology() -> None:
    result = extract_requirements(
        _vacancy(text="Python или Scala, опыт от 3 лет\nОпыт от 4 лет"),
        extraction_version="requirements-v2",
    )

    any_group = next(
        group for group in result.groups if group.operator is RequirementGroupOperator.ANY
    )
    alternatives = [
        item
        for item in result.requirements
        if item.requirement_id in any_group.requirement_ids
    ]
    assert len(alternatives) == 2
    assert all(item.kind is RequirementKind.TECHNOLOGY for item in alternatives)
    assert all(item.threshold is not None for item in alternatives)
    assert {
        item.threshold.minimum
        for item in alternatives
        if item.threshold is not None
    } == {Decimal("3")}

    standalone = next(
        item for item in result.requirements if item.statement == "Опыт от 4 лет"
    )
    assert standalone.kind is RequirementKind.EXPERIENCE
    assert standalone.threshold is not None
    assert standalone.threshold.minimum == Decimal("4")


def test_structured_experience_range_does_not_create_hard_upper_bound() -> None:
    result = extract_requirements(
        _vacancy(
            experience=_known(
                ExperienceRange(
                    source_code="between1And3",
                    minimum_years=Decimal("1"),
                    maximum_years=Decimal("3"),
                )
            )
        ),
        extraction_version="requirements-v2",
    )

    requirement = next(
        item for item in result.requirements if item.kind is RequirementKind.EXPERIENCE
    )
    assert requirement.threshold is not None
    assert requirement.threshold.minimum == Decimal("1")
    assert requirement.threshold.maximum is None
    assert "1–3" in requirement.statement
    assert requirement.source_refs[0].rendered_value == "between1And3"


def test_requirement_contract_rejects_incoherent_modality_and_non_all_root() -> None:
    source_ref = SemanticSourceRef(source_path="description", rendered_value="Python")
    with pytest.raises(ValidationError, match="PROHIBITED"):
        Requirement(
            requirement_id="req-1",
            kind=RequirementKind.TECHNOLOGY,
            statement="Python запрещён",
            importance=RequirementImportance.MANDATORY,
            modality=RequirementModality.PROHIBITED,
            polarity=SemanticPolarity.POSITIVE,
            source_refs=(source_ref,),
        )

    with pytest.raises(ValidationError, match="modality and importance"):
        Requirement(
            requirement_id="req-2",
            kind=RequirementKind.TECHNOLOGY,
            statement="Python",
            importance=RequirementImportance.OPTIONAL,
            modality=RequirementModality.REQUIRED,
            source_refs=(source_ref,),
        )

    requirements = (
        Requirement(
            requirement_id="req-3",
            kind=RequirementKind.TECHNOLOGY,
            statement="Python",
            source_refs=(source_ref,),
        ),
        Requirement(
            requirement_id="req-4",
            kind=RequirementKind.TECHNOLOGY,
            statement="Scala",
            source_refs=(source_ref,),
        ),
    )
    with pytest.raises(ValidationError, match="root requirement group must use ALL"):
        RequirementSet(
            source_key="hh",
            source_entity_id="vacancy-42",
            semantic_content_hash=HASH_C,
            normalized_schema_version="vacancy-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            extraction_version="requirements-v2",
            requirements=requirements,
            groups=(
                RequirementGroup(
                    group_id="root",
                    operator=RequirementGroupOperator.ANY,
                    requirement_ids=("req-3", "req-4"),
                ),
            ),
            root_group_id="root",
        )


def test_subject_matching_respects_identifier_boundaries_and_aliases() -> None:
    subjects = subjects_from_statement(
        "Django и Mongo, сервис на C++ и ASP.NET",
        known_subjects=("Go", "C", "C++", ".NET"),
    )
    normalized = {item.normalized for item in subjects}
    assert "go" not in normalized
    assert "c" not in normalized
    assert "c++" in normalized
    assert ".net" in normalized


class _Registry:
    def __init__(self) -> None:
        self.ref: ProcessingArtifactRef | None = None

    @asynccontextmanager
    async def lock(self, key: SemanticArtifactKey) -> AsyncIterator[None]:
        del key
        yield

    async def get(self, key: SemanticArtifactKey) -> ProcessingArtifactRef | None:
        del key
        return self.ref

    async def put(
        self,
        key: SemanticArtifactKey,
        ref: ProcessingArtifactRef,
    ) -> ProcessingArtifactRef:
        del key
        self.ref = ref
        return ref


class _Publisher:
    def __init__(self) -> None:
        self.requirement_publish_count = 0
        self.fail_verification = False

    async def publish_requirement_set(self, requirement_set: Any) -> ProcessingArtifactRef:
        del requirement_set
        self.requirement_publish_count += 1
        return ProcessingArtifactRef(
            kind=ProcessingArtifactKind.REQUIREMENT_SET,
            schema_version="careerops.processing.requirement-set.v2",
            uri="s3://careerops-artifacts/processing/requirement-set.json",
            sha256="b" * 64,
            size_bytes=10,
        )

    async def publish_resume_evidence_set(self, evidence_set: Any) -> ProcessingArtifactRef:
        del evidence_set
        raise AssertionError("resume evidence publication is not expected")

    async def verify_artifact(self, ref: ProcessingArtifactRef) -> None:
        del ref
        if self.fail_verification:
            raise SemanticArtifactIntegrityError("corrupted semantic artifact")


@pytest.mark.asyncio
async def test_semantic_resolver_reuses_one_requirement_artifact() -> None:
    registry = _Registry()
    publisher = _Publisher()
    resolver = P204SemanticArtifactResolver(registry=registry, publisher=publisher)
    vacancy = _vacancy(text="Python")

    first = await resolver.resolve_requirements(
        vacancy,
        extraction_version="requirements-v2",
    )
    second = await resolver.resolve_requirements(
        vacancy,
        extraction_version="requirements-v2",
    )

    assert first == second
    assert publisher.requirement_publish_count == 1

    publisher.fail_verification = True
    with pytest.raises(SemanticArtifactIntegrityError):
        await resolver.resolve_requirements(
            vacancy,
            extraction_version="requirements-v2",
        )
