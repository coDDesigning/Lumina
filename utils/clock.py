"""The one clock every dated decision about a calendar date reads.

UTC rather than the host's local calendar. A service that reads
``date.today()`` moves its date boundaries by a day depending on where the
process happens to run, which is how the exam roadmap came to disagree with
the exam plan gate and the browser about which day it was.
"""

from datetime import date, datetime, timezone


def utc_today() -> date:
    return datetime.now(timezone.utc).date()
