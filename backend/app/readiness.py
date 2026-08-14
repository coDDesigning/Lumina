"""Runtime dependency checks shared by the API and document worker."""

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.models import Role as DatabaseRole
from schemas.user import Role
from storage.base import Storage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_ROLE_NAMES = frozenset(role.value for role in Role)


class ReadinessError(RuntimeError):
    """A required runtime dependency is unavailable or incorrectly prepared."""


@lru_cache(maxsize=1)
def _expected_migration_heads() -> frozenset[str]:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("prepend_sys_path", str(PROJECT_ROOT))
    heads = frozenset(ScriptDirectory.from_config(config).get_heads())
    if not heads:
        raise RuntimeError("Alembic has no migration heads.")
    return heads


def check_readiness(db: Session, storage: Storage) -> None:
    """Verify database schema, seed data, and document storage availability."""
    try:
        current_heads = frozenset(
            MigrationContext.configure(db.connection()).get_current_heads()
        )
        if current_heads != _expected_migration_heads():
            raise ReadinessError("Database migrations are not current.")

        role_names = frozenset(
            db.scalars(
                select(DatabaseRole.name).where(
                    DatabaseRole.name.in_(REQUIRED_ROLE_NAMES)
                )
            ).all()
        )
        if role_names != REQUIRED_ROLE_NAMES:
            raise ReadinessError("Required database roles are missing.")

        # Updating a guaranteed seed row to its current value forces SQLite to
        # open its journal and proves that the runtime identity has DML access.
        try:
            result = db.execute(
                update(DatabaseRole)
                .where(DatabaseRole.name == Role.ADMIN.value)
                .values(name=DatabaseRole.name)
            )
            if result.rowcount != 1:
                raise ReadinessError("Database write probe did not find its seed row.")
        finally:
            db.rollback()

        storage.check_ready()
    except ReadinessError:
        raise
    except Exception as exc:
        raise ReadinessError("A runtime dependency is not ready.") from exc
