from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from careerops_processing.contracts import (
    FILTER_TRACE_SCHEMA_VERSION,
    P204_RESULT_SCHEMA_VERSION,
    REQUIREMENT_SET_SCHEMA_VERSION,
    RESUME_EVIDENCE_SET_SCHEMA_VERSION,
    BindingSnapshot,
    DataQualityReport,
    DataQualityStatus,
    Employer,
    EntityType,
    EvidenceActorScope,
    EvidenceContext,
    EvidenceKind,
    EvidenceStrength,
    ExperienceEntry,
    ExperienceRange,
    FilterDecision,
    FilterOutcome,
    Location,
    NormalizedRef,
    NormalizedResume,
    NormalizedVacancy,
    P204ResultArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
    ProcessingInputManifest,
    ProcessingVersionBundle,
    ProjectEntry,
    RawObservationRef,
    Requirement,
    RequirementContext,
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementModality,
    RequirementSet,
    RequirementThresholdMetric,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SourceLabel,
    SourceTextRef,
    SourceValue,
    TargetPolicy,
    TextBlock,
    ValueState,
    WorkPreferences,
)
from careerops_processing.core import extract_requirements, extract_resume_evidence
from careerops_processing.core.text_semantics import subjects_from_statement
from careerops_processing.executor import P204Executor
from careerops_processing.infrastructure import (
    ProcessingArtifactPublisher,
    ProcessingArtifactStore,
    ProcessingArtifactStoreSettings,
    S3ProcessingInputLoader,
)
from careerops_processing.queue import (
    RECONCILIATION_WITHDRAWN,
    ProcessingJobRecord,
    ProcessingJobStatus,
    ProcessingPairKey,
    ProcessingWorkSpec,
)
from careerops_processing.semantic_cache import (
    P204SemanticArtifactResolver,
    SemanticArtifactKey,
)
from careerops_processing.service.config import ProcessingRuntimeConfig
from careerops_processing.worker import ProcessingWorker

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _known(value: Any) -> SourceValue[Any]:
    return SourceValue(state=ValueState.KNOWN, value=value)


def _missing() -> SourceValue[Any]:
    return SourceValue(state=ValueState.NOT_PROVIDED)


def _raw(sha: str) -> RawObservationRef:
    return RawObservationRef(
        raw_uri=f"s3://careerops-raw/test/{sha}.json",
        raw_sha256=sha,
        observed_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )


def _text_block(
    *,
    block_id: str,
    text: str,
    ordinal: int = 0,
    heading: str | None = None,
    section_hint: str | None = None,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        text=text,
        ordinal=ordinal,
        heading=heading,
        section_hint=section_hint,
        source_ref=SourceTextRef(
            source_path="description",
            locator=f"block:{block_id}",
            quote=text,
        ),
    )


def _vacancy(
    *,
    title: str = "Data Engineer",
    text_blocks: tuple[TextBlock, ...] = (),
    key_skills: tuple[SourceLabel, ...] = (),
    experience: SourceValue[ExperienceRange] | None = None,
    employment: tuple[SourceLabel, ...] = (),
    schedules: tuple[SourceLabel, ...] = (),
    work_formats: tuple[SourceLabel, ...] = (),
    location: SourceValue[Location] | None = None,
    relocation_facts: tuple[str, ...] = (),
) -> NormalizedVacancy:
    return NormalizedVacancy(
        schema_version="careerops.hh.vacancy.normalized.v1",
        normalization_version="spark-hh-normalizer-1",
        dictionary_version="careerops-dictionary-2026-09",
        materialization_key="vacancy:42",
        source_key="hh",
        source_entity_id="vacancy-42",
        raw=_raw(HASH_A),
        semantic_content_hash=HASH_C,
        title=_known(title),
        employer=_known(Employer(name="Example")),
        professional_roles=(),
        key_skills=key_skills,
        experience=experience or _missing(),
        employment=employment,
        schedules=schedules,
        work_formats=work_formats,
        location=location or _missing(),
        relocation_facts=relocation_facts,
        salary=_missing(),
        archived=_known(False),
        closed_for_applicants=_known(False),
        published_at=_missing(),
        text_blocks=text_blocks,
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )


