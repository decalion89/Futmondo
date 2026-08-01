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


def parse_team_injuries(html):
    """De la sección "Estado físico de la plantilla", extrae por jugador
    (indexado por slug) el motivo y la fecha de regreso ESTIMADA que ya
    calcula la propia web — más útil que el simple flag lesionado/no: no es
    lo mismo "vuelve la semana que viene" que "vuelve en dos meses" a la
    hora de decidir si vender o esperar."""
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.find("div", class_="lesionados_wrapper")
    if not wrapper:
        return {}
    injuries = {}
    for el in wrapper.select("div.elemento"):
        name_el = el.select_one("a.jugador")
        if not name_el:
            continue
        slug = (name_el.get("href") or "").rstrip("/").split("/")[-1]
        if not slug:
            continue
        reason_el = el.select_one("span.lesion")
        return_el = el.select_one("span[class*='gravedad-']")
        injuries[slug] = {
            "reason": reason_el.get_text(strip=True) if reason_el else None,
            "expected_return": return_el.get_text(strip=True) if return_el else None,
        }
    return injuries


def _parse_transfer_items(section, other_team_index):
    items = []
    for el in section.select("div.elemento"):
        name_el = el.select_one("a.jugador")
        if not name_el:
            continue
        slug = (name_el.get("href") or "").rstrip("/").split("/")[-1]
        if not slug:
            continue
        tag_el = el.select_one("span.mercado-tag-label")
        team_names = [t.get_text(strip=True) for t in el.select("span.mercado-equipo-nombre") if t.get_text(strip=True)]
        items.append({
            "name": name_el.get_text(strip=True),
            "slug": slug,
            "status": tag_el.get_text(strip=True) if tag_el else None,
            "other_team": team_names[other_team_index] if team_names else None,
        })
    return items


def parse_team_transfers(html):
    """De las secciones "Posibles fichajes"/"Posibles salidas" de la ficha
    de equipo: movimientos de mercado real en marcha que podrían afectar a
    los minutos de otros jugadores (una salida le abre hueco a alguien, un
    fichaje nuevo se lo puede quitar) antes de que se note en las
    estadísticas. Devuelve (incoming, outgoing)."""
    soup = BeautifulSoup(html, "html.parser")
    incoming, outgoing = [], []
    for header in soup.find_all("header", class_="title"):
        text = header.get_text(strip=True).lower()
        section = header.find_parent("section")
        if not section:
            continue
        if "posibles fichajes" in text:
            # Ruta origen -> equipo actual: el "otro" equipo es el primero.
            incoming = _parse_transfer_items(section, other_team_index=0)
        elif "posibles salidas" in text:
            # Ruta equipo actual -> destino: el "otro" equipo es el último.
            outgoing = _parse_transfer_items(section, other_team_index=-1)
    return incoming, outgoing


def get_team_page_data(team_name):
    """Todo lo que sacamos de la ficha de un equipo real (once probable,
    lesiones con fecha de regreso, fichajes/salidas en marcha) en UNA sola
    petición cacheada un día completo — evita pedir la misma página varias
    veces para cosas distintas. {lineup, injuries, incoming, outgoing}
    vacíos si el equipo no se reconoce o falla la petición."""
    team_slug = _resolve_team_slug(team_name)
    empty = {"lineup": {}, "injuries": {}, "incoming": [], "outgoing": []}
    if not team_slug:
        return empty

    def _fetch():
        try:
            html = _fetch_team_page(team_slug)
        except FutbolFantasyError:
            return empty
        incoming, outgoing = parse_team_transfers(html)
        return {
            "lineup": parse_team_lineup(html),
            "injuries": parse_team_injuries(html),
            "incoming": incoming,
            "outgoing": outgoing,
        }

    return cache.get_or_set(f"futbolfantasy_team:{team_slug}", _fetch, ttl=CACHE_TTL)


def get_team_lineup_probabilities(team_name):
    """Probabilidad de titularidad por jugador de un equipo real de LaLiga,
    indexada por slug de nombre. Nunca lanza excepción: es una capa de
    enriquecimiento opcional, no debe tumbar el resto de la app."""
    return get_team_page_data(team_name)["lineup"]


def _match_by_slug(index, player_name):
    """Coincidencia exacta de slug primero, luego parcial (nombres
    compuestos, apodos) — mismo criterio en todos los lookups por jugador."""
    if not index:
        return None
    target = _slugify(player_name)
    if target in index:
        return index[target]
    for slug, data in index.items():
        if target and (target in slug or slug in target):
            return data
    return None


def find_player_probability(player_name, team_name):
    """Probabilidad de titularidad de un jugador concreto. None si no se
    encuentra."""
    return _match_by_slug(get_team_page_data(team_name)["lineup"], player_name)


def find_player_injury_detail(player_name, team_name):
    """Motivo y fecha de regreso estimada de la lesión de un jugador
    concreto. None si no está en la lista de lesionados o no se encuentra."""
    return _match_by_slug(get_team_page_data(team_name)["injuries"], player_name)


def get_team_transfer_rumors(team_name):
    """(incoming, outgoing): movimientos de mercado real en marcha para un
    equipo — listas vacías si no hay o falla la petición."""
    data = get_team_page_data(team_name)
    return data["incoming"], data["outgoing"]


def find_player_transfer_rumor(player_name, team_name):
    """¿Aparece este jugador en la lista de "posibles salidas" de su
    equipo real? Señal directa de que podría dejar el club pronto — antes
    de que se note en ningún dato de rendimiento. None si no aparece."""
    _, outgoing = get_team_transfer_rumors(team_name)
    index = {item["slug"]: item for item in outgoing if item.get("slug")}
    return _match_by_slug(index, player_name)
