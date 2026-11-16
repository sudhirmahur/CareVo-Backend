"""Background jobs (alert digests, event/calendar reminders, scheduled backups). Runs inside the API process."""
import asyncio
import logging
import time

log = logging.getLogger("carevo.scheduler")
_last: dict[str, float] = {}


def _due(name: str, every: int) -> bool:
    t = time.monotonic()
    if t - _last.get(name, -1e9) >= every:
        _last[name] = t
        return True
    return False


def tick() -> dict:
    """One scheduler pass; every task is isolated so a failure never stops the others."""
    from app.routes.career import run_alert_digests, run_event_reminders
    from app.routes.hiring import run_calendar_reminders
    from app.services import backup

    ran: dict = {}
    tasks = [("reminders", 300, lambda: (run_event_reminders(), run_calendar_reminders())), ("digests", 3600, run_alert_digests), ("backup", 900, backup.run_if_due)]
    for name, every, fn in tasks:
        if not _due(name, every):
            continue
        try:
            ran[name] = fn()
        except Exception:  # noqa: BLE001
            log.exception("scheduled task %s failed", name)
    return ran


async def loop() -> None:
    while True:
        await asyncio.to_thread(tick)
        await asyncio.sleep(60)
