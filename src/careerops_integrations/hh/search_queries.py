"""Canonical HH search query definitions for the lossless scheduler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .configuration import DiscoveryConfig, DiscoveryQuery


@dataclass(frozen=True, slots=True)
class SearchQueryDefinition:
    """One immutable logical version of an HH search query."""

    query_key: str
    query_set_key: str
    request_params: dict[str, Any]
    query_signature: str


def build_search_query_definition(
    query: DiscoveryQuery,
    discovery: DiscoveryConfig,
) -> SearchQueryDefinition:
    """Build one canonical query snapshot and its stable SHA-256 signature."""

    defaults = discovery.defaults
    spec = query.spec

    request_params: dict[str, Any] = {
        "text": spec.text,
        "area": spec.area or defaults.area,
        "period": spec.period or defaults.period,
        "order_by": defaults.order_by,
        "per_page": spec.per_page or defaults.per_page,
        "professional_roles": None,
    }

    signature_payload = {
    "query_set_key": query.query_set_key,
    "request_params": request_params,
}

    encoded = json.dumps(
        signature_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    query_signature = hashlib.sha256(encoded).hexdigest()

    return SearchQueryDefinition(
        query_key=spec.key,
        query_set_key=query.query_set_key,
        request_params=request_params,
        query_signature=query_signature,
    )