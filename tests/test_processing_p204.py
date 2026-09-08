from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from careerops_processing.contracts import (
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
    FilterDecision,
    FilterOutcome,
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
    RequirementGroup,
    RequirementGroupOperator,
    RequirementImportance,
    RequirementKind,
    RequirementSet,
    ResumeEvidenceSet,
    SemanticPolarity,
    SemanticSourceRef,
    SourceLabel,
    SourceTextRef,
    SourceValue,
    TargetPolicy,
    TextBlock,
    ValueState,
)
from careerops_processing.core import extract_requirements, extract_resume_evidence
from careerops_processing.executor import P204Executor
from careerops_processing.infrastructure import (
    ProcessingArtifactPublisher,
    ProcessingArtifactStore,
    ProcessingArtifactStoreSettings,
)
from careerops_processing.queue import (
    RECONCILIATION_WITHDRAWN,
    ProcessingJobRecord,
    ProcessingJobStatus,
    ProcessingPairKey,
    ProcessingWorkSpec,
)
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
        experience=_missing(),
        employment=(),
        schedules=(),
        work_formats=(),
        location=_missing(),
        relocation_facts=(),
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


def _resume() -> NormalizedResume:
    experience_block = _text_block(
        block_id="exp-1",
        text="Разработал Kafka consumer\nНе работал с Kubernetes",
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
        skill_set=(SourceLabel(key="kafka", label="Kafka"),),
        about=_known("Команда использовала Spark"),
        experience_entries=(
            ExperienceEntry(
                entry_id="exp-entry-1",
                employer_name=_known("Example"),
                position=_known("Data Engineer"),
                start_date=_known(date(2024, 1, 1)),
                end_date=_known(date(2025, 1, 1)),
                currently_active=_known(False),
                description_blocks=(experience_block,),
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
        location=_missing(),
        work_preferences=_missing(),
        relocation=_missing(),
        business_trips=_missing(),
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
            requirement_extraction_version="requirements-v1",
            evidence_version="evidence-v1",
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


def _artifact_ref(kind: ProcessingArtifactKind, suffix: str) -> ProcessingArtifactRef:
    return ProcessingArtifactRef(
        kind=kind,
        schema_version=f"schema-{suffix}",
        uri=f"s3://careerops-artifacts/processing/{suffix}/{'f' * 64}.json",
        sha256="f" * 64,
        size_bytes=10,
    )


def test_requirement_set_rejects_duplicate_ids_and_cycles() -> None:
    duplicate = _requirement("req-1")
    with pytest.raises(ValidationError, match="unique"):
        RequirementSet(
            source_key="hh",
            source_entity_id="vacancy-42",
            semantic_content_hash=HASH_C,
            normalized_schema_version="vacancy-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            extraction_version="requirements-v1",
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

    with pytest.raises(ValidationError, match="acyclic"):
        RequirementSet(
            source_key="hh",
            source_entity_id="vacancy-42",
            semantic_content_hash=HASH_C,
            normalized_schema_version="vacancy-v1",
            normalization_version="normalizer-v1",
            dictionary_version="dict-v1",
            extraction_version="requirements-v1",
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
                    operator=RequirementGroupOperator.ANY,
                    child_group_ids=("root",),
                ),
            ),
            root_group_id="root",
        )


def test_requirement_extractor_preserves_logic_and_semantics() -> None:
    block = _text_block(
        block_id="requirements",
        heading="Требования",
        section_hint="requirements",
        text=(
            "Python или Scala, опыт от 3 лет\n"
            "Kafka будет плюсом\n"
            "Если работа с потоками: Spark обязателен\n"
            "Kubernetes не требуется"
        ),
    )
    result = extract_requirements(
        _vacancy(text_blocks=(block,)),
        extraction_version="requirements-v1",
    )

    any_groups = [
        group
        for group in result.groups
        if group.operator is RequirementGroupOperator.ANY
    ]
    assert len(any_groups) == 1
    any_requirements = {
        item.requirement_id: item
        for item in result.requirements
        if item.requirement_id in any_groups[0].requirement_ids
    }
    assert {
        subject.normalized
        for item in any_requirements.values()
        for subject in item.subjects
    } == {"python", "scala"}
    assert {item.minimum_experience_years for item in any_requirements.values()} == {
        Decimal("3")
    }
    assert all(
        item.importance is RequirementImportance.MANDATORY
        for item in any_requirements.values()
    )

    kafka = next(item for item in result.requirements if "Kafka" in item.statement)
    assert kafka.importance is RequirementImportance.PREFERRED

    conditional = next(
        group
        for group in result.groups
        if group.operator is RequirementGroupOperator.CONDITIONAL
    )
    assert conditional.condition == "работа с потоками"

    kubernetes = next(
        item for item in result.requirements if "Kubernetes" in item.statement
    )
    assert kubernetes.polarity is SemanticPolarity.NEGATIVE


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
    first = extract_requirements(vacancy, extraction_version="requirements-v1")
    replay = extract_requirements(vacancy, extraction_version="requirements-v1")
    changed = extract_requirements(vacancy, extraction_version="requirements-v2")

    assert first == replay
    assert [item.requirement_id for item in first.requirements] != [
        item.requirement_id for item in changed.requirements
    ]


def test_resume_evidence_preserves_scope_polarity_strength_and_time() -> None:
    result = extract_resume_evidence(_resume(), evidence_version="evidence-v1")

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

    project = next(
        item for item in result.evidence if item.statement == "Создал ETL pipeline на Python"
    )
    assert project.kind is EvidenceKind.PROJECT
    assert project.context is EvidenceContext.PROJECT


def test_resume_evidence_is_deterministic_and_versioned() -> None:
    resume = _resume()
    first = extract_resume_evidence(resume, evidence_version="evidence-v1")
    replay = extract_resume_evidence(resume, evidence_version="evidence-v1")
    changed = extract_resume_evidence(resume, evidence_version="evidence-v2")

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
                _text_block(
                    block_id="requirements",
                    heading="Требования",
                    text="Python",
                ),
            )
        ),
        extraction_version="requirements-v1",
    )
    evidence = extract_resume_evidence(_resume(), evidence_version="evidence-v1")

    req_ref = await publisher.publish_requirement_set(requirements)
    req_replay_ref = await publisher.publish_requirement_set(requirements)
    evidence_ref = await publisher.publish_resume_evidence_set(evidence)

    assert req_ref == req_replay_ref
    assert req_ref.kind is ProcessingArtifactKind.REQUIREMENT_SET
    assert evidence_ref.kind is ProcessingArtifactKind.RESUME_EVIDENCE_SET
    assert req_ref.sha256 in req_ref.uri
    assert evidence_ref.sha256 in evidence_ref.uri
    assert client.put_count == 2


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


