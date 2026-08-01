"""Tests de services/futmondo_magazine.py: limpieza de HTML (pura) y
get_latest_posts con requests.get sustituido, sin red real."""
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import futmondo_magazine as mag


def test_strip_html_removes_tags_and_unescapes_entities():
    assert mag._strip_html("<p>Hola &amp; adiós</p>") == "Hola & adiós"
    assert mag._strip_html("Texto <strong>en negrita</strong> normal") == "Texto en negrita normal"


def test_strip_html_handles_empty_input():
    assert mag._strip_html("") == ""
    assert mag._strip_html(None) == ""


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_get_latest_posts_maps_wordpress_fields(monkeypatch):
    payload = [
        {
            "title": {"rendered": "Título <b>de prueba</b>"},
            "excerpt": {"rendered": "<p>Resumen de la noticia.</p>"},
            "link": "https://magazine.futmondo.com/noticia/",
            "date": "2026-08-01T02:05:33",
        }
    ]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload))
    posts = mag.get_latest_posts(limit=1)
    assert posts == [{
        "title": "Título de prueba",
        "excerpt": "Resumen de la noticia.",
        "link": "https://magazine.futmondo.com/noticia/",
        "date": "2026-08-01T02:05:33",
    }]


def test_get_latest_posts_empty_on_request_failure(monkeypatch):
    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("no network")
    monkeypatch.setattr(requests, "get", _boom)
    assert mag.get_latest_posts(limit=99) == []


def test_get_latest_posts_empty_on_invalid_json(monkeypatch):
    class _BadResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _BadResponse())
    assert mag.get_latest_posts(limit=98) == []