def _resume(
    *,
    skill_set: tuple[SourceLabel, ...] = (SourceLabel(key="kafka", label="Kafka"),),
    about: str = "Команда использовала Spark",
    experience_blocks: tuple[TextBlock, ...] | None = None,
    include_preferences: bool = False,
) -> NormalizedResume:
    blocks = experience_blocks or (
        _text_block(
            block_id="exp-1",
            text="Разработал Kafka consumer\nНе работал с Kubernetes",
        ),
    )
    project_block = _text_block(
        block_id="project-1",
        text="Создал ETL pipeline на Python",
    )
    return NormalizedResume(
        schema_version="careerops.hh.resume.normalized.v1",
        normalization_version="spark-hh-normalizer-1",
        dictionary_version="careerops-dictionary-2026-09",
        materialization_key="resume:7",
        source_key="hh",
        account_key="junior",
        source_entity_id="resume-7",
        raw=_raw(HASH_B),
        semantic_content_hash=HASH_D,
        headline=_known("Data Engineer"),
        skill_set=skill_set,
        about=_known(about),
        experience_entries=(
            ExperienceEntry(
                entry_id="exp-entry-1",
                employer_name=_known("Example"),
                position=_known("Data Engineer"),
                start_date=_known(date(2024, 1, 1)),
                end_date=_known(date(2025, 1, 1)),
                currently_active=_known(False),
                description_blocks=blocks,
            ),
        ),
        projects=(
            ProjectEntry(
                project_id="project-entry-1",
                name=_known("CareerOPS"),
                role=_known("Developer"),
                start_date=_known(date(2025, 2, 1)),
                end_date=_known(date(2025, 6, 1)),
                description_blocks=(project_block,),
            ),
        ),
        education=(),
        languages=(),
        location=_known(Location(area_name="Москва")) if include_preferences else _missing(),
        work_preferences=(
            _known(
                WorkPreferences(
                    locations=("Москва",),
                    work_formats=("remote",),
                    employment=("full",),
                    schedules=("full_day",),
                )
            )
            if include_preferences
            else _missing()
        ),
        relocation=_known(False) if include_preferences else _missing(),
        business_trips=_known(True) if include_preferences else _missing(),
        total_experience_years=_known(Decimal("2.0")),
        dq=DataQualityReport(
            status=DataQualityStatus.CLEAN,
            full_entity_available=True,
            parse_complete=True,
        ),
    )


def _normalized_ref(entity_type: EntityType) -> NormalizedRef:
    is_resume = entity_type is EntityType.RESUME
    return NormalizedRef(
        entity_type=entity_type,
        source_key="hh",
        source_entity_id="resume-7" if is_resume else "vacancy-42",
        account_key="junior" if is_resume else None,
        raw=_raw(HASH_B if is_resume else HASH_A),
        normalized_uri=(
            "s3://careerops-lake/normalized/resume/b.json"
            if is_resume
            else "s3://careerops-lake/normalized/vacancy/a.json"
        ),
        normalized_sha256=HASH_B if is_resume else HASH_A,
        semantic_content_hash=HASH_D if is_resume else HASH_C,
        schema_version=(
            "careerops.hh.resume.normalized.v1"
            if is_resume
            else "careerops.hh.vacancy.normalized.v1"
        ),
        normalization_version="spark-hh-normalizer-1",
        dictionary_version="careerops-dictionary-2026-09",
        materialization_key="resume:7" if is_resume else "vacancy:42",
        processing_ready=True,
        dq_status=DataQualityStatus.CLEAN,
    )


def _manifest(*, frontend_exclusion: bool = False) -> ProcessingInputManifest:
    filtering: dict[str, object] = {"schema_version": 1}
    if frontend_exclusion:
        filtering["allowed_primary_roles"] = ["data_engineering"]
    policy = TargetPolicy.from_content(
        target_key="data_engineer_junior",
        schema_version="careerops.target-policy.v1",
        policy_version="2026-09-08.1",
        content={"filtering": filtering},
    )
    return ProcessingInputManifest(
        vacancy=_normalized_ref(EntityType.VACANCY),
        resume=_normalized_ref(EntityType.RESUME),
        binding=BindingSnapshot(
            binding_key="de-junior",
            binding_version=4,
            account_key="junior",
            source_resume_id="resume-7",
            target_key="data_engineer_junior",
        ),
        target_policy=policy,
        versions=ProcessingVersionBundle(
            pipeline_version="processing-v2",
            dictionary_version="careerops-dictionary-2026-09",
            filter_version="filter-v1",
            requirement_extraction_version="requirements-v2",
            evidence_version="evidence-v2",
            qualification_version="qualification-v1",
            scoring_version="scoring-v1",
            calibration_version="calibration-unset",
        ),
        as_of=datetime(2026, 9, 8, 12, tzinfo=UTC),
    )


