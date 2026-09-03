"""Suite-wide test configuration.

Two environment variables are set here, before any test module imports
``backend.main``, because both are read once at import time.

``DESK_REQUIRE_AUTH=0``
    The desk requires a sign-in by default in a real deployment. Almost every
    test in this suite is about what the desk *computes* -- a forecast, a
    schedule, a port constraint -- and prefixing all of them with a login would
    test the login 149 extra times and the subject once.

    The gate itself is tested deliberately and in both directions, in
    ``tests/backend/test_auth_api.py``: ``TestClosedMode`` turns enforcement on
    and checks that anonymous callers are refused, that the sign-in route stays
    reachable, and that each role can do exactly what it should;
    ``TestDefaults`` checks that the default really is enforced, by reading the
    same environment logic the module uses rather than the value this file has
    already overridden.

``DESK_DISABLE_ALERT_LOOP=1`` and ``DESK_DISABLE_RATE_REFRESH=1``
    The app starts two background tasks: one evaluates standing alerts on a
    timer, the other fetches the day's Baltic rates and folds them into
    ``master_long.parquet``. Neither belongs in a test run. The rate refresh in
    particular would make a real network request and rewrite a real data file
    as a side effect of ``TestClient(app)`` being constructed, which would make
    the suite non-deterministic and mutate the repository's own data.
    ``tests/data_builders/test_harvest_handybulk.py`` exercises that harvester
    properly, against a saved page and temporary files.
"""

from __future__ import annotations

import os

os.environ.setdefault("DESK_REQUIRE_AUTH", "0")
os.environ.setdefault("DESK_DISABLE_ALERT_LOOP", "1")
os.environ.setdefault("DESK_DISABLE_RATE_REFRESH", "1")
