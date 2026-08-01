"""Últimas noticias oficiales de Futmondo (magazine.futmondo.com) — el blog
oficial del juego corre sobre WordPress y expone su API REST estándar y
pública (/wp-json/wp/v2/posts), sin necesidad de scraping ni credenciales:
es contenido de la propia Futmondo, publicado explícitamente para ser
consumido así.
"""
import html
import re

import requests

from services import cache

BASE_URL = "https://magazine.futmondo.com/wp-json/wp/v2/posts"
CACHE_TTL = 6 * 3600  # las noticias oficiales no cambian cada minuto


def _strip_html(text):
    """El título/extracto de WordPress vienen con etiquetas HTML y
    entidades — nos interesa el texto plano para mostrarlo en la app."""
    without_tags = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(without_tags).strip()


def get_latest_posts(limit=5):
    """Últimas `limit` noticias del magazine de Futmondo, más reciente
    primero — {title, excerpt, link, date}. [] si falla la petición (nunca
    lanza excepción: es contenido informativo opcional, no debe tumbar
    ninguna página)."""
    def _fetch():
        try:
            resp = requests.get(BASE_URL, params={"per_page": limit}, timeout=10)
            resp.raise_for_status()
            posts = resp.json()
        except (requests.exceptions.RequestException, ValueError):
            return []
        return [
            {
                "title": _strip_html((p.get("title") or {}).get("rendered", "")),
                "excerpt": _strip_html((p.get("excerpt") or {}).get("rendered", ""))[:220],
                "link": p.get("link"),
                "date": p.get("date"),
            }
            for p in posts
            if isinstance(p, dict)
        ]

    return cache.get_or_set(f"futmondo_magazine:{limit}", _fetch, ttl=CACHE_TTL)
