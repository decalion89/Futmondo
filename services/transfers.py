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
from services.futmondo import FutmondoError, normalize_roster, normalize_league_teams, next_match_by_team, collect_known_players

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


def build_reason(r):
    """Frase corta explicando POR QUÉ destaca (o no) este candidato, a partir
    de las señales que ya calculamos para él — para que la recomendación no
    sea una caja negra y puedas decidir tú con el motivo delante."""
    parts = []
    value = r.get("value")
    if value is not None:
        tag = " (estimado por precio, sin partidos jugados todavía)" if r.get("score_from_price") else ""
        parts.append(f"{value} pts/M€{tag}")
    if r.get("score_low_sample"):
        games = r.get("implied_games_played")
        plural = "s" if games != 1 else ""
        parts.append(f"dato con margen: solo {games} partido{plural} contabilizado{plural} todavía, no sabemos si es titular fijo")
    if r.get("next_rival"):
        vs = "vs" if r.get("is_home") else "@"
        rival_txt = f"próximo rival {vs} {r['next_rival']}"
        win_prob = r.get("win_prob")
        if win_prob is not None and win_prob >= 0.55:
            rival_txt += f" (favorito, {round(win_prob * 100)}% de ganar según las cuotas)"
        elif win_prob is not None and win_prob <= 0.3:
            rival_txt += " (no es favorito, partido cuesta arriba)"
        parts.append(rival_txt)
    if r.get("price_trend") == "up":
        parts.append("precio subiendo, podría encarecerse si esperas")
    elif r.get("price_trend") == "down":
        parts.append("precio bajando, buen momento para entrar")
    if r.get("penalty_taker"):
        parts.append("lanza penaltis")
    if r.get("card_risk"):
        parts.append("a una amarilla de sanción")
    if r.get("congestion_count") and r["congestion_count"] >= 2:
        parts.append(f"{r['congestion_count']} partidos en 10 días, riesgo de rotación")
    if r.get("team_limit_reached"):
        parts.append(f"ya tienes el máximo de {r.get('team')} en tu plantilla")
    if not parts:
        return "Buena puntuación reciente para su precio, sin más señales destacadas todavía."
    return "; ".join(parts).capitalize()


def rank_market(market_listings, benchmark_value=scoring.DEFAULT_VALUE_BENCHMARK, squad=None,
                 next_match_index=None, real_budget_cap=None, position_price_index=None):
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
        raw_form = _clean_futmondo_form(listing.get("futmondo_average_last_five")) \
            or _clean_futmondo_form(listing.get("futmondo_average"))
        games_played = scoring.implied_games_played(listing.get("futmondo_points"), listing.get("futmondo_average"))
        preseason_base = scoring.price_percentile_base(listing.get("price"), position, position_price_index)
        score_from_price = raw_form is None
        if score_from_price:
            futmondo_form = preseason_base
        else:
            futmondo_form = scoring.shrink_form_estimate(raw_form, games_played, preseason_base)
        score_low_sample = raw_form is not None and games_played is not None \
            and games_played < scoring.LOW_SAMPLE_GAMES_THRESHOLD
        futmondo_match = next_match_index.get(listing.get("futmondo_team_id")) \
            or next_match_index.get(listing.get("team"))
        futmondo_win_prob = futmondo_match.get("win_prob") if futmondo_match else None
        next_rival = (futmondo_match or {}).get("rival")
        next_is_home = (futmondo_match or {}).get("is_home")

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
            "is_home": next_is_home,
            "win_prob": futmondo_win_prob,
            "score": score,
            "value": value,
            "max_bid": max_bid,
            "over_real_budget": real_budget_cap is not None and current_price is not None and current_price > real_budget_cap,
            "worth_bidding_more": worth_bidding_more,
            "team_limit_reached": team_limit_reached,
            "score_from_price": score_from_price,
            "score_low_sample": score_low_sample,
            "implied_games_played": games_played,
            "price_trend": scoring.price_trend(listing.get("price"), listing.get("futmondo_price_change")),
        })

    ranked.sort(key=lambda r: (r["team_limit_reached"], r["value"] is None, -(r["value"] or 0)))
    for r in ranked:
        r["reason"] = build_reason(r)
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
            row = {**p, **info}
            row["reason"] = row.get("reason") or {
                "lesionado": "Lesionado según Futmondo, no puntúa mientras dure",
                "sancionado": "Sancionado, no puede jugar",
                "duda": "Duda para el próximo partido",
            }.get(status, "No disponible ahora mismo")
            unavailable.append(row)
        elif status == "ok" and info.get("value") is not None:
            ranked_ok.append({**p, **info})

    ranked_ok.sort(key=lambda r: r["value"])
    for r in ranked_ok[:top]:
        r["reason"] = f"Peor relación puntos/precio de tu plantilla ({r['value']} pts/M€) — ese dinero rendiría más en otro sitio"
    return unavailable, ranked_ok[:top]


def full_market_ranking(client, squad, status_cache):
    """Hace todo el trabajo de cruzar el mercado real de Futmondo con tu
    plantilla y liga: lo llaman tanto Fichajes (tabla completa) como el
    resumen de recomendaciones del Dashboard, para no duplicar esta lógica
    en dos sitios. Nunca lanza excepción — degrada devolviendo listas vacías
    y acumulando el motivo en `errors`, igual que el resto de la app."""
    errors = []
    listings = []
    next_match_index = {}

    if not client.enabled:
        return {
            "ranked": [], "errors": errors, "benchmark_value": scoring.DEFAULT_VALUE_BENCHMARK,
            "real_budget_cap": None, "resale_lock_days": None,
        }

    try:
        raw = client.get_market()
        listings = normalize_roster(raw)
    except FutmondoError as e:
        errors.append(str(e))
    try:
        match_data = client.get_match_list()
        next_match_index = next_match_by_team(match_data)
    except FutmondoError as e:
        errors.append(f"No se pudo leer el calendario de Futmondo: {e}")

    squad_values = [status_cache.get(p["id"], {}).get("value") for p in squad]
    benchmark_value = scoring.squad_value_benchmark(squad_values)

    position_price_index = {}
    try:
        position_price_index = scoring.build_position_price_index(collect_known_players(client) + squad)
    except FutmondoError as e:
        errors.append(f"No se pudo leer precios de referencia de la liga: {e}")

    real_budget_cap = None
    resale_lock_days = None
    try:
        raw_teams = client.get_league_teams()
        teams, configuration = normalize_league_teams(raw_teams)
        resale_lock_days = configuration.get("resale_lock_days")
        my_team = next((t for t in teams if t["id"] == client.team_id), None)
        if my_team:
            real_budget_cap = scoring.real_budget_max_bid(
                configuration.get("budget"), my_team.get("team_value"),
                configuration.get("max_bid_over_funds_pct"),
            )
    except FutmondoError as e:
        errors.append(f"No se pudo leer la configuración de tu liga: {e}")

    ranked = []
    if listings:
        ranked, rank_errors = rank_market(
            listings, benchmark_value, squad, next_match_index, real_budget_cap, position_price_index,
        )
        errors.extend(rank_errors)

    return {
        "ranked": ranked, "errors": errors, "benchmark_value": benchmark_value,
        "real_budget_cap": real_budget_cap, "resale_lock_days": resale_lock_days,
    }
