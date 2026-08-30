"""Real-data tests for local elasticity, built on the real stockflow/supplycurve output."""
import pytest

from impact.elasticity import NoStockDataError, local_elasticity
from opt.types import VesselClass
from tonnage.basins import Basin, PortIndexMissingError, load_port_index
from tonnage.stockflow import reconstruct
from tonnage.supplycurve import build_tightness_index, fit_all

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


@pytest.fixture(scope="module")
def stockflow_result():
    return reconstruct()


@pytest.fixture(scope="module")
def fits(stockflow_result):
    return fit_all(build_tightness_index(stockflow_result))


@pytest.fixture(scope="module")
def as_of(stockflow_result):
    return stockflow_result.frame["date"].max()


def test_mechanical_derivative_is_exact(stockflow_result, fits, as_of):
    fit = fits[VesselClass.PANAMAX]
    est = local_elasticity(stockflow_result, fit, Basin.PACIFIC, as_of, quantile=0.5)
    assert est.d_tightness_d_demand == pytest.approx(1.0 / est.stock_dwt, rel=1e-12)


def test_chain_rule_product_is_arithmetically_correct(stockflow_result, fits, as_of):
    fit = fits[VesselClass.PANAMAX]
    est = local_elasticity(stockflow_result, fit, Basin.PACIFIC, as_of, quantile=0.5)
    assert est.d_rate_d_demand_usd_per_day_per_dwt == pytest.approx(
        est.d_rate_d_tightness_usd_per_day * est.d_tightness_d_demand, rel=1e-9
    )


def test_confidence_flags_propagate_from_the_real_supply_curve_fit(stockflow_result, fits, as_of):
    # Capesize and Handysize were found weak_signal=True in tonnage.supplycurve's
    # own real-data tests -- confirm that flag actually reaches the elasticity
    # estimate rather than being dropped along the way.
    cape = local_elasticity(stockflow_result, fits[VesselClass.CAPESIZE], Basin.PACIFIC, as_of)
    panamax = local_elasticity(stockflow_result, fits[VesselClass.PANAMAX], Basin.PACIFIC, as_of)
    assert cape.weak_signal is True
    assert panamax.weak_signal is False


def test_basin_share_of_world_stock_is_a_real_fraction_between_zero_and_one(stockflow_result, fits, as_of):
    fit = fits[VesselClass.CAPESIZE]
    for basin in Basin:
        est = local_elasticity(stockflow_result, fit, basin, as_of)
        assert 0.0 < est.basin_share_of_world_stock <= 1.0


def test_pacific_capesize_share_reflects_the_australia_china_concentration(stockflow_result, fits, as_of):
    # Real finding from tonnage.stockflow's own tests: Pacific holds the large
    # majority of world Capesize stock. Confirm the share the elasticity layer
    # reports is consistent with that, not a different, disconnected number.
    est = local_elasticity(stockflow_result, fits[VesselClass.CAPESIZE], Basin.PACIFIC, as_of)
    assert est.basin_share_of_world_stock > 0.5


def test_price_impact_scales_linearly_with_demand(stockflow_result, fits, as_of):
    fit = fits[VesselClass.PANAMAX]
    est = local_elasticity(stockflow_result, fit, Basin.PACIFIC, as_of)
    small = est.price_impact_usd_per_day(76_000.0)
    triple = est.price_impact_usd_per_day(3 * 76_000.0)
    assert triple == pytest.approx(3 * small, rel=1e-9)


def test_footprint_fraction_of_zero_demand_is_zero(stockflow_result, fits, as_of):
    fit = fits[VesselClass.PANAMAX]
    est = local_elasticity(stockflow_result, fit, Basin.PACIFIC, as_of)
    assert est.footprint_fraction(0.0) == 0.0


def test_unknown_date_raises_not_silently_zero(stockflow_result, fits):
    from datetime import date

    fit = fits[VesselClass.PANAMAX]
    with pytest.raises(NoStockDataError):
        local_elasticity(stockflow_result, fit, Basin.PACIFIC, date(2099, 1, 1))


def test_invalid_quantile_rejected(stockflow_result, fits, as_of):
    fit = fits[VesselClass.PANAMAX]
    with pytest.raises(ValueError):
        local_elasticity(stockflow_result, fit, Basin.PACIFIC, as_of, quantile=0.42)
