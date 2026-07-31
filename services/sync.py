"""Cruza tu plantilla guardada con datos reales de API-Football:
lesiones, sanciones, próximo rival y forma reciente.
"""
import datetime
from services import store
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
    store.update_player(player["id"], api_football_id=pid, api_football_team_id=team_id)
    return pid, team_id


def sync_all():
    """Actualiza el cache de estado (lesión/sanción/próximo rival/forma) para toda la plantilla.

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

    for player in players:
        try:
            pid, team_id = _resolve_player(client, player)
            if not team_id:
                errors.append(f"No se encontró a '{player['name']}' en API-Football (revisa el nombre/equipo)")
                continue

            if team_id not in team_injuries_cache:
                team_injuries_cache[team_id] = client.get_team_injuries(team_id)
            injuries = team_injuries_cache[team_id]

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

            fixture = client.get_next_fixture(team_id)
            rival, is_home, fixture_date = None, None, None
            difficulty = None
            if fixture:
                teams = fixture["teams"]
                is_home = teams["home"]["id"] == team_id
                rival_team = teams["away"] if is_home else teams["home"]
                rival = rival_team["name"]
                fixture_date = fixture["fixture"]["date"]
                rival_row = standings.get(rival_team["id"])
                if rival_row:
                    played = max(rival_row["all"]["played"], 1)
                    difficulty = round(rival_row["all"]["goals"]["against"] / played, 2)

            cache[player["id"]] = {
                "status": status,
                "reason": reason,
                "rival": rival,
                "is_home": is_home,
                "fixture_date": fixture_date,
                "fixture_difficulty": difficulty,
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }
        except ApiFootballError as e:
            errors.append(f"{player['name']}: {e}")
        except Exception as e:  # datos inesperados de la API, no debe romper el resto
            errors.append(f"{player['name']}: error inesperado ({e})")

    store.save_status_cache(cache)
    return cache, errors