def _semantic_ref(path: str = "description") -> SemanticSourceRef:
    return SemanticSourceRef(source_path=path, rendered_value="Python")


def _requirement(requirement_id: str) -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        kind=RequirementKind.TECHNOLOGY,
        statement="Python",
        source_refs=(_semantic_ref(),),
    )


def _artifact_schema(kind: ProcessingArtifactKind) -> str:
    return {
        ProcessingArtifactKind.FILTER_TRACE: FILTER_TRACE_SCHEMA_VERSION,
        ProcessingArtifactKind.REQUIREMENT_SET: REQUIREMENT_SET_SCHEMA_VERSION,
        ProcessingArtifactKind.RESUME_EVIDENCE_SET: RESUME_EVIDENCE_SET_SCHEMA_VERSION,
        ProcessingArtifactKind.P2_04_RESULT: P204_RESULT_SCHEMA_VERSION,
    }[kind]


def _artifact_ref(kind: ProcessingArtifactKind, suffix: str) -> ProcessingArtifactRef:
    digest = hashlib.sha256(suffix.encode("utf-8")).hexdigest()
    return ProcessingArtifactRef(
        kind=kind,
        schema_version=_artifact_schema(kind),
        uri=f"s3://careerops-artifacts/processing/{suffix}/{digest}.json",
        sha256=digest,
        size_bytes=10,
    )


def test_requirement_graph_rejects_invalid_operators_and_multiple_ownership() -> None:
    with pytest.raises(ValidationError, match="ANY"):
        RequirementGroup(
            group_id="one",
            operator=RequirementGroupOperator.ANY,
            requirement_ids=("req-1",),
        )

    with pytest.raises(ValidationError, match="condition"):
        RequirementGroup(
            group_id="conditional",
            operator=RequirementGroupOperator.CONDITIONAL,
            requirement_ids=("req-1",),
        )

    requirement = _requirement("req-1")
    with pytest.raises(ValidationError, match="exactly one"):
        RequirementSet(
            source_key="hh",
            source_entity_id="vacancy-42",
            semantic_content_hash=HASH_C,
            normalized_schema_version="vacancy-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            extraction_version="requirements-v2",
            requirements=(requirement,),
            groups=(
                RequirementGroup(
                    group_id="root",
                    operator=RequirementGroupOperator.ALL,
                    requirement_ids=("req-1",),
                    child_group_ids=("child",),
                ),
                RequirementGroup(
                    group_id="child",
                    operator=RequirementGroupOperator.ALL,
                    requirement_ids=("req-1",),
                ),
            ),
            root_group_id="root",
        )


def test_requirement_graph_rejects_duplicate_ids_and_cycles() -> None:
    duplicate = _requirement("req-1")
    with pytest.raises(ValidationError, match="unique"):
        RequirementSet(
            source_key="hh",
            source_entity_id="vacancy-42",
            semantic_content_hash=HASH_C,
            normalized_schema_version="vacancy-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            extraction_version="requirements-v2",
            requirements=(duplicate, duplicate),
            groups=(
                RequirementGroup(
                    group_id="root",
                    operator=RequirementGroupOperator.ALL,
                    requirement_ids=("req-1",),
                ),
            ),
            root_group_id="root",
        )

    with pytest.raises(ValidationError, match="root requirement group"):
        RequirementSet(
            source_key="hh",
            source_entity_id="vacancy-42",
            semantic_content_hash=HASH_C,
            normalized_schema_version="vacancy-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            extraction_version="requirements-v2",
            requirements=(_requirement("req-1"),),
            groups=(
                RequirementGroup(
                    group_id="root",
                    operator=RequirementGroupOperator.ALL,
                    requirement_ids=("req-1",),
                    child_group_ids=("child",),
                ),
                RequirementGroup(
                    group_id="child",
                    operator=RequirementGroupOperator.ALL,
                    child_group_ids=("root",),
                ),
            ),
            root_group_id="root",
        )


