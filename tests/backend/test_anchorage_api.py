"""4.3 -- GET /anchorage/{port_code}/census and GET /anchorage/calibration
against the real FastAPI app and real data on disk (no mocking, same
philosophy as test_reality_api.py)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import anchorage.store as store_module
import backend.main as main_module
from anchorage.detect import AnchorageCensus
from backend.main import app

client = TestClient(app)


def _census(port: str, scene_id: str, acquired_at: datetime, vessel_count: int = 4) -> AnchorageCensus:
    return AnchorageCensus(
        port=port,
        scene_id=scene_id,
        acquired_at=acquired_at,
        vessel_count=vessel_count,
        detections=(),
        mean_sea_state_proxy=0.1,
        confidence="high",
        provenance="MODEL_DERIVED",
    )


@pytest.fixture()
def isolated_census_log(tmp_path, monkeypatch):
    """Same isolation pattern test_ledger_api.py/test_landed_cost_api.py
    already use -- this suite must never read or write the project's own
    raw_data/anchorage/census.jsonl."""
    path = tmp_path / "census.jsonl"
    monkeypatch.setattr(store_module, "CENSUS_LOG", path)
    monkeypatch.setattr(main_module, "latest_census", lambda port: store_module.latest_census(port, path=path))
    return path


class TestAnchorageCensusEndpoint:
    def test_no_scene_processed_returns_404_not_a_fabricated_empty_body(self, isolated_census_log) -> None:
        r = client.get("/anchorage/PARADIP/census")
        assert r.status_code == 404
        assert "PARADIP" in r.json()["detail"]

    def test_unknown_port_returns_422(self) -> None:
        r = client.get("/anchorage/NOT_A_REAL_PORT/census")
        assert r.status_code == 422
        assert "Unknown anchorage port" in r.json()["detail"]

    def test_a_port_outside_opt_network_portenum_still_resolves(self, isolated_census_log) -> None:
        """HAY_POINT_AU is not an opt.network.PortEnum member (see
        data_builders.harvest_sentinel1's own docstring) -- must still
        validate as a real anchorage port, not 422 as unknown. Uses the
        isolated log (not the project's real census.jsonl) so this stays
        true regardless of whether a real HAY_POINT_AU scene has since been
        processed -- it did, this test used to read that real 200 by
        accident and assert the stale "never processed yet" 404 on it."""
        r = client.get("/anchorage/HAY_POINT_AU/census")
        assert r.status_code == 404  # valid port, just no scene yet -- not 422

    def test_a_real_recorded_census_is_returned_with_its_real_fields(self, isolated_census_log) -> None:
        c = _census("PARADIP", "real-shaped-scene", datetime(2026, 8, 20, 12, 0, tzinfo=UTC), vessel_count=7)
        store_module.record_census(c, path=isolated_census_log)

        r = client.get("/anchorage/PARADIP/census")
        assert r.status_code == 200
        body = r.json()
        assert body["port"] == "PARADIP"
        assert body["scene_id"] == "real-shaped-scene"
        assert body["vessel_count"] == 7
        assert body["confidence"] == "high"
        assert body["provenance"] == "MODEL_DERIVED"
        assert "acquired_at" in body

    def test_returns_the_latest_of_multiple_real_censuses(self, isolated_census_log) -> None:
        older = _census("PARADIP", "older", datetime(2026, 8, 1, tzinfo=UTC), vessel_count=2)
        newer = _census("PARADIP", "newer", datetime(2026, 8, 25, tzinfo=UTC), vessel_count=9)
        store_module.record_census(older, path=isolated_census_log)
        store_module.record_census(newer, path=isolated_census_log)

        r = client.get("/anchorage/PARADIP/census")
        assert r.status_code == 200
        assert r.json()["scene_id"] == "newer"


@pytest.fixture()
def isolated_overlay_dir(tmp_path, monkeypatch):
    """Same isolation shape as isolated_census_log above -- redirects the
    (port, scene_id) -> PNG lookup GET /anchorage/{port}/overlay.png uses to
    a tmp dir, never the project's real raw_data/anchorage/overlays/.
    overlay_path's own `root` parameter defaults to the module-level
    OVERLAY_DIR bound at function-definition time (an ordinary Python
    default-argument), so monkeypatching anchorage.render.OVERLAY_DIR alone
    would NOT redirect an already-bound default -- this instead replaces
    backend.main's own imported reference to overlay_path itself, the same
    real function with `root` pinned to tmp_path via functools.partial."""
    import functools

    from anchorage.render import overlay_path as real_overlay_path

    monkeypatch.setattr(main_module, "overlay_path", functools.partial(real_overlay_path, root=tmp_path))
    return tmp_path


class TestAnchorageOverlayEndpoint:
    def test_no_census_returns_404(self, isolated_census_log, isolated_overlay_dir) -> None:
        r = client.get("/anchorage/PARADIP/overlay.png")
        assert r.status_code == 404

    def test_census_exists_but_no_overlay_rendered_returns_404(self, isolated_census_log, isolated_overlay_dir) -> None:
        c = _census("PARADIP", "scene-1", datetime(2026, 8, 20, tzinfo=UTC))
        store_module.record_census(c, path=isolated_census_log)

        r = client.get("/anchorage/PARADIP/overlay.png")
        assert r.status_code == 404
        assert "scene-1" in r.json()["detail"]

    def test_a_real_rendered_overlay_is_served_as_a_png(self, isolated_census_log, isolated_overlay_dir) -> None:
        import numpy as np

        from anchorage.render import render_detection_overlay

        c = _census("PARADIP", "scene-2", datetime(2026, 8, 20, tzinfo=UTC))
        store_module.record_census(c, path=isolated_census_log)
        render_detection_overlay(
            np.clip(np.random.RandomState(0).normal(10.0, 1.0, (40, 40)), 0.01, None),
            [],
            port="PARADIP",
            scene_id="scene-2",
            acquired_at=c.acquired_at,
            out_path=isolated_overlay_dir / "PARADIP" / "scene-2.png",
        )

        r = client.get("/anchorage/PARADIP/overlay.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_port_returns_422(self, isolated_overlay_dir) -> None:
        r = client.get("/anchorage/NOT_A_REAL_PORT/overlay.png")
        assert r.status_code == 422

    def test_lowercase_port_code_is_normalised(self, isolated_census_log) -> None:
        c = _census("PARADIP", "s1", datetime(2026, 8, 20, tzinfo=UTC))
        store_module.record_census(c, path=isolated_census_log)
        r = client.get("/anchorage/paradip/census")
        assert r.status_code == 200


class TestAnchorageCalibrationEndpoint:
    def test_returns_the_min_n_constant_and_all_five_ports(self) -> None:
        r = client.get("/anchorage/calibration")
        assert r.status_code == 200
        body = r.json()
        assert body["min_n_for_correlation"] >= 1
        ports = {row["port"] for row in body["results"]}
        assert ports == {"PARADIP", "VISAKHAPATNAM", "NEWCASTLE_AU", "HAY_POINT_AU", "RICHARDS_BAY_ZA"}

    def test_every_row_has_the_documented_shape(self) -> None:
        r = client.get("/anchorage/calibration")
        for row in r.json()["results"]:
            assert set(row) == {
                "port", "n", "date_from", "date_to", "spearman_r", "pearson_r", "mean_abs_diff", "finding",
            }
            assert isinstance(row["finding"], str) and row["finding"]

    def test_a_port_with_n_below_min_n_reports_null_correlations_not_zeros(self) -> None:
        """The real, current state of this environment: zero real scenes
        have been processed anywhere (see anchorage.detect's own module
        docstring), so every port's real n is 0 -- null, never a fabricated
        0.0 correlation standing in for 'no data'."""
        r = client.get("/anchorage/calibration")
        for row in r.json()["results"]:
            if row["n"] < 10:
                assert row["spearman_r"] is None
                assert row["pearson_r"] is None
