"""Cliente ligero para API-Football (https://www.api-football.com/).

Se usa solo para datos reales de fútbol (lesiones, sanciones, calendario,
forma de los jugadores). Los datos de tu plantilla de Futmondo (precios,
qué jugadores tienes) se gestionan a mano en la app porque Futmondo no
tiene una API pública.
"""
import os
import datetime
import requests

BASE_URL = "https://v3.football.api-sports.io"


def _current_season(today=None):
    today = today or datetime.date.today()
    # Las temporadas europeas empiezan en verano (julio/agosto).
    return today.year if today.month >= 7 else today.year - 1


class ApiFootballError(Exception):
    pass


class ApiFootballClient:
    def __init__(self, api_key=None, league_id=None, season=None):
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY")
        self.league_id = int(league_id or os.environ.get("API_FOOTBALL_LEAGUE_ID") or 140)
        season_env = season or os.environ.get("API_FOOTBALL_SEASON")
        self.season = int(season_env) if season_env else _current_season()

    @property
    def enabled(self):
        return bool(self.api_key)

    def _get(self, path, params=None):
        if not self.enabled:
            raise ApiFootballError("Falta API_FOOTBALL_KEY en el archivo .env")
        headers = {"x-apisports-key": self.api_key}
        resp = requests.get(f"{BASE_URL}/{path}", headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise ApiFootballError(str(payload["errors"]))
        return payload.get("response", [])

    def search_player(self, name, team_name=None):
        """Busca un jugador por nombre dentro de la liga/temporada configurada."""
        results = self._get("players", {
            "search": name,
            "league": self.league_id,
            "season": self.season,
        })
        if team_name:
            results = [
                r for r in results
                if r.get("statistics") and r["statistics"][0]["team"]["name"].lower() == team_name.lower()
            ] or results
        return results

    def get_team_injuries(self, team_id):
        """Lesionados y sancionados actuales de un equipo."""
        return self._get("injuries", {
            "league": self.league_id,
            "season": self.season,
            "team": team_id,
        })

    def get_next_fixture(self, team_id):
        fixtures = self._get("fixtures", {
            "team": team_id,
            "next": 1,
            "league": self.league_id,
            "season": self.season,
        })
        return fixtures[0] if fixtures else None

    def get_standings(self):
        response = self._get("standings", {
            "league": self.league_id,
            "season": self.season,
        })
        if not response:
            return {}
        table = response[0]["league"]["standings"][0]
        return {row["team"]["id"]: row for row in table}
