"""P4 requirements 1/2 -- the route-basis mechanism: real evidence or an
explicit ROUTE_RATE_BASIS_UNAVAILABLE, never a fabricated route premium.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from opt.basis import (
    MASTER_LONG_PATH,
    MIN_ROUTE_OBS,
    ROUTE_FAMILY_TO_SIGNAL_SERIES,
    RouteEvidence,
    build_route_basis_table,
)
from opt.network import PortEnum, RouteFamily
from opt.quote import quote
from opt.types import RouteEvidenceLevel


class TestRealRouteBasisTable:
    """Against the real master_long.parquet on disk."""

    def test_every_route_family_has_a_result(self) -> None:
        table = build_route_basis_table()
        assert set(table) == set(RouteFamily)

    def test_indonesia_evidence_is_real_and_disclosed(self) -> None:
        """Indonesia is the one family with real EC-India route evidence.

        This used to assert the honest dead end: three Signal points, all
        ~$9/**tonne**, none of them convertible to the $/day this module needs,
        so the family resolved ROUTE_RATE_BASIS_UNAVAILABLE like every other.

        `data_builders.harvest_route_rates` now supplies the missing
        denomination -- handybulk publishes this lane daily in dollars per day
        -- so the family resolves to a real basis. What must stay true is that
        the $/tonne points are still not converted: the fit uses only $/day
        observations, and the unconvertible ones remain visible rather than
        being quietly dropped or forced into the fit.
        """
        table = build_route_basis_table()
        result = table[RouteFamily.INDONESIA_EC_INDIA]

        assert result.evidence is not RouteEvidence.ROUTE_RATE_BASIS_UNAVAILABLE
        assert result.basis_entry is not None

        # The fit counts only the usable $/day observations.
        usd_day = [o for o in result.raw_observations if o.series_id.endswith("_USD_DAY")]
        assert usd_day, "the whole point of this family is that a $/day observation now exists"
        assert result.n_real_observations == len(usd_day)
        assert all(o.unit == "usd/day" for o in usd_day)

        # And they come from the route harvester, not from Signal.
        assert all(o.series_id.startswith("HB_") for o in usd_day)

    def test_the_per_tonne_points_are_still_not_converted(self) -> None:
        """The reason they were unusable has not changed: turning a voyage
        $/tonne rate into a $/day TC-equivalent needs cargo-quantity, voyage-
        duration and bunker assumptions this module has no evidence for. A
        newly available $/day series must not be taken as licence to convert
        them."""
        table = build_route_basis_table()
        result = table[RouteFamily.INDONESIA_EC_INDIA]
        per_tonne = [o for o in result.raw_observations if o.unit in ("usd/ton", "usd/tonne")]
        # They may or may not be inside the as-of window; if they are, they
        # must not be counted in the fit.
        assert result.n_real_observations == len(
            [o for o in result.raw_observations if o.series_id.endswith("_USD_DAY")]
        )
        for o in per_tonne:
            assert not o.series_id.endswith("_USD_DAY")

    def test_no_calibration_factor_or_fabricated_premium(self) -> None:
        """Real evidence exists for Indonesia (unit mismatch, thin sample) and
        nowhere else -- both must resolve honestly, not to a forced number."""
        table = build_route_basis_table()
        for result in table.values():
            if result.evidence is RouteEvidence.ROUTE_RATE_BASIS_UNAVAILABLE:
                assert result.basis_entry is None
            else:
                # If evidence ever does clear the bar, the entry must be real
                # (built from real observations, not a hardcoded constant).
                assert result.n_real_observations > 0
                assert result.basis_entry is not None
            assert result.reason  # every result explains itself

    def test_families_with_no_real_observations_are_unavailable(self) -> None:
        """Registering a series id is not the same as having data for it.

        This test used to key off an empty tuple in
        ROUTE_FAMILY_TO_SIGNAL_SERIES. That stopped meaning anything the moment
        every family got the series ids it *would* use once its lane is
        published -- the loop skipped every family and asserted nothing, which
        is worse than failing because it still reports as a pass.

        The real property is about observations, not registrations: a family
        with nothing in master_long must resolve to the honest branch.
        """
        master = pl.read_parquet(MASTER_LONG_PATH)
        present = set(master["series_id"].unique().to_list())
        table = build_route_basis_table()

        checked = 0
        for route_family, series_ids in ROUTE_FAMILY_TO_SIGNAL_SERIES.items():
            if any(sid in present for sid in series_ids):
                continue
            checked += 1
            result = table[route_family]
            assert result.evidence is RouteEvidence.ROUTE_RATE_BASIS_UNAVAILABLE
            assert result.basis_entry is None
            assert result.n_real_observations == 0
        assert checked, "no family lacks data -- this test would be asserting nothing"


class TestBasisMechanismWithSyntheticData:
    """Real code paths (VALIDATED, MODELLED) that today's real data does not
    reach -- exercised here with a small, clearly-synthetic master frame so
    the MECHANISM is proven correct without asserting anything false about
    real current evidence (see TestRealRouteBasisTable above for that)."""

    def _synthetic_master(self, n: int, unit: str = "usd/day") -> pl.DataFrame:
        rows = []
        for i in range(n):
            rows.append(
                {"series_id": "SG_P1A_82_USD_DAY", "date": date(2025, 1, 1 + i), "value": 20000.0 + i * 100, "unit": unit, "source": "signal"}
            )
            rows.append(
                {"series_id": "PANAMAX_TCAVG", "date": date(2025, 1, 1 + i), "value": 18000.0, "unit": "usd/day", "source": "handybulk"}
            )
        return pl.DataFrame(rows)

    def test_n_at_or_above_threshold_is_validated_and_applied(self) -> None:
        # AUSTRALIA_EC_INDIA has no real mapped series -- patch the mapping
        # for this one synthetic call via a local override instead of
        # mutating the module-level dict.
        import opt.basis as basis_mod

        master = self._synthetic_master(MIN_ROUTE_OBS)
        original = dict(basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES)
        try:
            basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES[RouteFamily.AUSTRALIA_EC_INDIA] = ("SG_P1A_82_USD_DAY",)
            result = basis_mod._resolve_one(master, RouteFamily.AUSTRALIA_EC_INDIA, as_of=None)
        finally:
            basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES.clear()
            basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES.update(original)

        assert result.evidence is RouteEvidence.OBSERVED
        assert result.basis_entry is not None
        assert result.n_real_observations == MIN_ROUTE_OBS
        # Route rate (20000-20400) is above the 18000 TCAVG benchmark -> positive basis.
        assert result.basis_entry.basis_mean > 0

    def test_n_below_threshold_but_above_zero_is_modelled_not_unavailable(self) -> None:
        import opt.basis as basis_mod

        master = self._synthetic_master(MIN_ROUTE_OBS - 1)
        original = dict(basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES)
        try:
            basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES[RouteFamily.AUSTRALIA_EC_INDIA] = ("SG_P1A_82_USD_DAY",)
            result = basis_mod._resolve_one(master, RouteFamily.AUSTRALIA_EC_INDIA, as_of=None)
        finally:
            basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES.clear()
            basis_mod.ROUTE_FAMILY_TO_SIGNAL_SERIES.update(original)

        assert result.evidence is RouteEvidence.MODELLED
        assert result.basis_entry is not None
        assert result.n_real_observations == MIN_ROUTE_OBS - 1

    def test_non_usd_day_evidence_never_gets_applied(self) -> None:
        """A route with real evidence but in the wrong unit (e.g. usd/tonne)
        must stay UNAVAILABLE -- never silently converted."""
        import opt.basis as basis_mod

        master = pl.DataFrame(
            [{"series_id": "SG_SUPRAMAX_INDONESIA_ECI_USD_T", "date": date(2025, 1, i + 1), "value": 9.0, "unit": "usd/tonne", "source": "signal"} for i in range(10)]
        )
        result = basis_mod._resolve_one(master, RouteFamily.INDONESIA_EC_INDIA, as_of=None)
        assert result.evidence is RouteEvidence.ROUTE_RATE_BASIS_UNAVAILABLE
        assert result.basis_entry is None
        assert result.n_real_observations == 10  # observations exist and are counted...
        assert "USD/day" in result.reason  # ...but the reason names the real blocker


class TestBasisEntryInstantiatedInProduction:
    def test_basis_entry_is_constructed_by_opt_basis_not_only_tests(self) -> None:
        """opt.basis._resolve_one is the real, non-test call site (P4
        requirement 2) -- run it against real data and confirm any applied
        entries really are BasisEntry instances, not a stub."""
        from opt.types import BasisEntry

        table = build_route_basis_table()
        for result in table.values():
            if result.basis_entry is not None:
                assert isinstance(result.basis_entry, BasisEntry)

    def test_quote_wires_a_real_basis_table_not_the_old_empty_dict(self) -> None:
        """The historical defect this fixes: opt.quote.quote() used to pass
        basis={} unconditionally. Confirm the real function this test
        exercises actually calls opt.basis (a static source check, mirroring
        tests/test_frozen_test_guard.py's own style -- auditable and simple)."""
        import inspect

        import opt.quote as quote_mod

        src = inspect.getsource(quote_mod)
        assert "basis_table_to_entries" in src
        assert "basis={}" not in src


class TestFourOriginComparison:
    """The acceptance test (P4 spec, verbatim): must pass on EITHER real
    branch -- validated route-specific $/day, OR an explicit
    ROUTE_RATE_BASIS_UNAVAILABLE/MODELLED label with the class-only fallback
    visible. Never silently identical $/day with no explanation (the
    original defect), and never a fabricated difference."""

    ORIGINS = ("NEWCASTLE_AU", "RICHARDS_BAY", "BALIKPAPAN", "HAMPTON_ROADS")

    def test_four_origins_are_either_validated_or_honestly_labelled(self) -> None:
        results = []
        for name in self.ORIGINS:
            r = quote(
                cargo_volume_dwt=70_000,
                origin_port=PortEnum[name],
                dest_port=PortEnum.PARADIP,
                laycan_start=date(2026, 9, 15),
                laycan_end=date(2026, 9, 25),
                contract_term_days=30,
            )
            results.append(r)

        for r in results:
            assert r.route_evidence in (
                RouteEvidenceLevel.OBSERVED, RouteEvidenceLevel.MODELLED, RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE,
            )
            for horizon in r.rate_forecast:
                assert horizon.route_evidence == r.route_evidence

        day_rates = {r.origin_port.name: r.ceiling_usd_per_day for r in results}
        all_identical = len(set(day_rates.values())) == 1

        if all_identical:
            # Branch (b): every origin must explicitly disclose why -- no
            # route_evidence silently implying awareness that isn't there.
            for r in results:
                assert r.route_evidence == RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE, (
                    f"{r.origin_port.name}: identical $/day across origins but route_evidence="
                    f"{r.route_evidence} -- this would silently imply route-awareness that isn't real."
                )
        else:
            # Branch (a): whichever origins differ must be backed by a real,
            # non-UNAVAILABLE evidence level -- a difference with no
            # evidence attached would be exactly the fabrication this test
            # exists to catch.
            for r in results:
                if r.route_evidence == RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE:
                    continue
                assert r.route_adjustment is not None

    def test_mt_figure_is_never_silently_claimed_route_aware(self) -> None:
        """$/MT is always class_rate / transit_days (opt.present's own
        docstring) -- confirm this by construction: with no vessels/other
        state differing, the four origins' $/MT figures must differ from
        each other in exactly the same ratio their transit days do, proving
        $/MT tracks distance alone here, not a hidden route premium."""
        results = {}
        for name in self.ORIGINS:
            r = quote(
                cargo_volume_dwt=70_000, origin_port=PortEnum[name], dest_port=PortEnum.PARADIP,
                laycan_start=date(2026, 9, 15), laycan_end=date(2026, 9, 25), contract_term_days=30,
            )
            results[name] = r
        base = results[self.ORIGINS[0]]
        for name in self.ORIGINS[1:]:
            r = results[name]
            if r.route_evidence != RouteEvidenceLevel.ROUTE_RATE_BASIS_UNAVAILABLE:
                continue  # this origin genuinely IS route-adjusted; the ratio check doesn't apply
            expected_ratio = r.assumed_transit_days / base.assumed_transit_days
            actual_ratio = r.ceiling_usd_per_mt / base.ceiling_usd_per_mt
            assert actual_ratio == pytest.approx(expected_ratio, rel=1e-6)
