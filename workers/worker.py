"""Run document processing and background AI generation in one worker task."""

import argparse
import logging
import signal
import threading
from collections.abc import Callable, Sequence

from backend.app.config import settings
from backend.app.observability import configure_logging
from backend.app.readiness import ReadinessError
from workers.document_processor import (
    check_worker_ready,
    run_worker as run_document_worker,
)
from workers.generation_processor import run_worker as run_generation_worker

logger = logging.getLogger(__name__)


def _install_shutdown_handlers(stop: threading.Event) -> None:
    def request_shutdown(_signum: int, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def run_worker(
    *, once: bool = False, stop_event: threading.Event | None = None
) -> None:
    stop = stop_event or threading.Event()
    failures: list[BaseException] = []
    failure_lock = threading.Lock()

    def run(name: str, target: Callable[..., None], concurrency: int) -> None:
        try:
            target(
                once=once,
                worker_id=name,
                stop_event=stop,
                concurrency=concurrency,
            )
        except BaseException as exc:
            with failure_lock:
                failures.append(exc)
            stop.set()

    threads = [
        threading.Thread(
            target=run,
            args=(
                "documents",
                run_document_worker,
                settings.processing_job_concurrency,
            ),
        ),
        threading.Thread(
            target=run,
            args=(
                "generations",
                run_generation_worker,
                settings.generation_job_concurrency,
            ),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with failure_lock:
        if failures:
            raise failures[0]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run Lumina background workers")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument(
        "--check",
        action="store_true",
        help="Verify worker dependencies without claiming work",
    )
    args = parser.parse_args(argv)
    configure_logging(service="worker", environment=settings.app_env)

    if args.check:
        try:
            check_worker_ready()
        except ReadinessError as exc:
            logger.error("Worker readiness check failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Worker readiness check succeeded")
        return

    stop = threading.Event()
    _install_shutdown_handlers(stop)
    try:
        run_worker(once=args.once, stop_event=stop)
    except ReadinessError as exc:
        logger.error("Worker readiness check failed: %s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
