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

    def get_match_list(self):
        """Calendario de la jornada actual: partidos reales (equipo local/
        visitante, fecha) con cuotas de casas de apuestas por partido.
        Confirmado con datos reales (2026-08-01) en `/1/match/list`."""
        return self._post("/1/match/list")


# Confirmado con datos reales (2026-08-01): el campo de posición es `role`,
# en español y en minúsculas.
POSITION_MAP = {
    "portero": "POR",
    "defensa": "DEF",
    "centrocampista": "CEN",
    "delantero": "DEL",
}


def _map_position(role):
    if not role:
        return None
    return POSITION_MAP.get(str(role).strip().lower())


def _map_status(status):
    """Traduce el campo `status` real de Futmondo (visto: "injured2" para un
    lesionado) a nuestros códigos internos. No hemos visto todavía un
    ejemplo de sancionado, así que cualquier valor no reconocido pero no
    vacío se trata como "duda" para no perder la señal."""
    if not status:
        return None
    s = str(status).lower()
    if "injur" in s or "lesion" in s:
        return "lesionado"
    if "suspend" in s or "sancion" in s:
        return "sancionado"
    return "duda"


def normalize_roster(raw):
    """Convierte la respuesta cruda de /1/userteam/roster (o del mercado) a
    una lista simple de {name, position, team, price, futmondo_player_id,
    futmondo_status}.

    Campos confirmados con datos reales el 2026-08-01: `role` (posición en
    español), `team` (nombre del equipo como texto plano, no un objeto),
    `value` (precio), `status` (lesión/sanción). El campo `photo` trae solo
    un nombre de archivo (ej. "67011217.png"), no una URL completa — no
    sabemos todavía el dominio del CDN de imágenes de Futmondo, así que de
    momento no lo usamos como foto (la app ya usa la de API-Football como
    respaldo). Se mantienen alternativas por si el mercado usa nombres
    ligeramente distintos.
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
        team_name = team.get("name") if isinstance(team, dict) else (team or p.get("teamName") or p.get("club"))
        position = (
            _map_position(p.get("role"))
            or p.get("position")
            or p.get("positionName")
            or p.get("posId")
            or "?"
        )
        average = p.get("average") or {}
        normalized.append({
            "name": p.get("nickname") or p.get("name") or p.get("playerName") or "Desconocido",
            "position": position,
            "team": team_name or "?",
            "price": p.get("value") or p.get("clausule") or p.get("marketValue") or p.get("price"),
            "futmondo_player_id": p.get("id") or p.get("playerId"),
            "futmondo_team_id": p.get("teamId"),
            "photo_url": p.get("photoUrl") or p.get("image") or p.get("urlPhoto") or p.get("avatar"),
            "futmondo_status": _map_status(p.get("status")),
            # Forma/puntuación reales de Futmondo (0 en pretemporada, útiles
            # en cuanto arranque la liga): media de la temporada, media de
            # los últimos 5, puntos totales y rating de la última jornada.
            "futmondo_average": average.get("average"),
            "futmondo_average_last_five": average.get("averageLastFive"),
            "futmondo_points": p.get("points"),
            "futmondo_rating": p.get("rating"),
        })
    return normalized


def _normalize_team_name(name):
    """Quita conectores/artículos frecuentes para poder comparar nombres de
    equipo que Futmondo escribe distinto según el sitio (ej. "Racing" en la
    ficha del partido vs "Racing Santander" en la cuota, o "Atlético de
    Madrid" vs "Atlético Madrid")."""
    if not name:
        return ""
    n = str(name).lower()
    for token in (" de ", " fc ", " cf "):
        n = n.replace(token, " ")
    return " ".join(n.split())


def _matches_team(selection_name, team_name):
    a, b = _normalize_team_name(selection_name), _normalize_team_name(team_name)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def parse_match_odds(match, home_name, away_name):
    """A partir del bloque `odds.sels` de un partido de Futmondo (varias
    casas de apuestas por selección), calcula probabilidades implícitas de
    victoria local/empate/visitante, promediando entre casas y quitando el
    margen de la casa (normalizando a que sumen 1). None si no hay cuotas.

    Empareja por nombre "flexible" (ver `_matches_team`) porque el nombre
    del equipo en `homeTeam`/`awayTeam` no siempre coincide letra a letra
    con el nombre de la selección de cuota (viene de un proveedor de
    apuestas distinto)."""
    sels = ((match.get("odds") or {}).get("sels")) or []
    home_p = draw_p = away_p = None
    for sel in sels:
        name = sel.get("sn")
        odds_list = [o["c"] for o in (sel.get("odds") or []) if o.get("c")]
        if not odds_list or not name:
            continue
        avg_odd = sum(odds_list) / len(odds_list)
        implied = 1 / avg_odd if avg_odd else None
        if not implied:
            continue
        if name.strip().lower() == "draw":
            draw_p = implied
        elif _matches_team(name, home_name):
            home_p = implied
        elif _matches_team(name, away_name):
            away_p = implied

    total = sum(v for v in (home_p, draw_p, away_p) if v) or None
    if not total:
        return None
    return {
        "home": (home_p or 0) / total,
        "draw": (draw_p or 0) / total,
        "away": (away_p or 0) / total,
    }


def next_match_by_team(match_list_answer):
    """A partir de la respuesta de /1/match/list, indexa por id de equipo
    real de Futmondo (el mismo `teamId` que trae tu plantilla) el próximo
    rival, si juega en casa, la fecha, y la probabilidad de victoria
    implícita en las cuotas (para medir dificultad sin depender de otra
    API)."""
    matches = (match_list_answer or {}).get("matches") or []
    index = {}
    for m in matches:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        home_id, away_id = home.get("id"), away.get("id")
        probs = parse_match_odds(m, home.get("name"), away.get("name"))
        if home_id:
            index[home_id] = {
                "rival": away.get("name"),
                "is_home": True,
                "date": m.get("date"),
                "win_prob": probs["home"] if probs else None,
                "draw_prob": probs["draw"] if probs else None,
            }
        if away_id:
            index[away_id] = {
                "rival": home.get("name"),
                "is_home": False,
                "date": m.get("date"),
                "win_prob": probs["away"] if probs else None,
                "draw_prob": probs["draw"] if probs else None,
            }
    return index
