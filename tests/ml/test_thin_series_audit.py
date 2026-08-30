"""P4 requirement 6 -- thin-series audit numbers, asserted against the real
master table."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from ml.thin_series_audit import audit_all_classes, audit_thin_series


class TestThinSeriesAudit:
    def test_every_class_is_covered(self) -> None:
        reports = audit_all_classes()
        assert set(reports) == {"Capesize", "Panamax", "Supramax", "Handysize"}

    def test_index_history_is_real_and_much_deeper_than_tcavg(self) -> None:
        """Measured live: index history goes back to 2012-07-04 (~3,400+ real
        rows); TCAVG starts far later per class (2020/2024/2025). Assert the
        real, wide-margin relationship rather than exact counts, so this
        survives future data refreshes."""
        reports = audit_all_classes()
        for r in reports.values():
            assert r.n_index_rows > 3000
            assert r.n_tcavg_rows < 400
            assert r.index_first_date < r.tcavg_first_date  # index starts well before TCAVG

    def test_coverage_fraction_is_real_and_thin(self) -> None:
        reports = audit_all_classes()
        for r in reports.values():
            assert 0.0 < r.tcavg_coverage_fraction < 0.15  # measured live: 5.3%-7.9%

    def test_capesize_regime_breaks_match_the_known_real_finding(self) -> None:
        """Cross-checked against ml.units' own module docstring (slope
        8.2931 -> 9.0696 in January 2026, plus a transient additive offset
        that persisted until June 2026) -- re-derived live here via
        detect_regime_breaks, not copied from the docstring."""
        r = audit_thin_series("Capesize")
        assert len(r.regime_segments) >= 2  # a real regime change happened
        slopes = [seg.slope for seg in r.regime_segments]
        # the real 8.29 -> 9.07 move is captured
        assert min(slopes) < 8.5
        assert max(slopes) > 9.0

    def test_models_never_train_on_tcavg_directly(self) -> None:
        """Live-verified against the real training config, not asserted from
        the module docstring alone: [targets.classes] in samples.toml maps
        each class to its INDEX series (BC_INDEX etc.), never a TCAVG
        series_id."""
        config_path = Path(__file__).resolve().parents[2] / "src" / "config" / "samples.toml"
        with config_path.open("rb") as f:
            config = tomllib.load(f)
        target_series = set(config["targets"]["classes"])
        assert target_series == {"BC_INDEX", "BPI_INDEX", "BSI_INDEX", "BHSI_INDEX"}
        assert not any("TCAVG" in s for s in target_series)

        reports = audit_all_classes()
        for r in reports.values():
            assert r.trains_on_tcavg_directly is False

    def test_regime_segments_carry_a_real_max_residual(self) -> None:
        reports = audit_all_classes()
        for r in reports.values():
            for seg in r.regime_segments:
                assert seg.max_residual_usd >= 0
                assert seg.n_obs >= 3  # detect_regime_breaks' own minimum segment length

    def test_unknown_class_raises(self) -> None:
        with pytest.raises(KeyError):
            audit_thin_series("NotAClass")
