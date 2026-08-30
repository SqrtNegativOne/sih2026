"""P4 requirement 5 -- explicit provenance for every PortWatch field,
carried through to API and UI.

PortWatch (IMF's Portwatch, ``raw_data/portwatch/*_daily_portcalls.csv``)
publishes three real columns per port per day. They are NOT all the same
kind of fact:

- ``portcalls_dry_bulk`` -- a real AIS-derived COUNT of dry-bulk vessel
  calls. PortWatch observes vessel positions and port boundaries directly;
  a call either happened or it didn't. OBSERVED.
- ``import_dry_bulk`` / ``export_dry_bulk`` -- PortWatch's own MODEL OUTPUT
  (their published methodology estimates cargo tonnage from AIS draft
  changes and vessel-class capacity assumptions, not a customs manifest or
  a weighbridge reading). Real, useful, and already the best free proxy
  available -- but a model estimate, not a measurement. ESTIMATED.

Everything this codebase derives from either field is, at best,
MODEL_DERIVED (a computation over real inputs) -- never OBSERVED or
ESTIMATED itself, and never presented as if it were the input's own
provenance.
"""
from __future__ import annotations

from enum import Enum
from typing import Final

__all__ = ["PORTWATCH_FIELD_PROVENANCE", "Provenance", "portwatch_field_provenance"]


class Provenance(str, Enum):
    """The P4 API contract's five values, verbatim. Shared across every
    consumer (``opt.repositioning``, ``opt.congestion``, ``opt.risk``,
    ``tonnage/``, the API layer) rather than each defining its own."""

    OBSERVED = "OBSERVED"
    ESTIMATED = "ESTIMATED"
    INFERRED = "INFERRED"
    MODEL_DERIVED = "MODEL_DERIVED"
    DECLARED = "DECLARED"


#: The real PortWatch CSV columns (``raw_data/portwatch/*_daily_portcalls.csv``,
#: read by ``data_builders.build_master.read_portwatch_dir`` into
#: ``PW_<PORT>_CALLS`` / ``PW_<PORT>_IMPORT_T`` / ``PW_<PORT>_EXPORT_T``) and
#: their real provenance, per PortWatch's own published methodology (see
#: this module's docstring).
PORTWATCH_FIELD_PROVENANCE: Final[dict[str, Provenance]] = {
    "portcalls_dry_bulk": Provenance.OBSERVED,
    "import_dry_bulk": Provenance.ESTIMATED,
    "export_dry_bulk": Provenance.ESTIMATED,
}


def portwatch_field_provenance(field: str) -> Provenance:
    """Provenance for a raw PortWatch CSV column name. Raises KeyError for an
    unrecognised field -- silently returning a default here would be exactly
    the kind of un-auditable guess this module exists to prevent."""
    return PORTWATCH_FIELD_PROVENANCE[field]