def test_requirement_extractor_preserves_logic_thresholds_and_negation() -> None:
    block = _text_block(
        block_id="requirements",
        heading="Требования",
        section_hint="requirements",
        text=(
            "Python или Scala, опыт от 3 лет\n"
            "Kafka будет плюсом\n"
            "Если работа с потоками: Spark обязателен\n"
            "Kubernetes не требуется\n"
            "Использование Windows запрещено"
        ),
    )
    result = extract_requirements(
        _vacancy(
            text_blocks=(block,),
            key_skills=(
                SourceLabel(key="kubernetes", label="Kubernetes"),
                SourceLabel(key="windows", label="Windows"),
            ),
        ),
        extraction_version="requirements-v2",
    )

    any_group = next(
        group for group in result.groups if group.operator is RequirementGroupOperator.ANY
    )
    alternatives = {
        item.requirement_id: item
        for item in result.requirements
        if item.requirement_id in any_group.requirement_ids
    }
    assert {
        subject.normalized
        for item in alternatives.values()
        for subject in item.subjects
    } == {"python", "scala"}
    assert all(item.threshold is not None for item in alternatives.values())
    assert {
        item.threshold.minimum for item in alternatives.values() if item.threshold is not None
    } == {Decimal("3")}
    assert all(
        item.context is RequirementContext.QUALIFICATION for item in alternatives.values()
    )

    kafka = next(item for item in result.requirements if item.statement == "Kafka будет плюсом")
    assert kafka.importance is RequirementImportance.PREFERRED
    assert kafka.modality is RequirementModality.PREFERRED

    conditional = next(
        group
        for group in result.groups
        if group.operator is RequirementGroupOperator.CONDITIONAL
    )
    assert conditional.condition == "работа с потоками"

    kubernetes = next(
        item for item in result.requirements if item.statement == "Kubernetes не требуется"
    )
    assert kubernetes.modality is RequirementModality.NOT_REQUIRED
    assert kubernetes.importance is RequirementImportance.OPTIONAL
    assert kubernetes.polarity is SemanticPolarity.POSITIVE

    windows = next(
        item for item in result.requirements if item.statement == "Использование Windows запрещено"
    )
    assert windows.modality is RequirementModality.PROHIBITED
    assert windows.importance is RequirementImportance.MANDATORY
    assert windows.polarity is SemanticPolarity.NEGATIVE


def test_requirement_extractor_uses_structured_vacancy_fields() -> None:
    vacancy = _vacancy(
        experience=_known(
            ExperienceRange(
                source_code="between1And3",
                minimum_years=Decimal("1"),
                maximum_years=Decimal("3"),
            )
        ),
        employment=(SourceLabel(key="full", label="Полная занятость"),),
        schedules=(SourceLabel(key="fullDay", label="Полный день"),),
        work_formats=(SourceLabel(key="remote", label="Удаленно"),),
        location=_known(Location(area_name="Москва")),
        relocation_facts=("Переезд не требуется",),
    )
    result = extract_requirements(vacancy, extraction_version="requirements-v2")

    experience = next(
        item
        for item in result.requirements
        if item.kind is RequirementKind.EXPERIENCE
    )
    assert experience.threshold is not None
    assert experience.threshold.metric is RequirementThresholdMetric.EXPERIENCE_YEARS
    assert experience.threshold.minimum == Decimal("1")
    assert experience.threshold.maximum is None
    assert experience.source_refs[0].source_path == "experience"

    work_conditions = {
        item.statement
        for item in result.requirements
        if item.kind is RequirementKind.WORK_CONDITION
    }
    assert {"Полная занятость", "Полный день", "Удаленно", "Москва"} <= work_conditions


def test_subject_matching_uses_token_boundaries_and_aliases() -> None:
    false_matches = subjects_from_statement(
        "Работал с Django и MongoDB на Linux",
        known_subjects=("Go", "C", "R"),
    )
    assert {item.normalized for item in false_matches}.isdisjoint({"go", "c", "r"})

    exact = subjects_from_statement(
        "Разработка на C++ и ASP.NET Core, раньше CSharp",
        known_subjects=("C", "C++", ".NET", "C#"),
    )
    assert {item.normalized for item in exact} == {"c++", ".net", "c#"}


def test_requirement_dedup_merges_provenance_without_source_based_identity() -> None:
    vacancy = _vacancy(
        text_blocks=(
            _text_block(block_id="a", text="Python", heading="Требования", ordinal=0),
            _text_block(block_id="b", text="Python", heading="Требования", ordinal=1),
        )
    )
    result = extract_requirements(vacancy, extraction_version="requirements-v2")
    python_requirements = [item for item in result.requirements if item.statement == "Python"]
    assert len(python_requirements) == 1
    assert len(python_requirements[0].source_refs) == 2


def test_requirement_extraction_is_deterministic_and_versioned() -> None:
    vacancy = _vacancy(
        text_blocks=(
            _text_block(
                block_id="requirements",
                heading="Требования",
                text="Python\nSQL",
            ),
        )
    )
    first = extract_requirements(vacancy, extraction_version="requirements-v2")
    replay = extract_requirements(vacancy, extraction_version="requirements-v2")
    changed = extract_requirements(vacancy, extraction_version="requirements-v3")

    assert first == replay
    assert [item.requirement_id for item in first.requirements] != [
        item.requirement_id for item in changed.requirements
    ]


