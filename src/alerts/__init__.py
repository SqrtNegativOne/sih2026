"""Standing watches on the desk, and the firings they produce.

``models`` defines what a watch may be about and why the list is short;
``conditions`` resolves each kind against real data already on disk; ``run``
evaluates every active watch once; ``store`` persists watches and firings
alongside the accounts.

The condition module is called ``conditions`` and not ``evaluate`` because this
package re-exports the ``evaluate`` FUNCTION: a submodule of the same name
would be shadowed by it on the package object, so ``alerts.evaluate`` would
mean the function in one import order and the module in another. Found the
hard way -- it silently broke monkeypatching in the tests.
"""

from alerts.conditions import Evaluation, evaluate
from alerts.models import WATCH_DESCRIPTION, Direction, Firing, Watch, WatchKind
from alerts.run import run_due_watches
from alerts.store import AlertError, AlertStore, InvalidWatchError, NoSuchWatchError

__all__ = [
    "WATCH_DESCRIPTION",
    "AlertError",
    "AlertStore",
    "Direction",
    "Evaluation",
    "Firing",
    "InvalidWatchError",
    "NoSuchWatchError",
    "Watch",
    "WatchKind",
    "evaluate",
    "run_due_watches",
]
