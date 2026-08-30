"""Two kinds of "no answer", told apart.

When the optimizer cannot recommend a charter, the reason is one of two very
different things, and a caller needs to know which:

- **Structural** -- the request itself is impossible. A billion tonnes of coal
  cannot move as one lot; a port pair with no sea route cannot be sailed; a
  laycan that has already closed cannot be booked. No amount of loosening
  constraints changes that, so the honest response is to point at the specific
  input that is wrong. ``check_structural`` runs *before* the heavy solve.

- **Contingent** -- the request is reasonable, but every vessel configuration
  was ruled out under the constraints as given (a too-tight laycan, a port the
  natural class cannot enter, a chokepoint showing disruption). Here the useful
  response is to loosen the binding constraint, solve the nearby problem, and
  tell the caller exactly what was changed. ``blocking_reasons`` detects it
  after a solve; the relaxation loop lives in ``opt.quote.quote_envelope``.
"""
from __future__ import annotations

from datetime import date

from opt.network import PortEnum
from opt.present import estimate_transit_days
from opt.types import QuoteResult, StructuralProblem

__all__ = ["MAX_SANE_CAPESIZE_LOADS", "blocking_reasons", "check_structural"]

#: A Capesize lifts ~180,000 dwt. More than this many loads for one cargo lot is
#: not a chartering problem, it is a request to split the cargo.
_CAPESIZE_REF_DWT = 180_000.0
MAX_SANE_CAPESIZE_LOADS = 200
_MAX_TERM_DAYS = 366 * 5  # no real time charter runs longer than ~5 years


def check_structural(
    *,
    cargo_volume_dwt: float,
    origin_port: PortEnum,
    dest_port: PortEnum,
    laycan_end: date,
    as_of: date,
    contract_term_days: int,
) -> tuple[StructuralProblem, ...]:
    """Reasons the request cannot be satisfied at all. Empty tuple means the
    request is well-formed enough to hand to the solver."""
    problems: list[StructuralProblem] = []

    if cargo_volume_dwt > 0:
        loads = -(-cargo_volume_dwt // _CAPESIZE_REF_DWT)  # ceil
        if loads > MAX_SANE_CAPESIZE_LOADS:
            problems.append(
                StructuralProblem(
                    field="cargo_volume_dwt",
                    message=(
                        "This is more cargo than the Capesize market could lift as a single "
                        "lot. Book it as separate shipments."
                    ),
                    observed=f"{cargo_volume_dwt:,.0f} dwt (about {loads:,.0f} Capesize loads)",
                    limit=(
                        f"about {MAX_SANE_CAPESIZE_LOADS} Capesize loads "
                        f"({MAX_SANE_CAPESIZE_LOADS * _CAPESIZE_REF_DWT:,.0f} dwt)"
                    ),
                )
            )

    if estimate_transit_days(origin_port, dest_port) is None:
        problems.append(
            StructuralProblem(
                field="route",
                message="No sea route between these ports is on record. Choose ports the network covers.",
                observed=f"{origin_port.value.id} to {dest_port.value.id}",
                limit="a port pair present in the distance matrix",
            )
        )

    if laycan_end < as_of:
        problems.append(
            StructuralProblem(
                field="laycan",
                message="The laycan window closes before the charter date. Move the window forward.",
                observed=f"laycan ends {laycan_end.isoformat()}",
                limit=f"on or after the charter date, {as_of.isoformat()}",
            )
        )

    if contract_term_days > _MAX_TERM_DAYS:
        problems.append(
            StructuralProblem(
                field="contract_term_days",
                message="The contract term is longer than any real time charter. Use a term under five years.",
                observed=f"{contract_term_days} days",
                limit=f"{_MAX_TERM_DAYS} days",
            )
        )

    return tuple(problems)


def blocking_reasons(quote: QuoteResult) -> tuple[str, ...]:
    """Non-empty exactly when a numerically-complete solve is still not
    actionable: the fleet-mix frontier has no feasible configuration. Each
    string is a real, already-computed reason a vessel class was ruled out."""
    frontier = quote.fleet_mix
    if frontier is None:
        return ("The fleet-mix optimiser could not price any vessel configuration for this route.",)
    if frontier.configurations:
        return ()
    reasons = tuple(
        f"{c.vessel_class.value}: {c.infeasible_reason}"
        for c in frontier.rejected_configurations
        if c.infeasible_reason
    )
    return reasons or ("No vessel class can serve this cargo on this route as specified.",)
