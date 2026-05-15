from __future__ import annotations
import hashlib

CONDITIONS = ("sunny", "cloudy", "rainy", "snowy", "windy")

def _stable_seed(city: str) -> int:
    digest = hashlib.sha256(city.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)

async def get_weather(args: dict) -> dict:
    if not isinstance(args, dict):
        return {"error": "invalid_arguments"}
    raw_city = args.get("city")
    if not isinstance(raw_city, str):
        return {"error": "invalid_arguments"}
    city = raw_city.strip()
    if not city:
        return {"error": "invalid_arguments"}
    seed = _stable_seed(city.lower())
    condition = CONDITIONS[seed % len(CONDITIONS)]
    # Map seed to integer in [-50, 50] inclusive (101 values).
    temperature_c = (seed % 101) - 50
    return {
        "city": city,
        "condition": condition,
        "temperature_c": temperature_c,
    }
