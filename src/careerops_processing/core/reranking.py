"""Детерминированное представление P2-04 contracts для reranker P2-05"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from careerops_processing.contracts import Requirement, ResumeEvidence


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def render_requirement_for_reranker(requirement: Requirement) -> str:
    """Рендерит requirement без provenance и случайных runtime-полей"""

    payload = {
        "kind": requirement.kind.value,
        "statement": requirement.statement,
        "subjects": [
            {
                "text": item.text,
                "normalized": item.normalized,
                "dictionary_key": item.dictionary_key,
            }
            for item in requirement.subjects
        ],
        "activity": requirement.activity,
        "context": requirement.context.value,
        "importance": requirement.importance.value,
        "modality": requirement.modality.value,
        "polarity": requirement.polarity.value,
        "threshold": (
            requirement.threshold.model_dump(mode="json")
            if requirement.threshold is not None
            else None
        ),
    }
    return _canonical_json(payload)


def render_evidence_for_reranker(evidence: ResumeEvidence) -> str:
    """Рендерит evidence без provenance и случайных runtime-полей"""

    payload = {
        "kind": evidence.kind.value,
        "statement": evidence.statement,
        "subjects": [
            {
                "text": item.text,
                "normalized": item.normalized,
                "dictionary_key": item.dictionary_key,
            }
            for item in evidence.subjects
        ],
        "activity": evidence.activity,
        "actor_scope": evidence.actor_scope.value,
        "context": evidence.context.value,
        "polarity": evidence.polarity.value,
        "strength": evidence.strength.value,
        "time_span": (
            evidence.time_span.model_dump(mode="json")
            if evidence.time_span is not None
            else None
        ),
    }
    return _canonical_json(payload)


def evidence_pool_render_sha256(
    rendered_evidence: Iterable[tuple[str, str]],
) -> str:
    """Хеширует точный порядок evidence_id и текстов, передаваемых reranker"""

    payload = [
        {"evidence_id": evidence_id, "document": document}
        for evidence_id, document in rendered_evidence
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
