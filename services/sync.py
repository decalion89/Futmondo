"""Cruza tu plantilla guardada con datos reales: calendario/cuotas y forma
propia de Futmondo (siempre que tengas token), enriquecido opcionalmente con
lesiones/sanciones/estadísticas de API-Football si tienes esa clave.

Futmondo es la fuente PRINCIPAL (calendario con cuotas reales, estado de
lesión/sanción, media de puntos propia) porque no depende de ningún límite
de cuota ajeno y es más directa. API-Football es una CAPA OPCIONAL que
añade fatiga por Champions/Europa/Copa, riesgo de sanción por amarillas y
motivación por clasificación — si no está configurada o falla, la app sigue
funcionando solo con Futmondo.
"""
import datetime
from services import store, scoring, futbolfantasy
from services.futmondo import (
    FutmondoClient, FutmondoError, next_match_by_team, collect_known_players, get_lastseasons_prior,
)
from services.api_football import ApiFootballClient, ApiFootballError


def _resolve_player(client, player):
    """Encuentra el id de API-Football y el id del equipo para un jugador, cacheándolo."""
    if player.get("api_football_id") and player.get("api_football_team_id"):
        return player["api_football_id"], player["api_football_team_id"]

    matches = client.search_player(player["name"], team_name=player.get("team"))
    if not matches:
        return None, None

    match = matches[0]
    pid = match["player"]["id"]
    team_id = match["statistics"][0]["team"]["id"] if match.get("statistics") else None
    updates = {"api_football_id": pid, "api_football_team_id": team_id}
    if not player.get("photo_url"):
        photo = match["player"].get("photo")
        if photo:
            updates["photo_url"] = photo
    store.update_player(player["id"], **updates)
    return pid, team_id


def _clean_futmondo_form(value):
    """0 en pretemporada (o sin partidos jugados) no es una forma real
    todavía — trátalo como "sin dato" para no anclar la puntuación a 0."""
    return value if value else None


