"""JSON shaping for the HTTP layer: PortEnum -> its symbolic code string.

``opt.types`` models carry ``PortEnum`` fields all over (``QuoteResult``
itself, ``PortCheck``, and -- many levels deep -- ``OptimizerRecommendation``
via voyage assignments, repositioning actions, fleet-mix transshipment hubs).
Pydantic's default JSON encoding of a plain ``Enum`` whose *value* is itself a
``Port`` model embeds the entire ``Port`` object (id, max_loa_m, ...) in place
of a port reference -- correct, but not what a frontend wants when it just
needs to say "this is Newcastle" and later send that same code back in a
request. Rather than hand-patch every nested field path in
``OptimizerRecommendation`` (error-prone -- a new PortEnum field added later
would silently keep embedding raw Port objects), this walks the already-
dumped JSON structure generically and replaces any dict that has exactly a
``Port`` model's shape with that port's symbolic ``PortEnum`` member name
(``"NEWCASTLE_AU"``, not the embedded object) -- correct wherever a PortEnum
field exists in the schema, present or future.
"""
from __future__ import annotations

import logging
from typing import Any

from opt.fracture import route_fracture
from opt.network import PortEnum
from opt.types import QuoteEnvelope, QuoteResult
from opt.war_risk import listed_areas_on_route

__all__ = ["model_to_json", "quote_envelope_to_json", "quote_result_to_json"]

LOGGER = logging.getLogger(__name__)

_PORT_FIELDS = frozenset({
    "id", "max_loa_m", "max_beam_m", "max_draft_m",
    "max_dwt", "handling_rate_tph", "expected_wait_days", "bunker_price_usd",
})
_PORT_CODE_BY_ID: dict[str, str] = {p.value.id: p.name for p in PortEnum}


def _is_port_dict(d: dict[str, Any]) -> bool:
    return set(d.keys()) == _PORT_FIELDS and d.get("id") in _PORT_CODE_BY_ID


def _replace_ports(obj: Any) -> Any:
    if isinstance(obj, dict):
        if _is_port_dict(obj):
            return _PORT_CODE_BY_ID[obj["id"]]
        return {k: _replace_ports(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_ports(v) for v in obj]
    return obj


def _fracture_summary(result: QuoteResult) -> dict[str, Any] | None:
    """3.4: the route's real Fracture Index, attached to the quote envelope
    so the desk shows it without a second round trip to POST /fracture.
    Deliberately computed here rather than as a QuoteResult field -- this is
    a presentation-layer join of two things ``opt.quote.quote()`` already
    knows (``origin_port``, ``dest_port``, ``as_of``), not a new number the
    domain model itself needs to own. No hull value exists in this context
    (``QuoteRequest`` never asks for one), so this never includes a war-risk
    premium -- ``POST /fracture`` with ``hull_value_usd`` is the way to get
    that; a plain quote's summary is deliberately Listed Areas + the per-
    chokepoint index only, and stays ``None`` (never a fabricated empty
    list) if the real underlying computation itself fails."""
    try:
        chokepoints = route_fracture(result.origin_port, result.dest_port, result.as_of)
        listed_areas = listed_areas_on_route(result.origin_port, result.dest_port)
    except Exception:
        LOGGER.warning("Fracture summary unavailable for this quote's route.", exc_info=True)
        return None
    return {
        "chokepoints": [c.model_dump(mode="json") for c in chokepoints],
        "jwc_listed_areas": list(listed_areas),
    }


def quote_result_to_json(result: QuoteResult) -> dict[str, Any]:
    """A QuoteResult, JSON-shaped for the API: every PortEnum reference
    becomes its symbolic code (e.g. "NEWCASTLE_AU") instead of an embedded
    Port object -- the exact string a caller can pass back as
    ``origin_port``/``dest_port`` on the next request. Also attaches the
    route's real Fracture Index summary under ``fracture`` -- see
    ``_fracture_summary``."""
    raw = result.model_dump(mode="json")
    raw["fracture"] = _fracture_summary(result)
    return _replace_ports(raw)


def quote_envelope_to_json(envelope: QuoteEnvelope) -> dict[str, Any]:
    """A QuoteEnvelope, JSON-shaped for the API with the same PortEnum ->
    symbolic-code flattening ``quote_result_to_json`` does (the generic walk
    covers the nested ``quote`` and every ``route_exploration`` /
    ``structural_problems`` model without per-field handling), including the
    ``quote.fracture`` summary it attaches."""
    raw = envelope.model_dump(mode="json")
    if raw.get("quote") is not None:
        raw["quote"]["fracture"] = _fracture_summary(envelope.quote)
    return _replace_ports(raw)


def model_to_json(model: Any) -> dict[str, Any]:
    """P2: the same generic PortEnum -> symbolic-code flattening, for any
    Pydantic model with a PortEnum field (PortRealityReport, and the P2
    berth-truth response models) -- reuses ``_replace_ports`` rather than
    hand-writing per-endpoint port handling again."""
    raw = model.model_dump(mode="json")
    return _replace_ports(raw)
