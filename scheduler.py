"""Pure scheduling helpers. Persisted naive datetimes represent Jerusalem wall time."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Jerusalem')

def local(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)

def wall(value):
    return local(value).replace(tzinfo=None)

def overlaps(a, b, c, d):
    return a < d and c < b

def free_blocks(day, busy, now):
    start = datetime.combine(day, time(8))
    end = datetime.combine(day, time(22))
    if day == now.date():
        start = max(start, now + timedelta(minutes=10))
    blocks = [(start, end)] if start < end and day >= now.date() else []
    for a, b in sorted(busy):
        blocks = subtract(blocks, a, b)
    return blocks

def subtract(blocks, start, end):
    result = []
    for a, b in blocks:
        if not overlaps(a, b, start, end):
            result.append((a, b))
        else:
            if a < start: result.append((a, start))
            if end < b: result.append((end, b))
    return result

def choose_slot(task, day, blocks):
    duration = timedelta(minutes=task.duration_minutes)
    transit = timedelta(minutes=task.estimated_transit_minutes if task.location_context != 'Anywhere' else 0)
    exact = datetime.combine(day, time.fromisoformat(task.preferred_start_time)) if task.preferred_start_time else None
    windows = {'Morning': (8, 12), 'Afternoon': (12, 17), 'Evening': (17, 22)}
    # Explicit day-part requests remain constraints; inferred energy is only a preference.
    preferred = windows[{3:'Morning', 2:'Afternoon', 1:'Evening'}[task.energy_level]]
    candidates = []
    for a, b in blocks:
        earliest, latest = a + transit, b - duration
        if task.preferred_time != 'Any' and exact is None:
            lo, hi = windows[task.preferred_time]
            earliest = max(earliest, datetime.combine(day, time(lo)))
            latest = min(latest, datetime.combine(day, time(hi)) - duration)
        if earliest > latest: continue
        if exact is not None:
            if task.time_mode == 'fixed':
                if not earliest <= exact <= latest: continue
                start = exact
            else: start = max(earliest, min(exact, latest))
            score = abs((start-exact).total_seconds())
        else:
            preferred_start = max(earliest, datetime.combine(day, time(preferred[0])))
            if preferred_start <= latest and preferred_start + duration <= datetime.combine(day, time(preferred[1])):
                start, score = preferred_start, 0
            else: start, score = earliest, 1
        candidates.append((score, start, start + duration, start - transit))
    return min(candidates) if candidates else None
