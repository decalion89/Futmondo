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

from services import cache, scoring

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


FUTMONDO_MARKET_URL = "https://www.futbolfantasy.com/analytics/futmondo/mercado/social"
# Confirmado el 2026-08-02: la liga del usuario es modo "social"
# (championshipMode en /1/userteam/information real) — esta URL es
# específica de ese modo; la variante sin "/social" al final es para
# ligas en modo "clásico".
MARKET_REQUEST_TIMEOUT = 20  # esta página pesa unos 3MB (todos los jugadores de Futmondo en una tabla)


def parse_futmondo_market(html):
    """De /analytics/futmondo/mercado/social: histórico de precio real de
    TODOS los jugadores de Futmondo en una sola página — valor actual y la
    racha de días consecutivos en la misma dirección (`data-tendencia`,
    positivo = subiendo, negativo = bajando) junto con la variación en los
    últimos 7 días (`data-diferencia-pct7`), ya calculadas por la propia
    web desde el primer día — a diferencia de nuestro propio histórico de
    precio (services.futmondo.get_price_history), que tarda semanas en
    tener señal real porque solo empieza a acumularse desde que arranca
    esta app. Indexado por slug de nombre."""
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    for el in soup.select("tr.elemento_jugador"):
        name = el.get("data-nombre")
        if not name:
            continue
        slug = _slugify(name)

        def _num(attr):
            try:
                return float(el.get(attr))
            except (TypeError, ValueError):
                return None

        players[slug] = {
            "value": _num("data-valor"),
            "trend_streak_days": _num("data-tendencia"),
            "change_pct_7d": _num("data-diferencia-pct7"),
        }
    return players


def get_futmondo_market_data():
    """Todos los jugadores de Futmondo con su racha de precio real, en una
    sola petición cacheada un día completo."""
    def _fetch():
        try:
            resp = requests.get(FUTMONDO_MARKET_URL, headers=REQUEST_HEADERS, timeout=MARKET_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return {}
        return parse_futmondo_market(resp.text)

    return cache.get_or_set("futbolfantasy_futmondo_market", _fetch, ttl=CACHE_TTL)


SET_PIECES_URL = "https://www.futbolfantasy.com/analytics/balon-parado/jugadores"
# Confirmado el 2026-08-02 con datos reales (ej. Oyarzabal 7 penaltis,
# Muriqi 7, Gerard Moreno 3): quién ha lanzado penaltis/faltas directas de
# verdad, evidencia empírica en vez de una jerarquía editorial adivinada —
# y sin depender de ninguna clave de API-Football, que el usuario no
# quiere usar.


def parse_set_piece_takers(html):
    """De /analytics/balon-parado/jugadores: cuántos penaltis y faltas
    directas ha lanzado cada jugador de LaLiga — TODOS los jugadores en una
    sola página. Indexado por slug de nombre. Los sub-campos de precisión/
    goles (`-precisas-pct`, `-goles-pct`...) traen valores centinela raros
    cuando el jugador no ha lanzado ninguna (ej. "1000"), así que solo se
    usan los contadores brutos de intentos, que sí son fiables."""
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    for el in soup.select("tr.elemento_jugador"):
        name = el.get("data-nombre")
        if not name:
            continue
        slug = _slugify(name)

        def _int(attr):
            try:
                return int(float(el.get(attr)))
            except (TypeError, ValueError):
                return 0

        players[slug] = {
            "penalties_taken": _int("data-penaltis"),
            "direct_free_kicks_taken": _int("data-faltas-directas"),
            "corners_taken": _int("data-corners-colgados"),
        }
    return players


def get_set_piece_data():
    """Lanzadores de penaltis/faltas/corners de TODA LaLiga, en una sola
    petición cacheada un día completo."""
    def _fetch():
        try:
            resp = requests.get(SET_PIECES_URL, headers=REQUEST_HEADERS, timeout=MARKET_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return {}
        return parse_set_piece_takers(resp.text)

    return cache.get_or_set("futbolfantasy_set_pieces", _fetch, ttl=CACHE_TTL)


def find_player_set_pieces(player_name):
    """Intentos de penalti/falta directa/corner de un jugador concreto.
    None si no se encuentra (no si tiene 0 intentos — eso sigue
    devolviendo el dict con ceros, para distinguir "no lo lanza" de
    "no está en LaLiga")."""
    return _match_by_slug(get_set_piece_data(), player_name)


def find_player_market_momentum(player_name):
    """Racha de precio real de un jugador concreto (subida o bajada
    sostenida — mismos umbrales que scoring.price_momentum_flag, para que
    ambas fuentes avisen con el mismo criterio). None si no hay racha
    relevante o no se encuentra al jugador en la tabla."""
    data = _match_by_slug(get_futmondo_market_data(), player_name)
    if not data:
        return None
    streak = data.get("trend_streak_days")
    change_7d = data.get("change_pct_7d")
    if streak is None or change_7d is None:
        return None
    if abs(streak) < scoring.BUBBLE_MIN_STREAK_DAYS:
        return None
    cumulative_pct = change_7d / 100
    if abs(cumulative_pct) < scoring.BUBBLE_CUMULATIVE_THRESHOLD:
        return None
    return {
        "streak_days": abs(int(streak)),
        "cumulative_pct": round(cumulative_pct, 3),
        "direction": "up" if streak > 0 else "down",
    }


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
    encuentra.

    Probado el 2026-08-02: intenté tratar "el equipo se resolvió pero el
    jugador no aparece en la lista" como señal real de "no está en la
    pelea" (funcionó para Diego Conde, suplente del Betis) — pero al
    validarlo contra ~200 jugadores reales, jugadores caros y claramente
    relevantes (Isco 33,8M€, Hjulmand 34,9M€, Yeremay 26,6M€) también
    salían "ausentes", con la MISMA tasa de falsos positivos (15-32%) en
    todos los rangos de precio, no solo en jugadores baratos. La lista de
    futbolfantasy.com simplemente no cubre a todo el mundo — su ausencia
    no distingue "no va a jugar" de "no está en su página". Revertido:
    tratar la ausencia como señal fue un error, "sin dato" es lo honesto."""
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
