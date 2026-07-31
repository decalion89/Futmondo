"""Cliente ligero para API-Football (https://www.api-football.com/).

Se usa solo para datos reales de fútbol (lesiones, sanciones, calendario,
forma de los jugadores). Los datos de tu plantilla de Futmondo (precios,
qué jugadores tienes) se gestionan a mano en la app porque Futmondo no
tiene una API pública.
"""
import os
import datetime
import requests

from services import cache

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
        if resp.status_code == 429:
            raise ApiFootballError(
                "Has agotado tu cuota diaria de API-Football (plan gratuito: 100 peticiones/día). "
                "Vuelve a intentarlo mañana, o sincroniza con menos frecuencia."
            )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise ApiFootballError(str(payload["errors"]))
        return payload.get("response", [])

    def _cached_get(self, path, params, ttl=cache.DEFAULT_TTL):
        """Como `_get`, pero reutiliza la respuesta si ya se pidió lo mismo
        hace menos de `ttl` segundos (protege la cuota diaria gratuita)."""
        key = f"{path}:{sorted((params or {}).items())}"
        return cache.get_or_set(key, lambda: self._get(path, params), ttl=ttl)

    def search_player(self, name, team_name=None):
        """Busca un jugador por nombre dentro de la liga/temporada configurada."""
        results = self._cached_get("players", {
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
        return self._cached_get("injuries", {
            "league": self.league_id,
            "season": self.season,
            "team": team_id,
        })

    def get_next_fixtures(self, team_id, count=5):
        """Próximos `count` partidos de liga de un equipo (para medir la
        racha de calendario, no solo el partido inmediato)."""
        return self._cached_get("fixtures", {
            "team": team_id,
            "next": count,
            "league": self.league_id,
            "season": self.season,
        })

    def get_player_statistics(self, player_id, team_id=None):
        """Estadísticas de la temporada para un jugador (rating medio, titularidades...)."""
        params = {"id": player_id, "league": self.league_id, "season": self.season}
        if team_id:
            params["team"] = team_id
        results = self._cached_get("players", params)
        if not results:
            return None
        stats = results[0].get("statistics") or []
        return stats[0] if stats else None

    def get_standings(self):
        response = self._cached_get("standings", {
            "league": self.league_id,
            "season": self.season,
        })
        if not response:
            return {}
        table = response[0]["league"]["standings"][0]
        # Claves como texto: sobreviven intactas al paso por la caché en
        # disco (JSON convierte las claves int a str igualmente).
        return {str(row["team"]["id"]): row for row in table}
