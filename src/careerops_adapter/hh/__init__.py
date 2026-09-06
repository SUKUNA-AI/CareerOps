"""HeadHunter adapter boundaries for CareerOPS v2."""

from .errors import (
    HHFailureDisposition,
    HHFailureKind,
    HHTransportError,
    default_failure_disposition,
)
from .raw import (
    HHRawContext,
    HHRawObject,
    HHRawObjectKind,
    HHRawPublisher,
    RawObjectCollisionError,
    RawWriteVerificationError,
)
from .tasks import (
    SourceTaskKind,
    SourceTaskLeaseLost,
    SourceTaskRecord,
    SourceTaskRepository,
    SourceTaskSpec,
    SourceTaskStatus,
    resume_fetch_task,
    resume_sync_task,
    search_page_task,
    vacancy_fetch_task,
)
from .transport import (
    HHApplicantToolTransport,
    HHReadTransport,
    HHResumeListPageRequest,
    HHSearchPageRequest,
)
from .worker import (
    HHSourceFailurePolicy,
    HHSourceTaskExecutor,
    SourceTaskRunOutcome,
    SourceTaskRunResult,
)

__all__ = [
    "HHApplicantToolTransport",
    "HHFailureDisposition",
    "HHFailureKind",
    "HHRawContext",
    "HHRawObject",
    "HHRawObjectKind",
    "HHRawPublisher",
    "HHReadTransport",
    "HHResumeListPageRequest",
    "HHSearchPageRequest",
    "HHSourceFailurePolicy",
    "HHSourceTaskExecutor",
    "HHTransportError",
    "RawObjectCollisionError",
    "RawWriteVerificationError",
    "SourceTaskKind",
    "SourceTaskLeaseLost",
    "SourceTaskRecord",
    "SourceTaskRepository",
    "SourceTaskRunOutcome",
    "SourceTaskRunResult",
    "SourceTaskSpec",
    "SourceTaskStatus",
    "default_failure_disposition",
    "resume_fetch_task",
    "resume_sync_task",
    "search_page_task",
    "vacancy_fetch_task",
]
