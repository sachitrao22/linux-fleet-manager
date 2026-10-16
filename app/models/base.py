from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Timezone-aware UTC now. Passed as a callable (no parentheses) to column defaults."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """
    Always store UTC and always hand back timezone-aware datetimes.
    Postgres keeps the offset natively; SQLite drops it, so it is re-attached on load.
    Without this, the same code returns aware datetimes on Postgres and naive ones on
    SQLite, and comparing the two raises TypeError.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
