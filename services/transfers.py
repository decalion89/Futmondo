"""Recomendaciones de fichajes: cruza el mercado real de Futmondo con datos
de forma y calendario para priorizar altas por relación puntos/precio, y
señala qué jugadores de tu plantilla conviene vender.

Igual que en sync.py, Futmondo (forma propia + calendario con cuotas) es la
fuente PRINCIPAL — funciona sin ninguna clave de API-Football. Si tienes esa
clave configurada, se usa como capa extra (fatiga, riesgo de sanción,
motivación), pero no es obligatoria.
"""
import os
from services import scoring
from services.api_football import ApiFootballClient, ApiFootballError

DEFAULT_MAX_SAME_TEAM = 2  # normas de "Sparka grande y libre!!"; ajustable por si tu liga usa otro límite


def _max_same_team():
    value = os.environ.get("FUTMONDO_MAX_SAME_TEAM")
    return int(value) if value else DEFAULT_MAX_SAME_TEAM


def _team_counts(squad):
    counts = {}
    for p in squad:
        counts[p.get("team")] = counts.get(p.get("team"), 0) + 1
    return counts


def _clean_futmondo_form(value):
    return value if value else None


def _score_with_api_football(client, standings, name, team_name):
    """Enriquecimiento opcional vía API-Football: rating, fatiga, riesgo de
    sanción, motivación, goles/asistencias. Devuelve None si no encuentra
    al jugador o algo falla — no debe tumbar el resto."""
    try:
        matches = client.search_player(name, team_name=team_name)
    except ApiFootballError:
        return None
    if not matches:
        return None
    match = matches[0]
    team_id = match["statistics"][0]["team"]["id"] if match.get("statistics") else None
    pid = match["player"]["id"]
    if not team_id:
        return None

    rating, starter_rate = None, None
    yellow_cards, penalty_taker = None, False
    goals, assists, appearences = None, None, None
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
        yellow_cards = (stats.get("cards") or {}).get("yellow")
        penalty = stats.get("penalty") or {}
        penalty_taker = scoring.is_penalty_taker(penalty.get("scored"), penalty.get("missed"))
        goals_stats = stats.get("goals") or {}
        goals = goals_stats.get("total")
        assists = goals_stats.get("assists")

    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None, "fixtures": []}
    try:
        fixtures = client.get_next_fixtures(team_id, scoring.HORIZON)
        swing = scoring.fixture_swing(fixtures, team_id, standings)
    except ApiFootballError:
        pass

    congestion_count = None
    try:
        recent_fixtures = client.get_recent_fixtures_all_competitions(team_id)
        congestion_count = scoring.fixture_congestion(recent_fixtures)["count"]
    except ApiFootballError:
        pass

    motivation = scoring.team_motivation_factor(standings.get(str(team_id)))
    next_fixture = swing["fixtures"][0] if swing["fixtures"] else None

    return {
        "rating": rating,
        "starter_rate": starter_rate,
        "swing": swing,
        "congestion_count": congestion_count,
        "card_risk": scoring.card_suspension_risk(yellow_cards),
        "penalty_taker": penalty_taker,
        "low_motivation": motivation < 1.0,
        "motivation": motivation,
        "goals": goals,
        "assists": assists,
        "appearences": appearences,
        "next_rival": next_fixture["rival"] if next_fixture else None,
        "photo_url": match["player"].get("photo"),
    }


def rank_market(market_listings, benchmark_value=scoring.DEFAULT_VALUE_BENCHMARK, squad=None,
                 next_match_index=None, real_budget_cap=None):
    """Puntúa cada jugador del mercado, lo ordena por puntos-por-millón
    (mejor relación calidad/precio primero) y calcula hasta qué puja
    máxima compensaría pagar. `real_budget_cap` (si se pasa) es el tope
    físico real de tu liga (fondos + % configurado) — manda sobre el tope
    por rentabilidad si es más bajo. También marca los candidatos que no
    podrías fichar por el límite de jugadores del mismo equipo real."""
    api_client = ApiFootballClient()
    api_available = api_client.enabled
    errors = []
    standings = {}
    if api_available:
        try:
            standings = api_client.get_standings()
        except ApiFootballError as e:
            errors.append(f"API-Football no disponible ahora mismo ({e}) — seguimos solo con datos de Futmondo")
            api_available = False

    team_counts = _team_counts(squad or [])
    max_same_team = _max_same_team()
    next_match_index = next_match_index or {}

    ranked = []
    for listing in market_listings:
        position = listing.get("position")
        futmondo_form = _clean_futmondo_form(listing.get("futmondo_average_last_five")) \
            or _clean_futmondo_form(listing.get("futmondo_average"))
        futmondo_match = next_match_index.get(listing.get("futmondo_team_id"))
        futmondo_win_prob = futmondo_match.get("win_prob") if futmondo_match else None
        next_rival = (futmondo_match or {}).get("rival")

        extra = None
        if api_available:
            extra = _score_with_api_football(api_client, standings, listing["name"], listing.get("team"))

        swing = (extra or {}).get("swing") or {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
        score = scoring.player_score(
            position,
            (extra or {}).get("rating"),
            (extra or {}).get("starter_rate"),
            swing,
            (extra or {}).get("congestion_count"),
            (extra or {}).get("motivation", 1.0),
            (extra or {}).get("penalty_taker", False),
            (extra or {}).get("goals"),
            (extra or {}).get("assists"),
            (extra or {}).get("appearences"),
            futmondo_form=futmondo_form,
            futmondo_win_prob=futmondo_win_prob,
        )

        value = scoring.value_for_money(score, listing.get("price"))
        value_max_bid = scoring.max_recommended_bid(score, benchmark_value)
        max_bid = value_max_bid
        if real_budget_cap is not None:
            max_bid = min(v for v in (value_max_bid, real_budget_cap) if v is not None) \
                if value_max_bid is not None else real_budget_cap
        current_price = scoring.parse_price(listing.get("price"))
        worth_bidding_more = (
            max_bid is not None and current_price is not None and current_price < max_bid
        )
        team_limit_reached = team_counts.get(listing.get("team"), 0) >= max_same_team

        ranked.append({
            **listing,
            "rating": (extra or {}).get("rating"),
            "photo_url": listing.get("photo_url") or (extra or {}).get("photo_url"),
            "congestion_count": (extra or {}).get("congestion_count"),
            "card_risk": (extra or {}).get("card_risk", False),
            "penalty_taker": (extra or {}).get("penalty_taker", False),
            "low_motivation": (extra or {}).get("low_motivation", False),
            "next_rival": next_rival or (extra or {}).get("next_rival"),
            "score": score,
            "value": value,
            "max_bid": max_bid,
            "over_real_budget": real_budget_cap is not None and current_price is not None and current_price > real_budget_cap,
            "worth_bidding_more": worth_bidding_more,
            "team_limit_reached": team_limit_reached,
        })

    ranked.sort(key=lambda r: (r["team_limit_reached"], r["value"] is None, -(r["value"] or 0)))
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
