"""Final Spark-to-Processing normalized entity contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from .common import (
    DataQualityStatus,
    FrozenModel,
    NonEmptyStr,
    RawObservationRef,
    Sha256,
    SourceLabel,
    SourceValue,
    TextBlock,
    VersionId,
)


class DataQualityReport(FrozenModel):
    """Technical completeness of one normalized entity version."""

    status: DataQualityStatus
    full_entity_available: bool
    parse_complete: bool
    omitted_fields: tuple[NonEmptyStr, ...] = ()
    unknown_enums: tuple[NonEmptyStr, ...] = ()
    invalid_dates: tuple[NonEmptyStr, ...] = ()
    conflicting_fields: tuple[NonEmptyStr, ...] = ()
    unresolved_sections: tuple[NonEmptyStr, ...] = ()
    source_truncated: bool = False
    normalization_warnings: tuple[NonEmptyStr, ...] = ()

    @property
    def processing_ready(self) -> bool:
        return (
            self.status is not DataQualityStatus.BLOCKED
            and self.full_entity_available
            and self.parse_complete
            and not self.source_truncated
        )


class Employer(FrozenModel):
    source_employer_id: NonEmptyStr | None = None
    name: NonEmptyStr | None = None


class ExperienceRange(FrozenModel):
    source_code: NonEmptyStr | None = None
    minimum_years: Decimal | None = Field(default=None, ge=0)
    maximum_years: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> ExperienceRange:
        if (
            self.minimum_years is not None
            and self.maximum_years is not None
            and self.maximum_years < self.minimum_years
        ):
            raise ValueError("maximum_years must be >= minimum_years")
        return self


class Salary(FrozenModel):
    amount_from: Decimal | None = Field(default=None, ge=0)
    amount_to: Decimal | None = Field(default=None, ge=0)
    currency: NonEmptyStr
    gross: bool | None = None

    @model_validator(mode="after")
    def validate_range(self) -> Salary:
        if (
            self.amount_from is not None
            and self.amount_to is not None
            and self.amount_to < self.amount_from
        ):
            raise ValueError("salary amount_to must be >= amount_from")
        return self


class Location(FrozenModel):
    area_id: NonEmptyStr | None = None
    area_name: NonEmptyStr | None = None
    address: NonEmptyStr | None = None
    metro: tuple[NonEmptyStr, ...] = ()
    source_facts: tuple[NonEmptyStr, ...] = ()


class NormalizedVacancy(FrozenModel):
    """Source facts normalized by Spark; no suitability decisions are allowed here."""

    schema_version: VersionId
    normalization_version: VersionId
    dictionary_version: VersionId
    materialization_key: NonEmptyStr

    source_key: NonEmptyStr
    source_entity_id: NonEmptyStr
    raw: RawObservationRef
    semantic_content_hash: Sha256

    title: SourceValue[NonEmptyStr]
    employer: SourceValue[Employer]
    professional_roles: tuple[SourceLabel, ...] = ()
    key_skills: tuple[SourceLabel, ...] = ()
    experience: SourceValue[ExperienceRange]
    employment: tuple[SourceLabel, ...] = ()
    schedules: tuple[SourceLabel, ...] = ()
    work_formats: tuple[SourceLabel, ...] = ()
    location: SourceValue[Location]
    relocation_facts: tuple[NonEmptyStr, ...] = ()
    salary: SourceValue[Salary]
    archived: SourceValue[bool]
    closed_for_applicants: SourceValue[bool]
    published_at: SourceValue[datetime]
    text_blocks: tuple[TextBlock, ...] = ()
    dq: DataQualityReport

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value: SourceValue[datetime]) -> SourceValue[datetime]:
        if value.value is not None and (
            value.value.tzinfo is None or value.value.utcoffset() is None
        ):
            raise ValueError("published_at must be timezone-aware when known")
        return value

    @model_validator(mode="after")
    def validate_unique_blocks(self) -> NormalizedVacancy:
        block_ids = [block.block_id for block in self.text_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("text block ids must be unique within a vacancy version")
        return self


class ExperienceEntry(FrozenModel):
    entry_id: NonEmptyStr
    employer_name: SourceValue[NonEmptyStr]
    position: SourceValue[NonEmptyStr]
    start_date: SourceValue[date]
    end_date: SourceValue[date]
    currently_active: SourceValue[bool]
    description_blocks: tuple[TextBlock, ...] = ()

    @model_validator(mode="after")
    def validate_dates(self) -> ExperienceEntry:
        if self.start_date.value is not None and self.end_date.value is not None:
            if self.end_date.value < self.start_date.value:
                raise ValueError("experience end_date must be >= start_date")
        block_ids = [block.block_id for block in self.description_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("experience text block ids must be unique within an entry")
        return self


class ProjectEntry(FrozenModel):
    project_id: NonEmptyStr
    name: SourceValue[NonEmptyStr]
    role: SourceValue[NonEmptyStr]
    start_date: SourceValue[date]
    end_date: SourceValue[date]
    description_blocks: tuple[TextBlock, ...] = ()

    @model_validator(mode="after")
    def validate_project(self) -> ProjectEntry:
        if self.start_date.value is not None and self.end_date.value is not None:
            if self.end_date.value < self.start_date.value:
                raise ValueError("project end_date must be >= start_date")
        block_ids = [block.block_id for block in self.description_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("project text block ids must be unique within a project")
        return self


class EducationEntry(FrozenModel):
    entry_id: NonEmptyStr
    organization: SourceValue[NonEmptyStr]
    degree: SourceValue[NonEmptyStr]
    field: SourceValue[NonEmptyStr]
    graduation_year: SourceValue[int]


class LanguageEntry(FrozenModel):
    language: NonEmptyStr
    level: SourceValue[NonEmptyStr]


class WorkPreferences(FrozenModel):
    locations: tuple[NonEmptyStr, ...] = ()
    work_formats: tuple[NonEmptyStr, ...] = ()
    employment: tuple[NonEmptyStr, ...] = ()
    schedules: tuple[NonEmptyStr, ...] = ()


class NormalizedResume(FrozenModel):
    """Full normalized resume version consumed by Processing v2."""

    schema_version: VersionId
    normalization_version: VersionId
    dictionary_version: VersionId
    materialization_key: NonEmptyStr

    source_key: NonEmptyStr
    account_key: NonEmptyStr
    source_entity_id: NonEmptyStr
    raw: RawObservationRef
    semantic_content_hash: Sha256

    headline: SourceValue[NonEmptyStr]
    skill_set: tuple[SourceLabel, ...] = ()
    about: SourceValue[NonEmptyStr]
    experience_entries: tuple[ExperienceEntry, ...] = ()
    projects: tuple[ProjectEntry, ...] = ()
    education: tuple[EducationEntry, ...] = ()
    languages: tuple[LanguageEntry, ...] = ()
    location: SourceValue[Location]
    work_preferences: SourceValue[WorkPreferences]
    relocation: SourceValue[bool]
    business_trips: SourceValue[bool]
    total_experience_years: SourceValue[Decimal]
    dq: DataQualityReport

    @model_validator(mode="after")
    def validate_unique_entities(self) -> NormalizedResume:
        experience_ids = [entry.entry_id for entry in self.experience_entries]
        if len(experience_ids) != len(set(experience_ids)):
            raise ValueError("experience entry ids must be unique within a resume version")
        project_ids = [entry.project_id for entry in self.projects]
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("project ids must be unique within a resume version")
        education_ids = [entry.entry_id for entry in self.education]
        if len(education_ids) != len(set(education_ids)):
            raise ValueError("education entry ids must be unique within a resume version")
        return self
