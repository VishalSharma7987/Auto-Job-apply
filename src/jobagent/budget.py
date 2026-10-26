"""Run time budget + graceful interruption (SIGTERM/SIGINT).

GitHub Actions kills a job at `timeout-minutes`; before that it sends SIGINT and then SIGTERM. The pipeline checks the
budget between items and stops starting new work once it is used up, so it finishes - and sends its Telegram report -
well before the hard kill. A signal raises RunInterrupted (a BaseException, so the pipeline's per-item
`except Exception` handlers cannot swallow it); the runner's `finally` then sends a short "run interrupted" message
and the partial report.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import time
from collections.abc import Callable, Iterator

log = logging.getLogger(__name__)


class RunInterrupted(BaseException):  # noqa: N818 - mirrors KeyboardInterrupt
    def __init__(self, signal_name: str = "SIGTERM"):
        super().__init__(signal_name)
        self.signal_name = signal_name
        self.reported = False  # set once the runner has sent the partial report


HARD_GRACE_SECONDS = 150  # the backstop fires this long after the budget (workflow timeout is 28 min = 1680 s)


class RunBudget:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.seconds = float(seconds)
        self._clock = clock
        self.started = clock()
        self._announced = False
        self.finishing = False  # set once the final report is being built: the backstop must not interrupt it

    def elapsed(self) -> float:
        return self._clock() - self.started

    def remaining(self) -> float:
        return self.seconds - self.elapsed()

    def exceeded(self) -> bool:
        over = self.elapsed() >= self.seconds
        if over and not self._announced:
            self._announced = True
            log.warning("time budget of %.0fs reached (elapsed %.0fs): no new work will be started", self.seconds,
                        self.elapsed())
        return over


@contextlib.contextmanager
def hard_deadline(budget: RunBudget, grace: float = HARD_GRACE_SECONDS) -> Iterator[None]:
    """Backstop for a call that blocks past the budget (the budget is only checked between items): `grace` seconds
    after it runs out, deliver SIGTERM to the main thread. handle_signals() turns that into RunInterrupted, so the
    partial report is still sent. Cancelled on exit; does nothing once the final report is being built."""
    import threading

    main_ident = threading.main_thread().ident

    def fire() -> None:
        if budget.finishing:
            return
        log.error("hard deadline reached (%.0fs budget + %.0fs grace) - interrupting the blocked main thread",
                  budget.seconds, grace)
        try:
            if hasattr(signal, "pthread_kill"):
                signal.pthread_kill(main_ident, signal.SIGTERM)  # also breaks a blocking recv()
            else:  # Windows
                import _thread

                _thread.interrupt_main(signal.SIGTERM)
        except (OSError, ValueError):  # pragma: no cover - platform specific
            log.exception("could not interrupt the main thread")

    timer = threading.Timer(max(0.0, budget.remaining()) + grace, fire)
    timer.daemon = True
    timer.name = "hard-deadline"
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


@contextlib.contextmanager
def handle_signals(signals: tuple[str, ...] = ("SIGTERM", "SIGINT")) -> Iterator[None]:
    """Turn SIGTERM/SIGINT into RunInterrupted while the block runs (main thread only); restore afterwards.
    Only the first signal raises - a second one during the final report is ignored so the report can finish."""
    import threading

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    fired = {"n": 0}

    def handler(signum, frame):  # noqa: ARG001
        fired["n"] += 1
        name = signal.Signals(signum).name
        if fired["n"] == 1:
            log.warning("received %s - finishing up and sending the report", name)
            raise RunInterrupted(name)
        log.warning("received %s again while finishing - ignored", name)

    previous = {}
    for name in signals:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):  # pragma: no cover - platform specific
            pass
    try:
        yield
    finally:
        for sig, old in previous.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, old)
