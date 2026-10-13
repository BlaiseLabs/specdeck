"""The one source of the current time."""
from datetime import datetime, timezone


def now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(timezone.utc)