def test_resume_evidence_preserves_scope_polarity_strength_time_and_preferences() -> None:
    result = extract_resume_evidence(
        _resume(include_preferences=True),
        evidence_version="evidence-v2",
    )

    headline = next(item for item in result.evidence if item.kind is EvidenceKind.HEADLINE)
    assert headline.statement == "Data Engineer"
    assert headline.strength is EvidenceStrength.MENTION

    skill = next(item for item in result.evidence if item.kind is EvidenceKind.SKILL)
    assert skill.strength is EvidenceStrength.MENTION
    assert skill.context is EvidenceContext.SKILL_LIST

    kafka_direct = next(
        item for item in result.evidence if item.statement == "Разработал Kafka consumer"
    )
    assert kafka_direct.actor_scope is EvidenceActorScope.SELF
    assert kafka_direct.strength is EvidenceStrength.DIRECT
    assert kafka_direct.context is EvidenceContext.COMMERCIAL
    assert kafka_direct.time_span is not None
    assert kafka_direct.time_span.start_date == date(2024, 1, 1)
    assert kafka_direct.time_span.end_date == date(2025, 1, 1)

    team = next(
        item for item in result.evidence if item.statement == "Команда использовала Spark"
    )
    assert team.actor_scope is EvidenceActorScope.TEAM

    negative = next(
        item for item in result.evidence if item.statement == "Не работал с Kubernetes"
    )
    assert negative.polarity is SemanticPolarity.NEGATIVE

    location = next(item for item in result.evidence if item.kind is EvidenceKind.LOCATION)
    assert location.statement == "Москва"
    assert location.context is EvidenceContext.CURRENT_LOCATION

    relocation = next(item for item in result.evidence if item.statement == "Не готов к переезду")
    assert relocation.polarity is SemanticPolarity.NEGATIVE
    trips = next(item for item in result.evidence if item.statement == "Готов к командировкам")
    assert trips.polarity is SemanticPolarity.POSITIVE


def test_resume_evidence_dedup_merges_multiple_source_refs() -> None:
    blocks = (
        _text_block(block_id="a", text="Разработал Kafka consumer", ordinal=0),
        _text_block(block_id="b", text="Разработал Kafka consumer", ordinal=1),
    )
    result = extract_resume_evidence(
        _resume(experience_blocks=blocks),
        evidence_version="evidence-v2",
    )
    direct = [item for item in result.evidence if item.statement == "Разработал Kafka consumer"]
    assert len(direct) == 1
    assert len(direct[0].source_refs) == 2


def test_resume_evidence_is_deterministic_and_versioned() -> None:
    resume = _resume()
    first = extract_resume_evidence(resume, evidence_version="evidence-v2")
    replay = extract_resume_evidence(resume, evidence_version="evidence-v2")
    changed = extract_resume_evidence(resume, evidence_version="evidence-v3")

    assert first == replay
    assert [item.evidence_id for item in first.evidence] != [
        item.evidence_id for item in changed.evidence
    ]


class _Body:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self) -> bytes:
        return self._body


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.put_count = 0

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        stored = self.objects.get((Bucket, Key))
        if stored is None:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )
        body, metadata = stored
        return {"Body": _Body(body), "Metadata": metadata}

    async def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Metadata: dict[str, str],
    ) -> dict[str, object]:
        del ContentType
        self.put_count += 1
        self.objects[(Bucket, Key)] = (bytes(Body), dict(Metadata))
        return {}


@pytest.mark.asyncio
async def test_p204_artifacts_are_content_addressed_and_idempotent() -> None:
    client = _FakeS3Client()
    store = ProcessingArtifactStore(
        ProcessingArtifactStoreSettings(
            endpoint_url="http://s3",
            access_key="key",
            secret_key="secret",
        ),
        client=client,
    )
    publisher = ProcessingArtifactPublisher(store)
    requirements = extract_requirements(
        _vacancy(
            text_blocks=(
                _text_block(block_id="requirements", heading="Требования", text="Python"),
            )
        ),
        extraction_version="requirements-v2",
    )
    evidence = extract_resume_evidence(_resume(), evidence_version="evidence-v2")

    req_ref = await publisher.publish_requirement_set(requirements)
    req_replay_ref = await publisher.publish_requirement_set(requirements)
    evidence_ref = await publisher.publish_resume_evidence_set(evidence)

    assert req_ref == req_replay_ref
    assert req_ref.kind is ProcessingArtifactKind.REQUIREMENT_SET
    assert req_ref.schema_version == REQUIREMENT_SET_SCHEMA_VERSION
    assert evidence_ref.kind is ProcessingArtifactKind.RESUME_EVIDENCE_SET
    assert evidence_ref.schema_version == RESUME_EVIDENCE_SET_SCHEMA_VERSION
    assert req_ref.sha256 in req_ref.uri
    assert evidence_ref.sha256 in evidence_ref.uri
    assert client.put_count == 2


