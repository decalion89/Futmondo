"""Recomendaciones de fichajes: cruza el mercado real de Futmondo con datos
de forma y calendario para priorizar altas por relación puntos/precio, y
señala qué jugadores de tu plantilla conviene vender.
"""
from services import scoring
from services.api_football import ApiFootballClient, ApiFootballError


def _score_candidate(client, standings, name, team_name, position):
    """Busca al jugador en API-Football y calcula su puntuación/forma."""
    matches = client.search_player(name, team_name=team_name)
    if not matches:
        return None
    match = matches[0]
    team_id = match["statistics"][0]["team"]["id"] if match.get("statistics") else None
    pid = match["player"]["id"]
    if not team_id:
        return None

    rating, starter_rate = None, None
    try:
        stats = client.get_player_statistics(pid, team_id)
    except ApiFootballError:
        stats = None
    if stats:
        games = stats.get("games") or {}
        rating = float(games["rating"]) if games.get("rating") else None
        appearences = games.get("appearences") or 0
        lineups = games.get("lineups") or 0
        starter_rate = (lineups / appearences) if appearences else None

    try:
        fixtures = client.get_next_fixtures(team_id, scoring.HORIZON)
    except ApiFootballError:
        fixtures = []
    swing = scoring.fixture_swing(fixtures, team_id, standings)
    score = scoring.player_score(position, rating, starter_rate, swing)
    next_fixture = swing["fixtures"][0] if swing["fixtures"] else None
    return {
        "rating": rating,
        "starter_rate": starter_rate,
        "score": score,
        "next_rival": next_fixture["rival"] if next_fixture else None,
    }


def rank_market(market_listings):
    """Puntúa cada jugador del mercado y lo ordena por puntos-por-millón
    (mejor relación calidad/precio primero)."""
    client = ApiFootballClient()
    if not client.enabled:
        return [], ["Falta configurar API_FOOTBALL_KEY para analizar el mercado"]

    errors = []
    try:
        standings = client.get_standings()
    except ApiFootballError as e:
        standings = {}
        errors.append(str(e))

    ranked = []
    for listing in market_listings:
        info = _score_candidate(client, standings, listing["name"], listing.get("team"), listing.get("position"))
        if not info:
            errors.append(f"No se encontró a '{listing['name']}' en API-Football")
            continue
        value = scoring.value_for_money(info["score"], listing.get("price"))
        ranked.append({**listing, **info, "value": value})

    ranked.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
    return ranked, errors


def sell_candidates(squad, status_cache, top=5):
    """Jugadores de tu plantilla peor posicionados para seguir aportando:
    primero los no disponibles (lesión/sanción), luego los de peor relación
    puntos/precio entre los disponibles."""
    unavailable, ranked_ok = [], []
    for p in squad:
        info = status_cache.get(p["id"], {})
        status = info.get("status")
        if status in ("lesionado", "sancionado", "duda"):
            unavailable.append({**p, **info})
        elif status == "ok" and info.get("value") is not None:
            ranked_ok.append({**p, **info})

    ranked_ok.sort(key=lambda r: r["value"])
    return unavailable, ranked_ok[:top]
