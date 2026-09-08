"""Общие семантические типы P2-04 для requirements и resume evidence"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import model_validator

from .common import FrozenModel, NonEmptyStr, SourceTextRef


class SemanticPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class SemanticSubject(FrozenModel):
    """Предмет требования или evidence без matching decision"""

    text: NonEmptyStr
    normalized: NonEmptyStr
    dictionary_key: NonEmptyStr | None = None


class SemanticSourceRef(FrozenModel):
    """Ссылка из semantic unit обратно в normalized field или source text"""

    source_path: NonEmptyStr
    rendered_value: NonEmptyStr
    entry_id: NonEmptyStr | None = None
    block_id: NonEmptyStr | None = None
    source_text: SourceTextRef | None = None


class SemanticTimeSpan(FrozenModel):
    """Временной интервал evidence без агрегации"""

    start_date: date | None = None
    end_date: date | None = None
    currently_active: bool | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> SemanticTimeSpan:
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("semantic time span end_date must be >= start_date")
        return self