class _MemoryRegistry:
    def __init__(self) -> None:
        self.refs: dict[str, ProcessingArtifactRef] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lock(self, key: SemanticArtifactKey) -> AsyncIterator[None]:
        lock = self.locks.setdefault(key.cache_key(), asyncio.Lock())
        async with lock:
            yield

    async def get(self, key: SemanticArtifactKey) -> ProcessingArtifactRef | None:
        return self.refs.get(key.cache_key())

    async def put(
        self,
        key: SemanticArtifactKey,
        ref: ProcessingArtifactRef,
    ) -> ProcessingArtifactRef:
        existing = self.refs.setdefault(key.cache_key(), ref)
        if existing != ref:
            raise AssertionError("semantic cache collision")
        return existing


class _SemanticPublisher:
    def __init__(self) -> None:
        self.requirement_publish_count = 0
        self.evidence_publish_count = 0
        self.verified: list[ProcessingArtifactRef] = []

    async def publish_requirement_set(
        self,
        requirement_set: RequirementSet,
    ) -> ProcessingArtifactRef:
        del requirement_set
        self.requirement_publish_count += 1
        await asyncio.sleep(0.01)
        return _artifact_ref(ProcessingArtifactKind.REQUIREMENT_SET, "requirements")

    async def publish_resume_evidence_set(
        self,
        evidence_set: ResumeEvidenceSet,
    ) -> ProcessingArtifactRef:
        del evidence_set
        self.evidence_publish_count += 1
        await asyncio.sleep(0.01)
        return _artifact_ref(ProcessingArtifactKind.RESUME_EVIDENCE_SET, "evidence")

    async def verify_artifact(self, ref: ProcessingArtifactRef) -> None:
        self.verified.append(ref)


@pytest.mark.asyncio
async def test_semantic_resolver_builds_each_semantic_version_once_under_concurrency() -> None:
    registry = _MemoryRegistry()
    publisher = _SemanticPublisher()
    resolver = P204SemanticArtifactResolver(registry=registry, publisher=publisher)
    vacancy = _vacancy(text_blocks=(_text_block(block_id="r", text="Python"),))
    resume = _resume()

    requirement_refs = await asyncio.gather(
        *(
            resolver.resolve_requirements(vacancy, extraction_version="requirements-v2")
            for _ in range(4)
        )
    )
    evidence_refs = await asyncio.gather(
        *(resolver.resolve_evidence(resume, evidence_version="evidence-v2") for _ in range(4))
    )

    assert len(set(requirement_refs)) == 1
    assert len(set(evidence_refs)) == 1
    assert publisher.requirement_publish_count == 1
    assert publisher.evidence_publish_count == 1
    assert len(registry.refs) == 2


class _Loader:
    def __init__(
        self,
        manifest: ProcessingInputManifest,
        vacancy: NormalizedVacancy,
        resume: NormalizedResume,
    ) -> None:
        self.manifest = manifest
        self.vacancy = vacancy
        self.resume = resume

    async def load_manifest(self, uri: str) -> ProcessingInputManifest:
        del uri
        return self.manifest

    async def load_vacancy(self, ref: NormalizedRef) -> NormalizedVacancy:
        del ref
        return self.vacancy

    async def load_resume(self, ref: NormalizedRef) -> NormalizedResume:
        del ref
        return self.resume


class _PairPublisher:
    def __init__(self) -> None:
        self.result: P204ResultArtifact | None = None
        self.filter_decision: FilterDecision | None = None

    async def publish_filter_trace(
        self,
        manifest: ProcessingInputManifest,
        decision: FilterDecision,
    ) -> ProcessingArtifactRef:
        del manifest
        self.filter_decision = decision
        return _artifact_ref(ProcessingArtifactKind.FILTER_TRACE, "filter")

    async def publish_p204_result(
        self,
        result: P204ResultArtifact,
    ) -> ProcessingArtifactRef:
        self.result = result
        return _artifact_ref(ProcessingArtifactKind.P2_04_RESULT, "p204")