class _Publisher:
    def __init__(self) -> None:
        self.requirements: RequirementSet | None = None
        self.evidence: ResumeEvidenceSet | None = None
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

    async def publish_requirement_set(
        self,
        requirement_set: RequirementSet,
    ) -> ProcessingArtifactRef:
        self.requirements = requirement_set
        return _artifact_ref(ProcessingArtifactKind.REQUIREMENT_SET, "requirements")

    async def publish_resume_evidence_set(
        self,
        evidence_set: ResumeEvidenceSet,
    ) -> ProcessingArtifactRef:
        self.evidence = evidence_set
        return _artifact_ref(ProcessingArtifactKind.RESUME_EVIDENCE_SET, "evidence")

    async def publish_p204_result(
        self,
        result: P204ResultArtifact,
    ) -> ProcessingArtifactRef:
        self.result = result
        return _artifact_ref(ProcessingArtifactKind.P2_04_RESULT, "p204")


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
async def test_p204_executor_runs_keep_path_and_publishes_checkpoint() -> None:
    manifest = _manifest()
    publisher = _Publisher()
    executor = P204Executor(
        loader=_Loader(manifest, _vacancy(), _resume()),
        publisher=publisher,
    )

    execution = await executor.execute(_job(manifest))

    assert execution.result_artifact_uri is not None
    assert execution.result_artifact_uri.endswith("/" + "f" * 64 + ".json")
    assert publisher.filter_decision is not None
    assert publisher.filter_decision.outcome is FilterOutcome.KEEP
    assert publisher.requirements is not None
    assert publisher.evidence is not None
    assert publisher.result is not None
    assert publisher.result.filter_outcome is FilterOutcome.KEEP


@pytest.mark.asyncio
async def test_p204_executor_short_circuits_semantic_extraction_on_exclusion() -> None:
    manifest = _manifest(frontend_exclusion=True)
    publisher = _Publisher()
    executor = P204Executor(
        loader=_Loader(manifest, _vacancy(title="Frontend Developer"), _resume()),
        publisher=publisher,
    )

    execution = await executor.execute(_job(manifest))

    assert execution.result_artifact_uri is not None
    assert publisher.filter_decision is not None
    assert publisher.filter_decision.outcome is FilterOutcome.EXCLUDE_PROVEN
    assert publisher.requirements is None
    assert publisher.evidence is None
    assert publisher.result is not None
    assert publisher.result.requirement_set_ref is None
    assert publisher.result.resume_evidence_set_ref is None


def test_p204_result_requires_semantic_artifacts_for_keep() -> None:
    manifest = _manifest()
    with pytest.raises(ValidationError, match="KEEP"):
        P204ResultArtifact(
            input_fingerprint=manifest.input_fingerprint(),
            manifest=manifest,
            filter_outcome=FilterOutcome.KEEP,
            filter_trace_ref=_artifact_ref(ProcessingArtifactKind.FILTER_TRACE, "filter"),
            requirement_extraction_version=manifest.versions.requirement_extraction_version,
            evidence_version=manifest.versions.evidence_version,
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
    publisher = _Publisher()
    executor = P204Executor(
        loader=_Loader(manifest, _vacancy(), _resume()),
        publisher=publisher,
    )
    worker = ProcessingWorker(
        store=store,
        executor=executor,
        worker_id="worker-a",
    )

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
    import json

    from careerops_processing.infrastructure import S3ProcessingInputLoader

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
