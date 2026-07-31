"""Cliente no oficial para la API de Futmondo (https://api.futmondo.com).

Reverse-engineered a partir del proyecto open-source vicenteqa/futmondo-utils
(https://github.com/vicenteqa/futmondo-utils). No hay login por usuario/
contraseña: cada petición POST lleva un token+userid de sesión que se
obtienen inspeccionando tu propio navegador ya logueado (ver README).

Es una API no documentada oficialmente: puede cambiar sin aviso y el token
puede caducar. Úsala solo para leer tus propios datos.
"""
import os
import requests

BASE_URL = "https://api.futmondo.com"


class FutmondoError(Exception):
    pass


class FutmondoClient:
    def __init__(self, token=None, user_id=None, championship_id=None, team_id=None):
        self.token = token or os.environ.get("FUTMONDO_TOKEN")
        self.user_id = user_id or os.environ.get("FUTMONDO_USER_ID")
        self.championship_id = championship_id or os.environ.get("FUTMONDO_CHAMPIONSHIP_ID")
        self.team_id = team_id or os.environ.get("FUTMONDO_TEAM_ID")

    @property
    def enabled(self):
        return bool(self.token and self.user_id)

    def _post(self, endpoint, query=None):
        if not self.enabled:
            raise FutmondoError(
                "Faltan FUTMONDO_TOKEN / FUTMONDO_USER_ID en el archivo .env "
                "(consulta el README para capturarlos desde el navegador)"
            )
        body = {
            "header": {"token": self.token, "userid": self.user_id},
            "query": {
                k: v
                for k, v in {
                    "championshipId": self.championship_id,
                    "userteamId": self.team_id,
                    **(query or {}),
                }.items()
                if v is not None
            },
        }
        try:
            resp = requests.post(f"{BASE_URL}{endpoint}", json=body, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FutmondoError(f"Fallo de red hablando con Futmondo: {e}") from e

        payload = resp.json()
        answer = payload.get("answer")
        if answer is None or (isinstance(answer, dict) and answer.get("error")):
            raise FutmondoError(
                "Futmondo devolvió un error (probablemente el token ha caducado, "
                f"vuelve a capturarlo): {payload}"
            )
        return answer

    def get_roster(self):
        """Tu plantilla real de Futmondo (jugadores fichados)."""
        return self._post("/1/userteam/roster")

    def get_market(self):
        """Mercado de fichajes actual de tu liga."""
        return self._post("/1/market/players", {"type": "market"})

    def get_player_summary(self, player_id):
        return self._post("/1/player/summary", {"playerId": player_id})

    def get_championship_teams(self):
        return self._post("/2/championship/teams")


def normalize_roster(raw):
    """Intenta convertir la respuesta cruda de /1/userteam/roster a una lista
    simple de {name, position, team, price, futmondo_player_id}.

    La forma exacta del JSON de Futmondo no está documentada públicamente,
    así que esto es un mapeo best-effort con varios nombres de campo
    probables. Si tu plantilla no aparece bien tras importar, revisa
    data/futmondo_raw_roster.json (se guarda en cada importación) para ver
    la forma real y ajustar este mapeo.
    """
    candidates = raw.get("players") if isinstance(raw, dict) else None
    if candidates is None and isinstance(raw, dict):
        for key in ("roster", "userTeamPlayers", "teamPlayers"):
            if key in raw:
                candidates = raw[key]
                break
    if candidates is None and isinstance(raw, list):
        candidates = raw
    candidates = candidates or []

    normalized = []
    for p in candidates:
        if not isinstance(p, dict):
            continue
        team = p.get("team")
        team_name = team.get("name") if isinstance(team, dict) else (p.get("teamName") or p.get("club"))
        normalized.append({
            "name": p.get("nickname") or p.get("name") or p.get("playerName") or "Desconocido",
            "position": p.get("position") or p.get("positionName") or p.get("posId") or "?",
            "team": team_name or "?",
            "price": p.get("value") or p.get("clausule") or p.get("marketValue") or p.get("price"),
            "futmondo_player_id": p.get("id") or p.get("playerId"),
        })
    return normalized
