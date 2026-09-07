"""Public immutable contracts for the CareerOPS Processing v2 boundary."""

from .common import (
    DataQualityStatus,
    EntityType,
    NormalizedRef,
    RawObservationRef,
    SourceLabel,
    SourceTextRef,
    SourceValue,
    TextBlock,
    ValueState,
)
from .compatibility import validate_bundle_for_ref
from .manifest import MANIFEST_SCHEMA_VERSION, ProcessingInputManifest
from .normalized import (
    DataQualityReport,
    EducationEntry,
    Employer,
    ExperienceEntry,
    ExperienceRange,
    LanguageEntry,
    Location,
    NormalizedResume,
    NormalizedVacancy,
    ProjectEntry,
    Salary,
    WorkPreferences,
)
from .policy import BindingSnapshot, TargetPolicy
from .reasons import INITIAL_REASON_CODES, ReasonCode, ReasonNamespace
from .versions import JinaVersionBundle, ProcessingVersionBundle

__all__ = [
    "BindingSnapshot",
    "DataQualityReport",
    "DataQualityStatus",
    "EducationEntry",
    "Employer",
    "EntityType",
    "ExperienceEntry",
    "ExperienceRange",
    "INITIAL_REASON_CODES",
    "JinaVersionBundle",
    "LanguageEntry",
    "Location",
    "MANIFEST_SCHEMA_VERSION",
    "NormalizedRef",
    "NormalizedResume",
    "NormalizedVacancy",
    "ProcessingInputManifest",
    "ProcessingVersionBundle",
    "ProjectEntry",
    "RawObservationRef",
    "ReasonCode",
    "ReasonNamespace",
    "Salary",
    "SourceLabel",
    "SourceTextRef",
    "SourceValue",
    "TargetPolicy",
    "TextBlock",
    "ValueState",
    "WorkPreferences",
    "validate_bundle_for_ref",
]
