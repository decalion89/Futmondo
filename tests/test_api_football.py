"""Tests del cliente de API-Football (services/api_football.py): que
cualquier fallo de la petición HTTP se traduzca en ApiFootballError (la
única excepción que el resto de la app sabe capturar) en vez de dejar
escapar una excepción cruda de `requests` que tumbaría la petición entera.
Sin red real: se sustituye requests.get por una respuesta de mentira.
"""
import sys
import os

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import api_football


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, json_error=False):
        self.status_code = status_code
        self._json_data = json_data or {"response": []}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


def _client():
    return api_football.ApiFootballClient(api_key="fake-key")


def test_get_raises_clean_error_on_401(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(status_code=401))
    client = _client()
    with pytest.raises(api_football.ApiFootballError, match="rechazado la clave"):
        client._get("standings")


def test_get_raises_clean_error_on_403(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(status_code=403))
    client = _client()
    with pytest.raises(api_football.ApiFootballError, match="rechazado la clave"):
        client._get("standings")


def test_get_raises_clean_error_on_429_quota(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(status_code=429))
    client = _client()
    with pytest.raises(api_football.ApiFootballError, match="cuota diaria"):
        client._get("standings")


def test_get_raises_clean_error_on_other_http_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(status_code=500))
    client = _client()
    with pytest.raises(api_football.ApiFootballError, match="500"):
        client._get("standings")


def test_get_raises_clean_error_on_connection_failure(monkeypatch):
    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("dns failure")
    monkeypatch.setattr(requests, "get", _boom)
    client = _client()
    with pytest.raises(api_football.ApiFootballError, match="No se pudo conectar"):
        client._get("standings")


def test_get_raises_clean_error_on_invalid_json(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(status_code=200, json_error=True))
    client = _client()
    with pytest.raises(api_football.ApiFootballError, match="no se pudo leer"):
        client._get("standings")


def test_get_raises_clean_error_on_payload_errors_field(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(json_data={"errors": {"league": "invalid"}})
    )
    client = _client()
    with pytest.raises(api_football.ApiFootballError):
        client._get("standings")


def test_get_returns_response_payload_on_success(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(json_data={"response": [{"id": 1}]})
    )
    client = _client()
    assert client._get("standings") == [{"id": 1}]


def test_get_raises_when_disabled_without_key():
    client = api_football.ApiFootballClient(api_key=None)
    with pytest.raises(api_football.ApiFootballError, match="Falta API_FOOTBALL_KEY"):
        client._get("standings")
