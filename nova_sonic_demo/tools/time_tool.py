from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

async def get_current_time(args: dict) -> dict:
    tz_name = args.get("timezone") if isinstance(args, dict) else None
    if not tz_name:
        tz_name = "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        return {"error": "invalid_timezone"}
    now = datetime.now(tz)
    return {"timestamp": now.isoformat(), "timezone": tz_name}
