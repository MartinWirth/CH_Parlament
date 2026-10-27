"""Optional periodic social profile link checks."""
import asyncio
import os
from datetime import datetime, timezone, timedelta


def interval_hours():
    value = float(os.getenv('SOCIAL_CHECK_INTERVAL_HOURS', '0'))
    if value != 0 and not 1 <= value <= 720:
        raise ValueError('Interval must be zero or 1..720 hours')
    return value


def due(previous, hours):
    if not previous:
        return True
    try:
        stamp = datetime.fromisoformat(previous)
        return stamp.tzinfo is None or datetime.now(timezone.utc) >= stamp + timedelta(hours=hours)
    except (ValueError, TypeError):
        return True


async def run_due():
    from . import admin
    from .social_check_history import latest_check_time
    hours = interval_hours()
    if not hours:
        return
    languages = [part.strip().upper() for part in os.getenv('SOCIAL_CHECK_LANGUAGES', 'DE').split(',')]
    if not languages or any(lang not in ('DE', 'FR', 'IT', 'RM') for lang in languages):
        raise ValueError('Invalid scheduled languages')
    for lang in dict.fromkeys(languages):
        if admin._live_task and not admin._live_task.done():
            return
        if admin._live_job.get('state') == 'running':
            return
        if not due(latest_check_time(lang), hours):
            continue
        admin._live_job.clear()
        admin._live_job.update(state='running', language=lang, checked=0, results=[], source='scheduled')
        await admin._execute_live_check(lang)


async def loop():
    import logging
    while True:
        try:
            await run_due()
        except Exception:
            logging.getLogger('ch_parlament').exception('Scheduled link check failed')
        await asyncio.sleep(3600)