class _FixedSemanticResolver:
    def __init__(self) -> None:
        self.requirement_calls = 0
        self.evidence_calls = 0

    async def resolve_requirements(
        self,
        vacancy: NormalizedVacancy,
        *,
        extraction_version: str,
    ) -> ProcessingArtifactRef:
        del vacancy, extraction_version
        self.requirement_calls += 1
        return _artifact_ref(ProcessingArtifactKind.REQUIREMENT_SET, "requirements")

    async def resolve_evidence(
        self,
        resume: NormalizedResume,
        *,
        evidence_version: str,
    ) -> ProcessingArtifactRef:
        del resume, evidence_version
        self.evidence_calls += 1
        return _artifact_ref(ProcessingArtifactKind.RESUME_EVIDENCE_SET, "evidence")


def _job(manifest: ProcessingInputManifest) -> ProcessingJobRecord:
    now = datetime.now(UTC)
    return ProcessingJobRecord(
        id=uuid4(),
        vacancy_id=1,
        binding_id=2,
        binding_version=manifest.binding.binding_version,
        input_fingerprint=manifest.input_fingerprint(),
        input_manifest_uri=(
            "s3://careerops-artifacts/processing/input_manifest/"
            f"{manifest.input_fingerprint()}.json"
        ),
        pipeline_version=manifest.versions.pipeline_version,
        policy_version=manifest.target_policy.policy_version,
        status=ProcessingJobStatus.CLAIMED,
        attempt_count=1,
        lease_owner="worker-a",
        lease_token=uuid4(),
        leased_at=now,
        lease_expires_at=now + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_p204_executor_uses_semantic_resolver_for_keep() -> None:
    manifest = _manifest()
    publisher = _PairPublisher()
    semantic_resolver = _FixedSemanticResolver()
    executor = P204Executor(
        loader=_Loader(manifest, _vacancy(), _resume()),
        publisher=publisher,
        semantic_resolver=semantic_resolver,
    )

    execution = await executor.execute(_job(manifest))

    assert execution.result_artifact_uri is not None
    assert publisher.filter_decision is not None
    assert publisher.filter_decision.outcome is FilterOutcome.KEEP
    assert publisher.result is not None
    assert publisher.result.filter_outcome is FilterOutcome.KEEP
    assert semantic_resolver.requirement_calls == 1
    assert semantic_resolver.evidence_calls == 1


@pytest.mark.asyncio
async def test_p204_executor_short_circuits_semantics_on_exclusion() -> None:
    manifest = _manifest(frontend_exclusion=True)
    publisher = _PairPublisher()
    semantic_resolver = _FixedSemanticResolver()
    executor = P204Executor(
        loader=_Loader(manifest, _vacancy(title="Frontend Developer"), _resume()),
        publisher=publisher,
        semantic_resolver=semantic_resolver,
    )

    execution = await executor.execute(_job(manifest))

    assert execution.result_artifact_uri is not None
    assert publisher.filter_decision is not None
    assert publisher.filter_decision.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert publisher.result is not None
    assert publisher.result.requirement_set_ref is None
    assert publisher.result.resume_evidence_set_ref is None
    assert semantic_resolver.requirement_calls == 0
    assert semantic_resolver.evidence_calls == 0


def test_p204_result_requires_exact_semantic_artifact_schemas() -> None:
    manifest = _manifest()
    bad_requirement_ref = ProcessingArtifactRef(
        kind=ProcessingArtifactKind.REQUIREMENT_SET,
        schema_version="careerops.processing.requirement-set.unknown",
        uri="s3://careerops-artifacts/bad.json",
        sha256=HASH_A,
        size_bytes=1,
    )
    with pytest.raises(ValidationError, match="schema version"):
        P204ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=FilterOutcome.KEEP,
            filter_trace_ref=_artifact_ref(ProcessingArtifactKind.FILTER_TRACE, "filter"),
            requirement_extraction_version=manifest.versions.requirement_extraction_version,
            evidence_version=manifest.versions.evidence_version,
            requirement_set_ref=bad_requirement_ref,
            resume_evidence_set_ref=_artifact_ref(
                ProcessingArtifactKind.RESUME_EVIDENCE_SET,
                "evidence",
            ),
        )


