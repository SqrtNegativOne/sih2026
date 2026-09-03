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

``DESK_DISABLE_ALERT_LOOP``, ``DESK_DISABLE_RATE_REFRESH``,
``DESK_DISABLE_PORT_REFRESH`` and ``DESK_DISABLE_WARMUP``
    The app starts background work on a timer: evaluating standing alerts,
    fetching the day's Baltic index and route rates, and topping up 128 port-call
    files. None of it belongs in a test run. Left on, merely constructing a
    ``TestClient(app)`` would make real network requests and rewrite the
    repository's own data files as a side effect, which is both
    non-deterministic and destructive.

    The harvesters are exercised properly in ``tests/data_builders/``, against
    saved pages and temporary files.
"""

from __future__ import annotations

import os

os.environ.setdefault("DESK_REQUIRE_AUTH", "0")
os.environ.setdefault("DESK_DISABLE_ALERT_LOOP", "1")
os.environ.setdefault("DESK_DISABLE_RATE_REFRESH", "1")
os.environ.setdefault("DESK_DISABLE_PORT_REFRESH", "1")
os.environ.setdefault("DESK_DISABLE_WARMUP", "1")
