"""Run document processing and background AI generation in one worker task."""

import backend.app.boot_attempt  # noqa: F401
import argparse
import logging
import os
import signal
import threading
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.observability import configure_logging
from backend.app.readiness import ReadinessError
from backend.app.settings_overrides import RevisionWatcher
from workers import shutdown
from workers.document_processor import (
    check_worker_ready,
    run_worker as run_document_worker,
)
from workers.generation_processor import run_worker as run_generation_worker

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


def _install_shutdown_handlers(stop: threading.Event) -> None:
    def request_shutdown(_signum: int, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def run_worker(
    *,
    once: bool = False,
    stop_event: threading.Event | None = None,
    session_factory: SessionFactory = SessionLocal,
) -> bool:
    stop = stop_event or threading.Event()
    failures: list[BaseException] = []
    failure_lock = threading.Lock()

    def run(name: str, target: Callable[..., None], concurrency: int) -> None:
        try:
            target(
                once=once,
                worker_id=name,
                stop_event=stop,
                session_factory=session_factory,
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
            name="documents",
            daemon=True,
        ),
        threading.Thread(
            target=run,
            args=(
                "generations",
                run_generation_worker,
                settings.generation_job_concurrency,
            ),
            name="generations",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    def adopt_new_revision(request) -> None:
        logger.info(
            "Stopping the worker to adopt a new configuration revision",
            extra={
                "event": "worker_settings_revision_changed",
                "settings_revision": request.target_revision,
                "success": True,
            },
        )
        stop.set()

    revision_watcher = RevisionWatcher(
        adopt_new_revision, name="lumina-worker-revision-watcher"
    )
    if not once:
        revision_watcher.start()
    try:
        abandoned = shutdown.supervise(
            threads,
            stop,
            mode=settings.worker_shutdown_mode,
            session_factory=session_factory,
        )
    finally:
        revision_watcher.stop()

    with failure_lock:
        if failures:
            raise failures[0]
    return abandoned


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
    configure_logging(
        service="worker",
        environment=settings.app_env,
        persistence_path=(
            settings.operational_log_path
            if settings.operational_log_persistence_enabled
            else None
        ),
        retention_days=settings.operational_log_retention_days,
        max_records=settings.operational_log_max_records,
    )

    if args.check:
        try:
            check_worker_ready()
        except ReadinessError as exc:
            logger.error(
                "Worker readiness check failed: %s",
                exc,
                extra={
                    "event": "worker_readiness_check_failed",
                    "failed_stage": exc.check,
                },
            )
            raise SystemExit(1) from None
        logger.info(
            "Worker readiness check succeeded",
            extra={"event": "worker_readiness_check_succeeded"},
        )
        return

    stop = threading.Event()
    _install_shutdown_handlers(stop)
    try:
        abandoned = run_worker(once=args.once, stop_event=stop)
    except ReadinessError as exc:
        logger.error(
            "Worker readiness check failed: %s",
            exc,
            extra={
                "event": "worker_readiness_check_failed",
                "failed_stage": exc.check,
            },
        )
        raise SystemExit(1) from None
    finally:
        shutdown.kill_child_processes()
    if abandoned:
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()