def sync_all():
    """Actualiza el cache de estado (lesión/sanción/calendario/forma/valor)
    para toda la plantilla.

    Devuelve (resultados, errores) sin lanzar excepción si un jugador falla,
    para que un fallo puntual no tumbe la sincronización completa.
    """
    futmondo_client = FutmondoClient()
    api_client = ApiFootballClient()
    api_available = api_client.enabled

    if not futmondo_client.enabled and not api_available:
        return {}, ["Configura FUTMONDO_TOKEN o API_FOOTBALL_KEY en el .env (o ambos)"]

    players = store.load_squad()
    cache = store.load_status_cache()
    score_history = store.load_score_history()
    today = datetime.date.today().isoformat()
    errors = []

    standings = {}
    if api_available:
        try:
            standings = api_client.get_standings()
        except ApiFootballError as e:
            errors.append(f"API-Football no disponible ahora mismo ({e}) — seguimos solo con datos de Futmondo")
            api_available = False

    next_match_index = {}
    position_price_index = {}
    if futmondo_client.enabled:
        try:
            match_data = futmondo_client.get_match_list()
            next_match_index = next_match_by_team(match_data)
        except FutmondoError as e:
            errors.append(f"No se pudo leer el calendario de Futmondo: {e}")
        try:
            known_players = collect_known_players(futmondo_client) + players
            position_price_index = scoring.build_position_price_index(known_players)
        except FutmondoError as e:
            errors.append(f"No se pudo leer precios de referencia de la liga: {e}")

    team_injuries_cache = {}
    team_congestion_cache = {}

    for player in players:
        try:
            status = player.get("futmondo_status") or "ok"
            reason = "Marcado por Futmondo" if player.get("futmondo_status") else None

            pid, team_id = None, None
            if api_available:
                try:
                    pid, team_id = _resolve_player(api_client, player)
                except ApiFootballError:
                    pid, team_id = None, None

            # Cruce con lesiones/sanciones de API-Football solo si Futmondo
            # no lo había marcado ya (Futmondo manda, es más directo).
            if api_available and team_id and status == "ok":
                if team_id not in team_injuries_cache:
                    team_injuries_cache[team_id] = api_client.get_team_injuries(team_id)
                for inj in team_injuries_cache[team_id]:
                    if inj["player"]["id"] == pid:
                        reason = inj["player"].get("reason") or inj["player"].get("type")
                        status = "sancionado" if reason and "suspen" in reason.lower() else "lesionado"
                        break

            congestion = {"count": None, "competitions": []}
            if api_available and team_id:
                if team_id not in team_congestion_cache:
                    try:
                        recent_fixtures = api_client.get_recent_fixtures_all_competitions(team_id)
                        team_congestion_cache[team_id] = scoring.fixture_congestion(recent_fixtures)
                    except ApiFootballError:
                        team_congestion_cache[team_id] = {"count": None, "competitions": []}
                congestion = team_congestion_cache[team_id]

            swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None, "fixtures": []}
            if api_available and team_id:
                try:
                    fixtures = api_client.get_next_fixtures(team_id, scoring.HORIZON)
                    swing = scoring.fixture_swing(fixtures, team_id, standings)
                except ApiFootballError:
                    pass

            rating, starter_rate = None, None
            yellow_cards, penalty_taker = None, False
            goals, assists, appearences = None, None, None
            if api_available and pid:
                try:
                    stats = api_client.get_player_statistics(pid, team_id)
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
                except (ApiFootballError, TypeError, ValueError):
                    pass

            # Lanzador de penaltis/faltas directas real (futbolfantasy.com,
            # sin depender de tener configurada API-Football) — se combina
            # con la señal de API-Football si también está disponible.
            set_pieces = futbolfantasy.find_player_set_pieces(player["name"])
            penalty_taker = penalty_taker or bool(set_pieces and set_pieces.get("penalties_taken"))
            free_kick_taker = bool(set_pieces and set_pieces.get("direct_free_kicks_taken"))

            motivation = 1.0
            if api_available and team_id:
                motivation = scoring.team_motivation_factor(standings.get(str(team_id)))
            card_risk = scoring.card_suspension_risk(yellow_cards)

            # Calendario: preferimos el partido real + cuotas de Futmondo
            # (dato directo de tu propia liga); si no hay, caemos al
            # calendario/estadísticas de API-Football.
            futmondo_match = next_match_index.get(player.get("futmondo_team_id")) \
                or next_match_index.get(player.get("team"))
            futmondo_win_prob = futmondo_match.get("win_prob") if futmondo_match else None
            api_fixture = swing["fixtures"][0] if swing["fixtures"] else None

            next_rival = (futmondo_match or {}).get("rival") or (api_fixture or {}).get("rival")
            next_is_home = (futmondo_match or {}).get("is_home") if futmondo_match else (api_fixture or {}).get("is_home")
            next_date = (futmondo_match or {}).get("date") or (api_fixture or {}).get("date")

            raw_form = _clean_futmondo_form(player.get("futmondo_average_last_five")) \
                or _clean_futmondo_form(player.get("futmondo_average"))
            games_played = scoring.implied_games_played(
                player.get("futmondo_points"), player.get("futmondo_average"),
            )
            # Rendimiento REAL de la temporada anterior (modo `presstats`,
            # confirmado el 2026-08-02) como base de pretemporada — mucho
            # mejor prior que el precio cuando existe. Tu plantilla es
            # pequeña (15-18 jugadores), así que se consulta siempre, sin el
            # tope de llamadas nuevas que sí hace falta para listas grandes
            # (mercado/rivales, ver services.transfers._historical_priors).
            historical = None
            if futmondo_client.enabled:
                player_id = player.get("futmondo_player_id")
                own_price = scoring.parse_price(player.get("price"))
                if player_id and own_price and own_price > scoring.FUTMONDO_FLOOR_PRICE:
                    historical = get_lastseasons_prior(futmondo_client, player_id)
            if historical:
                preseason_base = historical["average"]
                preseason_base_source = "historical"
            else:
                preseason_base = scoring.price_percentile_base(
                    player.get("price"), player["position"], position_price_index,
                )
                preseason_base_source = "price" if preseason_base is not None else None
            if raw_form is None:
                # Sin ni un partido jugado todavía: el precio es la única
                # señal que tenemos.
                futmondo_form = preseason_base
            else:
                # Hay dato real, pero lo regresionamos hacia el precio-base
                # si todavía respaldan pocos partidos — no sabemos si va a
                # ser titular, así que no confiamos del todo en 1-2 partidos.
                futmondo_form = scoring.shrink_form_estimate(raw_form, games_played, preseason_base)
            score_low_sample = raw_form is not None and games_played is not None \
                and games_played < scoring.LOW_SAMPLE_GAMES_THRESHOLD

            # Once probable real de futbolfantasy.com — la señal más directa
            # que existe de si va a jugar la próxima jornada, mejor que
            # cualquier proxy por precio o media histórica. Nunca lanza
            # excepción: si el equipo no se reconoce o falla la petición,
            # simplemente no hay dato y seguimos con el resto de señales.
            lineup_info = futbolfantasy.find_player_probability(player["name"], player.get("team"))
            titular_probability = lineup_info.get("probability") if lineup_info else None
            low_confidence_fringe = scoring.is_low_confidence_fringe(
                player.get("price"), has_real_data=raw_form is not None, titular_probability=titular_probability,
            )
            # Alerta temprana: Futmondo (fuente oficial) y futbolfantasy.com
            # (periodismo real, más rápido a veces) pueden no estar de
            # acuerdo todavía — eso es justo la ventana en la que te enteras
            # antes que un rival que solo mire una fuente.
            lineup_disagreement = scoring.detect_lineup_disagreement(status, lineup_info)

            # Detalle real de la lesión (motivo + fecha de regreso estimada)
            # cuando Futmondo ya lo marca como no disponible — mismo fetch
            # de equipo que ya hacíamos arriba, cero peticiones nuevas.
            if status in ("lesionado", "duda"):
                injury_detail = futbolfantasy.find_player_injury_detail(player["name"], player.get("team"))
                if injury_detail:
                    detail_parts = [p for p in (injury_detail.get("reason"), injury_detail.get("expected_return")) if p]
                    if detail_parts:
                        reason = " — ".join(detail_parts)

            # ¿Su equipo real lo tiene en la lista de posibles salidas?
            # Señal directa de que podría dejar el club pronto, antes de
            # que se note en ningún dato de rendimiento.
            transfer_rumor = futbolfantasy.find_player_transfer_rumor(player["name"], player.get("team"))

            score = None
            value = None
            consistency = None
            if status == "ok":
                score = scoring.player_score(
                    player["position"], rating, starter_rate, swing, congestion.get("count"),
                    motivation, penalty_taker, goals, assists, appearences,
                    futmondo_form=futmondo_form, futmondo_win_prob=futmondo_win_prob,
                    titular_probability=titular_probability,
                )
                value = scoring.value_for_money(score, player.get("price"))
                # Vamos guardando un punto de puntuación por día — todavía no
                # hay jornadas jugadas para calcular una varianza real (eso
                # exige varias semanas de histórico), pero así empezamos a
                # acumular el dato desde ya en vez de improvisarlo más tarde.
                player_history = store.add_score_entry(score_history, player["id"], today, score)
                consistency = scoring.score_consistency(player_history)

            cache[player["id"]] = {
                "status": status,
                "reason": reason,
                "rival": next_rival,
                "is_home": next_is_home,
                "fixture_date": next_date,
                "fixture_difficulty": swing["avg_goals_against_rivals"],
                "fixture_win_prob": futmondo_win_prob,
                "next_fixtures": swing["fixtures"],
                "rating": rating,
                "futmondo_form": futmondo_form,
                "score_from_price": raw_form is None,
                "preseason_base_source": preseason_base_source if raw_form is None else None,
                "historical_games": historical["games"] if historical else None,
                "historical_season": historical["season"] if historical else None,
                "score_low_sample": score_low_sample,
                "implied_games_played": games_played,
                "congestion_count": congestion.get("count"),
                "congestion_competitions": congestion.get("competitions"),
                "yellow_cards": yellow_cards,
                "card_risk": card_risk,
                "penalty_taker": penalty_taker,
                "free_kick_taker": free_kick_taker,
                "low_motivation": motivation < 1.0,
                "score": score,
                "value": value,
                "score_consistency": consistency,
                "titular_probability": titular_probability,
                "low_confidence_fringe": low_confidence_fringe,
                "lineup_disagreement": lineup_disagreement,
                "transfer_rumor": transfer_rumor,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }
        except Exception as e:  # un fallo puntual no debe tumbar el resto
            errors.append(f"{player['name']}: error inesperado ({e})")

    store.save_status_cache(cache)
    store.save_score_history(score_history)
    return cache, errors
