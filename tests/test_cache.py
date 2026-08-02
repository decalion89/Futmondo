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
