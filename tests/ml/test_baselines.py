import polars as pl
import pytest

from ml.baselines import _clean_sigma, pinball


def test_pinball_loss():
    # True values
    y = pl.Series("y", [10.0, 10.0, 10.0])
    # Predictions
    p = pl.Series("p", [8.0, 10.0, 12.0])
    
    # Errors: y - p = [2.0, 0.0, -2.0]
    # For q=0.5: mean of [0.5*2, 0.5*0, -0.5*-2] = [1.0, 0.0, 1.0] -> mean = 2/3 = 0.6666...
    loss = pinball(y, p, 0.5)
    assert loss == pytest.approx(0.6666666666666666)

    # For q=0.1: mean of [0.1*2, 0, -0.9*-2] = [0.2, 0, 1.8] -> mean = 2.0 / 3 = 0.6666...
    loss_01 = pinball(y, p, 0.1)
    assert loss_01 == pytest.approx(0.6666666666666666)
    
    # For q=0.9: mean of [0.9*2, 0, -0.1*-2] = [1.8, 0, 0.2] -> mean = 2.0 / 3 = 0.6666...
    loss_09 = pinball(y, p, 0.9)
    assert loss_09 == pytest.approx(0.6666666666666666)

def test_clean_sigma():
    # Valid float
    assert _clean_sigma(1.5, 0.5) == 1.5
    # None
    assert _clean_sigma(None, 0.5) == 0.5
    # NaN
    assert _clean_sigma(float('nan'), 0.5) == 0.5
    # Negative / Zero
    assert _clean_sigma(0.0, 0.5) == 0.5
    assert _clean_sigma(-1.0, 0.5) == 0.5
