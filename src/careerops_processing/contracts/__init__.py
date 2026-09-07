"""Public immutable contracts для CareerOPS Processing v2 boundary"""

from .artifacts import (
    FILTER_TRACE_SCHEMA_VERSION,
    FilterTraceArtifact,
    ProcessingArtifactKind,
    ProcessingArtifactRef,
)
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
from .filtering import (
    FilterDecision,
    FilterEvidence,
    FilterOutcome,
    FilterPolicy,
    ProvenExclusion,
    RoleFamily,
    SeniorityLevel,
    WorkFormat,
)
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
    "FILTER_TRACE_SCHEMA_VERSION",
    "FilterDecision",
    "FilterEvidence",
    "FilterOutcome",
    "FilterPolicy",
    "FilterTraceArtifact",
    "INITIAL_REASON_CODES",
    "JinaVersionBundle",
    "LanguageEntry",
    "Location",
    "MANIFEST_SCHEMA_VERSION",
    "NormalizedRef",
    "NormalizedResume",
    "NormalizedVacancy",
    "ProcessingArtifactKind",
    "ProcessingArtifactRef",
    "ProcessingInputManifest",
    "ProcessingVersionBundle",
    "ProjectEntry",
    "ProvenExclusion",
    "RawObservationRef",
    "ReasonCode",
    "ReasonNamespace",
    "RoleFamily",
    "Salary",
    "SeniorityLevel",
    "SourceLabel",
    "SourceTextRef",
    "SourceValue",
    "TargetPolicy",
    "TextBlock",
    "ValueState",
    "WorkFormat",
    "WorkPreferences",
    "validate_bundle_for_ref",
]
