"""Caché simple en disco con expiración.

API-Football (plan gratuito) da solo 100 peticiones/día. Sin esto, pedir
el calendario o las lesiones del mismo equipo para varios jugadores tuyos,
o sincronizar dos veces el mismo día, agota la cuota enseguida. Los datos
como el calendario o la clasificación apenas cambian en unas horas, así que
basta con no repetir la misma petición dentro de la ventana de `ttl`.
"""
import json
import os
import time

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "api_cache.json"
)
DEFAULT_TTL = 6 * 3600  # 6 horas


def _load():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def get_or_set(key, fn, ttl=DEFAULT_TTL):
    """Devuelve el valor cacheado si no ha caducado; si no, llama a `fn()`,
    lo guarda y lo devuelve."""
    cache = _load()
    entry = cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["value"]
    value = fn()
    cache[key] = {"ts": time.time(), "value": value}
    _save(cache)
    return value
