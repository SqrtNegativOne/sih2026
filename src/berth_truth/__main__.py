"""``python -m berth_truth`` -- runs the vessel-schedule archiver.

No scheduler is installed by this project; point cron / Task Scheduler /
GitHub Actions at this command, no more often than
``archiver.MIN_FETCH_INTERVAL_SECONDS`` allows.
"""
from __future__ import annotations

import sys

from berth_truth.archiver import main

if __name__ == "__main__":
    sys.exit(main())