class _WorkerStore:
    def __init__(self, job: ProcessingJobRecord) -> None:
        self.job = job
        self.claimed = False
        self.result_uri: str | None = None

    async def reconcile_current(self, spec: ProcessingWorkSpec) -> UUID:
        del spec
        return self.job.id

    async def withdraw_pair(
        self,
        pair: ProcessingPairKey,
        *,
        reason: str = RECONCILIATION_WITHDRAWN,
    ) -> int:
        del pair, reason
        return 0

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> ProcessingJobRecord | None:
        del worker_id, lease_seconds
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    async def mark_running(self, job: ProcessingJobRecord) -> None:
        del job

    async def renew_lease(
        self,
        job: ProcessingJobRecord,
        *,
        lease_seconds: int = 300,
    ) -> None:
        del job, lease_seconds

    async def succeed(
        self,
        job: ProcessingJobRecord,
        *,
        result_artifact_uri: str,
    ) -> None:
        del job
        self.result_uri = result_artifact_uri

    async def defer(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        raise AssertionError((job, error_category, next_attempt_at))

    async def retryable_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
        next_attempt_at: datetime,
    ) -> None:
        raise AssertionError((job, error_category, next_attempt_at))

    async def terminal_failure(
        self,
        job: ProcessingJobRecord,
        *,
        error_category: str,
    ) -> None:
        raise AssertionError((job, error_category))

    async def cancel(
        self,
        job: ProcessingJobRecord,
        *,
        reason: str = "operator.cancelled",
    ) -> None:
        raise AssertionError((job, reason))


@pytest.mark.asyncio
async def test_processing_worker_executes_concrete_p204_executor() -> None:
    manifest = _manifest()
    job = _job(manifest)
    store = _WorkerStore(job)
    publisher = _PairPublisher()
    semantic_resolver = _FixedSemanticResolver()
    executor = P204Executor(
        loader=_Loader(manifest, _vacancy(), _resume()),
        publisher=publisher,
        semantic_resolver=semantic_resolver,
    )
    worker = ProcessingWorker(store=store, executor=executor, worker_id="worker-a")

    assert await worker.run_one() is True
    assert store.result_uri is not None
    assert publisher.result is not None
    assert publisher.result.filter_outcome is FilterOutcome.KEEP


class _JsonStore:
    def __init__(self, payload: object, sha256: str) -> None:
        self.payload = payload
        self.sha256 = sha256

    async def get_json_with_metadata(self, uri: str) -> tuple[object, Any]:
        from careerops_storage.s3 import S3ObjectRef

        return (
            self.payload,
            S3ObjectRef(
                bucket="careerops-artifacts",
                key=uri,
                sha256=self.sha256,
                size_bytes=1,
            ),
        )


@pytest.mark.asyncio
async def test_s3_input_loader_verifies_manifest_content_address() -> None:
    manifest = _manifest()
    payload = manifest.model_dump(mode="json")
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    sha = hashlib.sha256(body).hexdigest()
    loader = S3ProcessingInputLoader(
        manifest_store=_JsonStore(payload, sha),  # type: ignore[arg-type]
        normalized_store=_JsonStore({}, HASH_A),  # type: ignore[arg-type]
    )

    loaded = await loader.load_manifest(
        f"s3://careerops-artifacts/processing/input_manifest/v1/{sha}.json"
    )
    assert loaded == manifest

    bad_loader = S3ProcessingInputLoader(
        manifest_store=_JsonStore(payload, HASH_A),  # type: ignore[arg-type]
        normalized_store=_JsonStore({}, HASH_A),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="content hash"):
        await bad_loader.load_manifest(
            f"s3://careerops-artifacts/processing/input_manifest/v1/{sha}.json"
        )


def test_processing_runtime_config_has_no_future_stage_dependencies() -> None:
    config = ProcessingRuntimeConfig.from_env(
        {
            "CAREEROPS_PROCESSING_POSTGRES_DSN": "postgresql://test:test@localhost/test",
            "CAREEROPS_PROCESSING_S3_ENDPOINT_URL": "http://localhost:8333",
            "CAREEROPS_PROCESSING_S3_ACCESS_KEY": "key",
            "CAREEROPS_PROCESSING_S3_SECRET_KEY": "secret",
            "CAREEROPS_PROCESSING_NORMALIZED_BUCKET": "careerops-lake",
            "CAREEROPS_PROCESSING_ARTIFACTS_BUCKET": "careerops-artifacts",
        }
    )

    assert config.worker_lease_seconds == 300
    assert config.worker_idle_sleep_seconds == 1.0
    assert not hasattr(config, "reranker_url")
    assert not hasattr(config, "matching_core_target")
