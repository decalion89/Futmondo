"""Recomendaciones de fichajes: cruza el mercado real de Futmondo con datos
de forma y calendario para priorizar altas por relación puntos/precio, y
señala qué jugadores de tu plantilla conviene vender.

Igual que en sync.py, Futmondo (forma propia + calendario con cuotas) es la
fuente PRINCIPAL — funciona sin ninguna clave de API-Football. Si tienes esa
clave configurada, se usa como capa extra (fatiga, riesgo de sanción,
motivación), pero no es obligatoria.
"""
import os
from services import scoring, cache, futbolfantasy
from services.api_football import ApiFootballClient, ApiFootballError
from services.futmondo import FutmondoError, normalize_roster, normalize_league_teams, next_match_by_team, collect_known_players

# Mínimo de jugadores disponibles en cada posición para poder completar
# CUALQUIERA de las formaciones habituales (services.scoring.FORMATIONS) —
# por debajo de esto, un rival ya no puede alinear un once legal en esa
# posición, no es solo que esté "corto de opciones".
MIN_PLAYERS_BY_POSITION = {
    "POR": min(f[0] for f in scoring.FORMATIONS),
    "DEF": min(f[1] for f in scoring.FORMATIONS),
    "CEN": min(f[2] for f in scoring.FORMATIONS),
    "DEL": min(f[3] for f in scoring.FORMATIONS),
}

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
    sea una caja negra y puedas decidir tú con el motivo delante.

    Riesgo/beneficio real de fichar a alguien se reduce a tres preguntas, en
    este orden — y las tres van SIEMPRE explícitas, nunca en silencio si no
    tenemos el dato: ¿va a jugar? ¿es titular fijo o solo puntual? ¿dónde
    saca los puntos (portería a cero si es DEF/POR, gol/asistencia si es
    CEN/DEL — la convención que usan las guías de estas ligas)?"""
    titular_probability = r.get("titular_probability")
    disagreement = r.get("lineup_disagreement")

    if r.get("low_confidence_fringe"):
        fringe_msg = (
            f"❓ Solo {titular_probability}% de probabilidad real de salir titular la próxima jornada "
            "(once probable de futbolfantasy.com) — el pts/M€ que ves no es de fiar aquí"
        ) if titular_probability is not None else (
            "❓ Precio mínimo de la plataforma y cero partidos reales todavía — "
            "sin apenas señal de que vaya a tener minutos, el pts/M€ que ves no es de fiar aquí"
        )
        return f"{disagreement}; {fringe_msg}" if disagreement else fringe_msg

    parts = []
    if disagreement:
        parts.append(disagreement)

    # 1. ¿Va a jugar? — la pregunta que manda sobre todas las demás: sin
    # minutos no hay puntos, por buena que sea la puntuación de al lado.
    if titular_probability is not None:
        parts.append(f"¿jugará? {titular_probability}% de probabilidad real de ser titular la próxima jornada")
    else:
        parts.append("⚠️ ¿jugará? sin dato real de titularidad — verifica alineaciones antes de fichar")

    # 2. ¿Es titular fijo o solo una racha puntual?
    if r.get("score_low_sample"):
        games = r.get("implied_games_played")
        plural = "s" if games != 1 else ""
        parts.append(f"solo {games} partido{plural} contabilizado{plural} todavía, no sabemos si es titular fijo")

    value = r.get("value")
    if value is not None:
        tag = " (estimado por precio, sin partidos jugados todavía)" if r.get("score_from_price") else ""
        parts.append(f"{value} pts/M€{tag}")

    # 3. ¿Dónde saca los puntos? Portería a cero para defensas/porteros,
    # gol/asistencia para centrocampistas/delanteros — no es la misma
    # lectura del mismo partido favorable.
    if r.get("next_rival"):
        vs = "vs" if r.get("is_home") else "@"
        rival_txt = f"próximo rival {vs} {r['next_rival']}"
        win_prob = r.get("win_prob")
        position = r.get("position")
        if win_prob is not None and win_prob >= 0.55:
            pct = round(win_prob * 100)
            if position in ("POR", "DEF"):
                rival_txt += f" (favorito, {pct}% de ganar — buena opción de portería a cero)"
            else:
                rival_txt += f" (favorito, {pct}% de ganar — buena opción de gol/asistencia)"
        elif win_prob is not None and win_prob <= 0.3:
            rival_txt += " (no es favorito, partido cuesta arriba)"
        parts.append(rival_txt)
    momentum = r.get("price_momentum")
    if momentum:
        pct = round(momentum["cumulative_pct"] * 100)
        parts.append(
            f"⚠️ precio subiendo {momentum['streak_days']} días seguidos (+{pct}% acumulado) — "
            "vigila si es mejora real o solo hype de la comunidad antes de pagar de más"
        )
    elif r.get("price_trend") == "up":
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
        # Once probable real de futbolfantasy.com — manda sobre el proxy de
        # precio para decidir si es un fichaje de relleno que no va a jugar.
        lineup_info = futbolfantasy.find_player_probability(listing.get("name"), listing.get("team"))
        titular_probability = lineup_info.get("probability") if lineup_info else None
        lineup_disagreement = scoring.detect_lineup_disagreement(
            listing.get("futmondo_status") or "ok", lineup_info,
        )
        # Precio mínimo de la plataforma + cero datos reales = sin apenas
        # señal de que vaya a jugar. Sin esto, dividir cualquier puntuación
        # entre un precio así de bajo dispara su pts/M€ por delante de
        # jugadores reales bien valorados, solo por aritmética del precio.
        low_confidence_fringe = scoring.is_low_confidence_fringe(
            listing.get("price"), has_real_data=raw_form is not None, titular_probability=titular_probability,
        )
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
            titular_probability=titular_probability,
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
            "titular_probability": titular_probability,
            "lineup_disagreement": lineup_disagreement,
            "score": score,
            "value": value,
            "max_bid": max_bid,
            "over_real_budget": real_budget_cap is not None and current_price is not None and current_price > real_budget_cap,
            "worth_bidding_more": worth_bidding_more,
            "team_limit_reached": team_limit_reached,
            "score_from_price": score_from_price,
            "score_low_sample": score_low_sample,
            "implied_games_played": games_played,
            "low_confidence_fringe": low_confidence_fringe,
            "price_trend": scoring.price_trend(listing.get("price"), listing.get("futmondo_price_change")),
        })

    # low_confidence_fringe va SIEMPRE detrás de cualquier candidato con
    # señal real (precio de verdad o partidos jugados), pase lo que pase con
    # su "valor" calculado — si no, el propio ratio pts/M€ los pone primero
    # por pura aritmética de dividir entre un precio mínimo, no porque el
    # motor tenga ningún indicio de que vayan a jugar.
    ranked.sort(key=lambda r: (
        r["team_limit_reached"], r["low_confidence_fringe"], r["value"] is None, -(r["value"] or 0),
    ))
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
            "real_budget_cap": None, "resale_lock_days": None, "my_rank": None, "total_teams": None,
            "clause_increase_pct": None, "next_match_index": {}, "position_price_index": {},
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
    clause_increase_pct = None
    my_rank = None
    total_teams = None
    try:
        raw_teams = client.get_league_teams()
        teams, configuration = normalize_league_teams(raw_teams)
        resale_lock_days = configuration.get("resale_lock_days")
        clause_increase_pct = configuration.get("clause_increase_pct")
        my_team = next((t for t in teams if t["id"] == client.team_id), None)
        if my_team:
            real_budget_cap = scoring.real_budget_max_bid(
                configuration.get("budget"), my_team.get("team_value"),
                configuration.get("max_bid_over_funds_pct"),
            )
        # Clasificación real por puntos (no por valor de equipo, que es el
        # orden de `teams`) — para saber si conviene jugar a "suelo" (vas
        # líder) o a "techo" (vas remontando). Antes de que arranque la
        # liga todos están a 0 puntos y el orden sería puro azar de
        # desempate, así que solo lo damos por válido si ya hay puntos
        # reales en juego.
        if teams and my_team and any((t.get("points") or 0) > 0 for t in teams):
            points_ranking = sorted(teams, key=lambda t: t.get("points") or 0, reverse=True)
            total_teams = len(points_ranking)
            my_rank = points_ranking.index(my_team) + 1
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
        "my_rank": my_rank, "total_teams": total_teams, "clause_increase_pct": clause_increase_pct,
        "next_match_index": next_match_index, "position_price_index": position_price_index,
    }


def scan_rival_weaknesses(client):
    """Escanea la plantilla de TODOS los rivales de tu liga (una llamada por
    rival) y detecta huecos reales por posición: menos jugadores disponibles
    de los que exige la formación habitual más exigente en esa posición, es
    decir, un rival que directamente NO PUEDE alinear un once legal ahí
    ahora mismo (no solo "va corto de opciones"). Sirve para decidir a quién
    bloquear en el mercado (el "clausulazo táctico") o contra quién puedes
    arriesgar más tu propia alineación.

    Implica una llamada por cada rival de tu liga, así que se cachea con un
    TTL corto (los estados de lesión/sanción no cambian cada minuto, pero sí
    de un día para otro)."""
    def _fetch():
        raw_teams = client.get_league_teams()
        teams, _ = normalize_league_teams(raw_teams)
        weaknesses = []
        for t in teams:
            if t.get("id") == client.team_id:
                continue
            try:
                roster = normalize_roster(client.get_roster(team_id=t["id"]))
            except FutmondoError:
                continue
            counts = {pos: 0 for pos in MIN_PLAYERS_BY_POSITION}
            for p in roster:
                position = p.get("position")
                status = p.get("futmondo_status") or "ok"
                if position in counts and status == "ok":
                    counts[position] += 1
            gaps = [
                {"position": pos, "available": counts[pos], "minimum": minimum}
                for pos, minimum in MIN_PLAYERS_BY_POSITION.items()
                if counts[pos] < minimum
            ]
            if gaps:
                weaknesses.append({"team_id": t.get("id"), "team_name": t.get("name"), "gaps": gaps})
        return weaknesses

    return cache.get_or_set("rival_weaknesses", _fetch, ttl=3 * 3600)


def _fetch_rival_owned_players(client):
    """Plantilla de TODOS los rivales de tu liga, cada jugador etiquetado
    con `owner_team` (quién lo tiene) — a diferencia de collect_known_players
    (que mezcla rivales + mercado sin diferenciar, para el índice de
    precios), aquí necesitamos saber de quién es cada uno para poder decir
    "fichar a X del equipo de Y". Cacheado: implica una llamada por rival."""
    def _fetch():
        try:
            raw_teams = client.get_league_teams()
            teams, _ = normalize_league_teams(raw_teams)
        except FutmondoError:
            return []
        players = []
        for t in teams:
            if t.get("id") == client.team_id:
                continue
            try:
                roster = normalize_roster(client.get_roster(team_id=t["id"]))
            except FutmondoError:
                continue
            for p in roster:
                p["owner_team"] = t.get("name")
            players.extend(roster)
        return players

    return cache.get_or_set(f"rival_owned_players:{client.championship_id}", _fetch, ttl=3 * 3600)


def scan_rival_targets(
    client, squad, benchmark_value, next_match_index, real_budget_cap,
    position_price_index, clause_increase_pct, top=10,
):
    """Jugadores de OTROS managers de tu liga que valdría la pena intentar
    fichar por clausulazo — se puntúan exactamente igual que el mercado
    abierto (mismo motor, mismas señales reales), así un objetivo bueno no
    se te escapa solo porque nunca sale a subasta libre.

    El importe de clausulazo (`clause_estimate`) SOLO se calcula si
    `clause_increase_pct` tiene una forma plausible de porcentaje (0-300%)
    — el campo real de Futmondo para esto (`enablingClause`) nunca se ha
    verificado contra datos reales y en producción ha dado valores que
    generaban importes NEGATIVOS, así que ante la duda no se muestra un
    número en vez de mostrar uno probablemente erróneo. Confirma siempre el
    importe exacto en Futmondo antes de pujar."""
    rival_players = _fetch_rival_owned_players(client)
    if not rival_players:
        return [], []

    ranked, errors = rank_market(
        rival_players, benchmark_value, squad, next_match_index, real_budget_cap, position_price_index,
    )

    plausible_pct = clause_increase_pct is not None and 0 <= clause_increase_pct <= 3
    for r in ranked:
        current_price = scoring.parse_price(r.get("price"))
        if current_price and plausible_pct:
            r["clause_estimate"] = round(current_price * (1 + clause_increase_pct))
        else:
            r["clause_estimate"] = None

    candidates = [
        r for r in ranked
        if not r.get("team_limit_reached") and not r.get("low_confidence_fringe")
    ]
    return candidates[:top], errors
