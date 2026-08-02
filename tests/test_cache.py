"""Tests de services/cache.py: caché simple en disco con expiración."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import cache


def test_peek_misses_without_prior_get_or_set(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_FILE", str(tmp_path / "cache.json"))
    hit, value = cache.peek("nunca-visto")
    assert hit is False
    assert value is None


def test_peek_hits_after_get_or_set(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_FILE", str(tmp_path / "cache.json"))
    cache.get_or_set("clave", lambda: "valor real", ttl=3600)
    hit, value = cache.peek("clave", ttl=3600)
    assert hit is True
    assert value == "valor real"


def test_peek_misses_once_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_FILE", str(tmp_path / "cache.json"))
    cache.get_or_set("clave", lambda: "valor real", ttl=3600)
    hit, value = cache.peek("clave", ttl=0)
    assert hit is False
    assert value is None


def test_peek_does_not_call_any_function():
    # A diferencia de get_or_set, peek no recibe (ni podría ejecutar) fn().
    import inspect
    assert "fn" not in inspect.signature(cache.peek).parameters


def test_load_does_not_reread_file_after_first_access(tmp_path, monkeypatch):
    # Confirmado el 2026-08-02 como causa real de lentitud en producción:
    # antes, cada get_or_set/peek releía el archivo entero desde disco. Si
    # de verdad se sigue leyendo en cada llamada, borrar el archivo tras el
    # primer acceso rompería la siguiente lectura — con la memoización, no.
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(cache, "CACHE_FILE", str(cache_file))
    cache.get_or_set("clave", lambda: "primer valor", ttl=3600)
    os.remove(cache_file)  # si algo releyera el archivo ahora, fallaría
    hit, value = cache.peek("clave", ttl=3600)
    assert hit is True
    assert value == "primer valor"


def test_different_cache_files_do_not_share_memory(tmp_path, monkeypatch):
    # Los tests que redirigen CACHE_FILE a un tmp_path distinto no deben
    # ver la copia en memoria de otro (aislamiento entre tests/paths).
    file_a = str(tmp_path / "a.json")
    file_b = str(tmp_path / "b.json")
    monkeypatch.setattr(cache, "CACHE_FILE", file_a)
    cache.get_or_set("clave", lambda: "valor A", ttl=3600)
    monkeypatch.setattr(cache, "CACHE_FILE", file_b)
    hit, value = cache.peek("clave", ttl=3600)
    assert hit is False
    assert value is None
