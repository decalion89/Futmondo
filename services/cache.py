"""Caché simple en disco con expiración.

API-Football (plan gratuito) da solo 100 peticiones/día. Sin esto, pedir
el calendario o las lesiones del mismo equipo para varios jugadores tuyos,
o sincronizar dos veces el mismo día, agota la cuota enseguida. Los datos
como el calendario o la clasificación apenas cambian en unas horas, así que
basta con no repetir la misma petición dentro de la ventana de `ttl`.

Asume un único proceso worker (el Procfile arranca gunicorn sin `--workers`,
por defecto 1) — la copia en memoria de `_load()` no se invalida si otro
proceso escribe el archivo, así que si algún día se añaden más workers,
esto necesitaría un cache compartido de verdad (Redis o similar), no un
diccionario en memoria de proceso."""
import json
import os
import time

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "api_cache.json"
)
DEFAULT_TTL = 6 * 3600  # 6 horas

# Copia en memoria del archivo de caché, indexada por ruta (para que los
# tests que redirigen CACHE_FILE a un tmp_path no se pisen entre sí).
# Sin esto, cada get_or_set/peek releía y reparseaba el archivo ENTERO
# desde disco — con la caché ya en cientos de KB (una página con ~150
# candidatos consulta caché varias veces cada uno), eso eran cientos de
# relecturas del mismo archivo grande en una sola petición a /fichajes,
# confirmado como la causa real de la lentitud en producción el 2026-08-02.
_memory_cache_by_path = {}


def _load():
    cache = _memory_cache_by_path.get(CACHE_FILE)
    if cache is not None:
        return cache
    if not os.path.exists(CACHE_FILE):
        cache = {}
    else:
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}
    _memory_cache_by_path[CACHE_FILE] = cache
    return cache


def _save(data):
    _memory_cache_by_path[CACHE_FILE] = data
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


def peek(key, ttl=DEFAULT_TTL):
    """Como get_or_set pero sin ejecutar `fn()` si no hay caché vigente —
    para poder saber de antemano si conseguir un dato costaría una llamada
    nueva antes de decidir si hay presupuesto para ella. Devuelve
    (True, valor) si hay acierto de caché, (False, None) si no."""
    entry = _load().get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return True, entry["value"]
    return False, None
