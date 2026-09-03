"""Precompute the historical-replay snapshot, once, at build time.

Why this exists
---------------
`opt.replay.get_replay_snapshot` runs a PSO calibration on the validation split
and then a Monte-Carlo report over the frozen test split. That is about
**twenty-two minutes** of real computation, and until now it was cached only
for the life of the process — so every backend restart threw it away, and the
launchers run uvicorn with ``--reload``, which means saving any Python file
threw it away too.

The consequence was not a performance note. It was that the first person to
open the Replay screen paid twenty-two minutes, and at a competition that
person is a judge.

Nothing about the numbers changes here. They are computed once, by whoever
prepares the build, and stored under a key that covers every input that could
alter them (see ``opt.replay.snapshot_key``). The server then loads the file
instead of redoing the work.

This is the same shape as every other builder in this directory: run it
deliberately, it writes into ``src/data/``, and the consumer degrades to
computing for itself if the artefact is absent.

Run it before a demo:
    uv run python -m data_builders.build_replay_snapshot

It prints the key it wrote under. If the models are retrained or the market
data is rebuilt, the key changes and this must be run again — the server will
not silently serve the old numbers, it will recompute them.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Final

from opt.replay import (
    get_replay_snapshot,
    snapshot_cache_path,
    snapshot_key,
    write_snapshot_to_disk,
)

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    key = snapshot_key()
    path = snapshot_cache_path(key)
    if path.exists():
        LOGGER.info("Already built for this key (%s) at %s", key, path.name)
        LOGGER.info("Nothing to do. Delete that file to force a rebuild.")
        return 0

    LOGGER.info("Key %s has no stored snapshot.", key)
    LOGGER.info(
        "Computing it now: PSO calibration on the validation split, then one "
        "Monte-Carlo report over the frozen test split. This takes roughly "
        "twenty minutes and is real work, not a warm-up."
    )
    started = time.perf_counter()
    snapshot = get_replay_snapshot()
    elapsed = time.perf_counter() - started

    # get_replay_snapshot writes on a successful compute, but it may also have
    # returned a snapshot it loaded from memory in the same process. Writing
    # again is cheap and makes this script's contract unconditional: after it
    # exits 0, the file exists.
    written = write_snapshot_to_disk(snapshot)

    LOGGER.info("Done in %.1f minutes.", elapsed / 60)
    LOGGER.info("Wrote %s", written)
    LOGGER.info(
        "Replayed %d real test rows; %d strategy summaries stored.",
        snapshot.n_test_rows,
        len(snapshot.summaries),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
