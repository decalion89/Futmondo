"""Cliente para futbolfantasy.com: probabilidad real de titularidad por
jugador (el "once probable" de cada equipo), publicada por su equipo
editorial a partir de ruedas de prensa y entrenamientos — la señal más
directa que existe para anticipar quién va a jugar de verdad la próxima
jornada, en vez de adivinarlo por su precio (ver services.scoring.
is_low_confidence_fringe, el parche que esto viene a mejorar con dato
real).

No es una API oficial — no hay endpoint público documentado — pero a
diferencia de FBref, sus términos de uso no prohíben expresamente el
acceso automatizado y su robots.txt está abierto a todos los user-agents.
Se trata con cuidado de todos modos: una petición por equipo (no por
jugador), cacheada un día completo, con un User-Agent de navegador normal
y sin reintentos agresivos — las probabilidades se actualizan según pasa
la semana, no hace falta consultarlas más a menudo que eso.
"""
import re
import unicodedata

import requests
from bs4 import BeautifulSoup

from services import cache

BASE_URL = "https://www.futbolfantasy.com/laliga/equipos"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
CACHE_TTL = 24 * 3600  # un día — no hace falta consultar más a menudo

# Slugs reales de equipo de futbolfantasy.com para LaLiga 2026/27 (confirmados
# contra su sitemap el 2026-08-01, incluyendo los tres ascendidos). La clave
# es el nombre de equipo tal como lo usa Futmondo en tu plantilla/mercado.
TEAM_SLUGS = {
    "Alavés": "alaves",
    "Athletic de Bilbao": "athletic",
    "Athletic Club": "athletic",
    "Atlético de Madrid": "atletico",
    "Atlético Madrid": "atletico",
    "Barcelona": "barcelona",
    "Betis": "betis",
    "Real Betis": "betis",
    "Celta de Vigo": "celta",
    "Celta": "celta",
    "Deportivo de la Coruña": "deportivo",
    "Deportivo": "deportivo",
    "Elche": "elche",
    "Espanyol": "espanyol",
    "Getafe": "getafe",
    "Levante": "levante",
    "Málaga": "malaga",
    "Osasuna": "osasuna",
    "Racing": "racing",
    "Racing de Santander": "racing",
    "Rayo Vallecano": "rayo-vallecano",
    "Real Madrid": "real-madrid",
    "Real Sociedad": "real-sociedad",
    "Sevilla": "sevilla",
    "Valencia": "valencia",
    "Villarreal": "villarreal",
}


class FutbolFantasyError(Exception):
    pass


def _slugify(name):
    """Normaliza un nombre (jugador o equipo) a un slug comparable con las
    URLs de futbolfantasy.com: sin acentos, minúsculas, separado por
    guiones. Es un matching mucho más fiable que comparar texto libre con
    apodos/orden de nombre distinto."""
    normalized = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return normalized


def _resolve_team_slug(team_name):
    if not team_name:
        return None
    if team_name in TEAM_SLUGS:
        return TEAM_SLUGS[team_name]
    # Fallback por si Futmondo usa una variante de nombre que no está en el
    # mapa de arriba: compara la forma normalizada contra los slugs conocidos.
    target = _slugify(team_name)
    for slug in set(TEAM_SLUGS.values()):
        if slug.replace("-", "") in target.replace("-", "") or target.replace("-", "") in slug.replace("-", ""):
            return slug
    return None


REQUEST_TIMEOUT = 8  # una plantilla puede tocar 10-15 equipos distintos en un solo sync —
# corto a propósito para que uno lento no se coma todo el tiempo del worker


def _fetch_team_page(team_slug):
    url = f"{BASE_URL}/{team_slug}"
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise FutbolFantasyError(f"No se pudo consultar futbolfantasy.com: {e}")
    return resp.text


def parse_team_lineup(html):
    """De la ficha de equipo, extrae por jugador (indexado por slug de su
    URL de ficha): probabilidad de titularidad (0-100) y flags de
    lesión/sanción/no disponible que la propia web ya calcula. Cada
    jugador aparece dos veces en la página (vista de campo + vista de
    lista) — nos quedamos con la primera aparición e ignoramos entradas sin
    slug real ('#', vacío)."""
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    for el in soup.find_all(attrs={"data-probabilidad": True}):
        href = el.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        if not slug or slug == "#" or slug in players:
            continue
        prob_raw = (el.get("data-probabilidad") or "").rstrip("%").strip()
        if not prob_raw.isdigit():
            continue
        players[slug] = {
            "probability": int(prob_raw),
            "injured": el.get("data-lesion") not in (None, "-1"),
            "suspended": el.get("data-sancionado") not in (None, "0"),
            "unavailable": el.get("data-nodisponible") not in (None, "0"),
        }
    return players


def get_team_lineup_probabilities(team_name):
    """Probabilidad de titularidad por jugador de un equipo real de LaLiga,
    indexada por slug de nombre — cacheada un día completo. {} si el equipo
    no se reconoce o la petición falla (nunca lanza excepción: es una capa
    de enriquecimiento opcional, no debe tumbar el resto de la app)."""
    team_slug = _resolve_team_slug(team_name)
    if not team_slug:
        return {}

    def _fetch():
        try:
            html = _fetch_team_page(team_slug)
            return parse_team_lineup(html)
        except FutbolFantasyError:
            return {}

    return cache.get_or_set(f"futbolfantasy_lineup:{team_slug}", _fetch, ttl=CACHE_TTL)


def find_player_probability(player_name, team_name):
    """Busca la probabilidad de titularidad de un jugador concreto por
    nombre — coincidencia exacta de slug primero, luego parcial (nombres
    compuestos, apodos). None si no se encuentra."""
    lineup = get_team_lineup_probabilities(team_name)
    if not lineup:
        return None
    target = _slugify(player_name)
    if target in lineup:
        return lineup[target]
    for slug, data in lineup.items():
        if target and (target in slug or slug in target):
            return data
    return None
