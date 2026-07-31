"""Cruza tu plantilla guardada con datos reales de API-Football:
lesiones, sanciones, calendario y forma reciente.
"""
import datetime
from services import store, scoring
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


def sync_all():
    """Actualiza el cache de estado (lesión/sanción/calendario/forma/valor)
    para toda la plantilla.

    Devuelve (resultados, errores) sin lanzar excepción si un jugador falla,
    para que un fallo puntual no tumbe la sincronización completa.
    """
    client = ApiFootballClient()
    if not client.enabled:
        return {}, ["Falta configurar API_FOOTBALL_KEY en el archivo .env"]

    players = store.load_squad()
    cache = store.load_status_cache()
    errors = []
    standings = {}
    try:
        standings = client.get_standings()
    except ApiFootballError as e:
        errors.append(f"No se pudo leer la clasificación: {e}")

    team_injuries_cache = {}
    team_congestion_cache = {}

    for player in players:
        try:
            pid, team_id = _resolve_player(client, player)
            if not team_id:
                errors.append(f"No se encontró a '{player['name']}' en API-Football (revisa el nombre/equipo)")
                continue

            if team_id not in team_injuries_cache:
                team_injuries_cache[team_id] = client.get_team_injuries(team_id)
            injuries = team_injuries_cache[team_id]

            if team_id not in team_congestion_cache:
                try:
                    recent_fixtures = client.get_recent_fixtures_all_competitions(team_id)
                    team_congestion_cache[team_id] = scoring.fixture_congestion(recent_fixtures)
                except ApiFootballError:
                    team_congestion_cache[team_id] = {"count": None, "competitions": []}
            congestion = team_congestion_cache[team_id]

            status = "ok"
            reason = None
            for inj in injuries:
                if inj["player"]["id"] == pid:
                    status = "duda"
                    reason = inj["player"].get("reason") or inj["player"].get("type")
                    if reason and "suspen" in reason.lower():
                        status = "sancionado"
                    else:
                        status = "lesionado"
                    break

            fixtures = client.get_next_fixtures(team_id, scoring.HORIZON)
            swing = scoring.fixture_swing(fixtures, team_id, standings)
            next_fixture = swing["fixtures"][0] if swing["fixtures"] else None

            rating, starter_rate = None, None
            yellow_cards, penalty_taker = None, False
            try:
                stats = client.get_player_statistics(pid, team_id)
                if stats:
                    games = stats.get("games") or {}
                    rating = float(games["rating"]) if games.get("rating") else None
                    appearences = games.get("appearences") or 0
                    lineups = games.get("lineups") or 0
                    starter_rate = (lineups / appearences) if appearences else None
                    yellow_cards = (stats.get("cards") or {}).get("yellow")
                    penalty = stats.get("penalty") or {}
                    penalty_taker = scoring.is_penalty_taker(penalty.get("scored"), penalty.get("missed"))
            except (ApiFootballError, TypeError, ValueError):
                pass

            motivation = scoring.team_motivation_factor(standings.get(str(team_id)))
            card_risk = scoring.card_suspension_risk(yellow_cards)

            score = (
                scoring.player_score(
                    player["position"], rating, starter_rate, swing, congestion.get("count"),
                    motivation, penalty_taker,
                )
                if status == "ok" else None
            )
            value = scoring.value_for_money(score, player.get("price")) if status == "ok" else None

            cache[player["id"]] = {
                "status": status,
                "reason": reason,
                "rival": next_fixture["rival"] if next_fixture else None,
                "is_home": next_fixture["is_home"] if next_fixture else None,
                "fixture_date": next_fixture["date"] if next_fixture else None,
                "fixture_difficulty": swing["avg_goals_against_rivals"],
                "next_fixtures": swing["fixtures"],
                "rating": rating,
                "congestion_count": congestion.get("count"),
                "congestion_competitions": congestion.get("competitions"),
                "yellow_cards": yellow_cards,
                "card_risk": card_risk,
                "penalty_taker": penalty_taker,
                "low_motivation": motivation < 1.0,
                "score": score,
                "value": value,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }
        except ApiFootballError as e:
            errors.append(f"{player['name']}: {e}")
        except Exception as e:  # datos inesperados de la API, no debe romper el resto
            errors.append(f"{player['name']}: error inesperado ({e})")

    store.save_status_cache(cache)
    return cache, errors
