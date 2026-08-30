"""P4 requirement 5 -- every PortWatch field carries correct provenance; no
ESTIMATED value surfaces labelled OBSERVED, anywhere it reaches a user.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from data_builders.provenance import (
    PORTWATCH_FIELD_PROVENANCE,
    Provenance,
    portwatch_field_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"


class TestProvenanceEnumAndMapping:
    def test_real_portwatch_columns_are_mapped(self) -> None:
        assert portwatch_field_provenance("portcalls_dry_bulk") == Provenance.OBSERVED
        assert portwatch_field_provenance("import_dry_bulk") == Provenance.ESTIMATED
        assert portwatch_field_provenance("export_dry_bulk") == Provenance.ESTIMATED

    def test_unknown_field_raises_rather_than_guessing(self) -> None:
        with pytest.raises(KeyError):
            portwatch_field_provenance("not_a_real_column")

    def test_mapping_covers_exactly_the_real_csv_columns(self) -> None:
        """Cross-checked against the real header of an actual PortWatch CSV
        on disk, not just the hardcoded mapping agreeing with itself."""
        import csv

        portwatch_dir = REPO_ROOT / "raw_data" / "portwatch"
        csvs = sorted(portwatch_dir.glob("*_daily_portcalls.csv"))
        if not csvs:
            pytest.skip("no real PortWatch CSVs on disk")
        with csvs[0].open(newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        for field in PORTWATCH_FIELD_PROVENANCE:
            assert field in header, f"{field!r} not found in real PortWatch header {header}"


class TestCongestionAndRiskNeverTouchEstimatedFields:
    """Static audit: opt.congestion and opt.risk's real wait/alert signals
    are built ONLY from portcalls_dry_bulk (OBSERVED) -- confirmed by
    scanning the real source for any reference to the two ESTIMATED PortWatch
    columns, mirroring tests/test_frozen_test_guard.py's own style."""

    @pytest.mark.parametrize("module_path", ["opt/congestion.py", "opt/risk.py"])
    def test_module_never_reads_an_estimated_field(self, module_path: str) -> None:
        text = (SRC / module_path).read_text(encoding="utf-8")
        assert "import_dry_bulk" not in text
        assert "export_dry_bulk" not in text
        assert "portcalls_dry_bulk" in text  # confirms it DOES use the real OBSERVED field


class TestRepositioningProvenance:
    """opt.repositioning is the real consumer of export_dry_bulk (ESTIMATED)
    -- confirmed the value now carries explicit provenance through to the
    API-facing types."""

    def test_module_reads_the_estimated_field_and_declares_it(self) -> None:
        text = (SRC / "opt" / "repositioning.py").read_text(encoding="utf-8")
        assert "export_dry_bulk" in text
        assert "from data_builders.provenance import Provenance" in text
        assert "data_provenance" in text

    def test_types_carry_provenance_through_to_the_api(self) -> None:
        text = (SRC / "opt" / "types.py").read_text(encoding="utf-8")
        assert re.search(r"class RepositioningAction\(BaseModel\):.*?data_provenance", text, re.DOTALL)
        assert re.search(r"class RepositioningOption\(BaseModel\):.*?data_provenance", text, re.DOTALL)

    def test_real_provenance_values_are_estimated_never_observed_or_declared(self) -> None:
        """Live check against a real quote: whenever real tonnage-field data
        was actually used (probability_is_real_data=True), the provenance
        must say ESTIMATED (PortWatch's own model output) -- never OBSERVED
        or DECLARED, which would misrepresent what export_dry_bulk actually
        is. Never CANNOT_VERIFY/None there either -- that combination would
        mean real data was used with no honest label for it."""
        from opt.network import PortEnum
        from opt.quote import quote
        from opt.types import Vessel, VesselClass

        vessel = Vessel(
            vessel_id="V1", vessel_class=VesselClass.PANAMAX, current_port=PortEnum.SINGAPORE,
            status="idle", available_from=date(2026, 9, 1), dwt=82_000, draft_m=14.5, loa_m=225.0,
            beam_m=32.26, speed_kn=14.0, laden_fuel_consumption_tpd=32.0, ballast_fuel_consumption_tpd=27.2,
        )
        r = quote(
            cargo_volume_dwt=70_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            laycan_start=date(2026, 9, 15), laycan_end=date(2026, 9, 25), contract_term_days=30,
            vessels=[vessel], revenue_usd=500_000,
        )
        options = r.full_recommendation.repositioning_options
        assert len(options) > 0
        any_real = False
        for o in options:
            if o.probability_is_real_data:
                any_real = True
                assert o.data_provenance == Provenance.ESTIMATED
            else:
                assert o.data_provenance is None
        assert any_real, "expected at least one real-data-backed repositioning option for this real scenario"


class TestNoEstimatedValuePresentedAsObserved:
    """Broader static sweep: nowhere in src/opt/ or src/tonnage/ does a
    docstring/comment claim import_dry_bulk or export_dry_bulk is
    'observed'/'measured' -- a looser, complementary check to the
    field-level audits above."""

    def test_no_estimated_portwatch_field_is_called_observed_or_measured(self) -> None:
        offenders: list[str] = []
        for root in (SRC / "opt", SRC / "tonnage"):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for lineno, line in enumerate(text.splitlines(), start=1):
                    lower = line.lower()
                    if ("export_dry_bulk" in lower or "import_dry_bulk" in lower) and (
                        "observed" in lower or "measured" in lower
                    ) and "provenance" not in lower and "estimate" not in lower:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
        assert not offenders, "\n".join(offenders)
