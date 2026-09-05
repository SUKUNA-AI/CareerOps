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
from .transport import (
    HHApplicantToolTransport,
    HHReadTransport,
    HHResumeListPageRequest,
    HHSearchPageRequest,
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
    "HHTransportError",
    "RawObjectCollisionError",
    "RawWriteVerificationError",
    "default_failure_disposition",
]
