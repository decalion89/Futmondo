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

from services import cache

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

    def get_roster(self, team_id=None):
        """Tu plantilla real de Futmondo (jugadores fichados). Si pasas
        `team_id`, trae la plantilla de OTRO participante de tu liga en vez
        de la tuya (confirmado con datos reales: funciona igual, útil para
        ver qué tienen los rivales antes de pujar)."""
        extra = {"userteamId": team_id} if team_id else None
        return self._post("/1/userteam/roster", extra)

    def get_market(self):
        """Mercado de fichajes actual de tu liga."""
        return self._post("/1/market/players", {"type": "market"})

    def get_player_summary(self, player_id):
        """Ficha completa de un jugador: incluye `prices`, su historial de
        precio día a día (no solo la variación puntual del roster/mercado)
        — confirmado con datos reales (2026-08-01)."""
        return self._post("/1/player/summary", {"playerId": player_id})

    def get_player_fullprofile(self, player_id):
        """Ficha "Estadísticas" del jugador tal cual se ve en la app:
        goles, minutos jugados, pases, entradas, y el histórico de
        puntuación de temporadas anteriores — capturado inspeccionando la
        pestaña de red del navegador el 2026-08-02, no está en el proyecto
        open-source del que partió este cliente."""
        return self._post("/1/player/fullprofile", {"playerId": player_id})

    def get_player_matches(self, player_id):
        """Historial partido a partido del jugador — encontrado por el
        usuario en la pestaña Red del navegador el 2026-08-02, pendiente
        de confirmar su estructura exacta."""
        return self._post("/2/player/matches", {"playerId": player_id})

    def get_player_statistics(self, player_id):
        """Estadísticas detalladas del jugador (goles, pases, entradas...)
        — encontrado por el usuario en la pestaña Red del navegador el
        2026-08-02, pendiente de confirmar su estructura exacta."""
        return self._post("/2/player/statistics", {"playerId": player_id})

    def get_player_lastseasons(self, player_id):
        """Puntuación por temporada de años anteriores (total, partidos
        jugados, media local/visitante) — encontrado por el usuario en la
        pestaña Red del navegador el 2026-08-02, pendiente de confirmar su
        estructura exacta."""
        return self._post("/2/player/lastseasons", {"playerId": player_id})

    def get_pressroom(self):
        """Actividad reciente del mercado de tu liga: quién ha puesto a
        quién en venta (con vendedor, precio, pujas ya recibidas) —
        confirmado en `/1/locker/pressroom`. Inteligencia competitiva: te
        enteras de movimientos de tus rivales sin tener que estar mirando
        el mercado a cada rato."""
        return self._post("/1/locker/pressroom")

    def get_championship_teams(self):
        return self._post("/2/championship/teams")

    def get_league_teams(self):
        """Todos los participantes de tu liga (nombre, foto, valor de
        equipo) y la configuración exacta de la liga (presupuesto, días de
        retención antes de poder revender, % máximo de puja sobre fondos,
        etc.) — confirmado en `/2/championship/teams`."""
        return self._post("/2/championship/teams")

    def get_match_list(self):
        """Calendario de la jornada actual: partidos reales (equipo local/
        visitante, fecha) con cuotas de casas de apuestas por partido.
        Confirmado con datos reales (2026-08-01) en `/1/match/list`."""
        return self._post("/1/match/list")

    def get_championship_players(self):
        """TODOS los jugadores de tu liga en una sola llamada — plantillas de
        cada rival, jugadores sin dueño y mercado, ya sea que estén fichados
        o no (`userteamId`/`userteam` presente o no) — confirmado en
        `/5/league/championshipplayers` el 2026-08-02 (513 jugadores para
        una liga de LaLiga completa). Sustituye a tener que pedir la
        plantilla de cada rival una por una."""
        return self._post("/5/league/championshipplayers")

    def get_real_teams(self):
        """Los 20 equipos reales de LaLiga con su id interno de Futmondo
        (distinto del id de equipo-manager) — confirmado en
        `/1/league/championshipteams` el 2026-08-02. Sirve para traducir el
        `teamId` de `get_championship_players()` a un nombre de equipo."""
        return self._post("/1/league/championshipteams")


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
    """Traduce el campo `status` real de Futmondo a nuestros códigos
    internos. Confirmado contra datos reales el 2026-08-02: "injured"/
    "injured2" (lesionado), "redcard" (tarjeta roja, sancionado para el
    próximo partido), "doubt" (duda), "ok"/"" (disponible). Cualquier otro
    valor no vacío y no reconocido se trata como "duda" para no perder la
    señal."""
    if not status:
        return None
    s = str(status).lower()
    if s == "ok":
        return None
    if "injur" in s or "lesion" in s:
        return "lesionado"
    if "suspend" in s or "sancion" in s or "redcard" in s or "card" in s:
        return "sancionado"
    if "doubt" in s or "duda" in s:
        return "duda"
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
            # Solo presentes en jugadores del mercado (no en tu plantilla):
            # cuándo cierra la puja y cuántas pujas lleva ya.
            "futmondo_expiration": p.get("expirationDate"),
            "futmondo_bids": p.get("numberOfBids"),
            # Variación de precio reciente y precio al que lo compraste (si
            # fue una compra activa tuya por mercado) — para especular
            # comprando barato antes de que suba / vendiendo en el pico.
            "futmondo_price_change": p.get("change"),
            "futmondo_buy_price": p.get("buyPrice"),
            # Precio EXACTO de clausulazo, tal cual lo calcula Futmondo —
            # sustituye a cualquier estimación por porcentaje cuando está
            # disponible. Confirmado el 2026-08-02 que el campo `clause` NO
            # aparece para jugadores del reparto inicial de pretemporada
            # que su dueño nunca ha comprado activamente (`owner.buy == 0`)
            # — probablemente solo se calcula tras actividad real de
            # mercado. Se espera que empiece a poblarse solo, sin tocar
            # este código, en cuanto haya transacciones reales o arranque
            # la temporada.
            "futmondo_clause_price": (p.get("clause") or {}).get("price"),
            # Forma partido a partido (más reciente al final) que ya
            # calcula la propia Futmondo — en cuanto haya jornadas jugadas,
            # es mejor que cualquier histórico que construyamos nosotros
            # desde cero.
            "futmondo_fitness_history": average.get("fitness"),
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
    """A partir de la respuesta de /1/match/list, indexa el próximo rival,
    si juega en casa, la fecha, y la probabilidad de victoria implícita en
    las cuotas (para medir dificultad sin depender de otra API).

    Se indexa TANTO por id de equipo (el `teamId` que trae tu plantilla)
    COMO por nombre de equipo (porque el mercado, a diferencia de tu
    plantilla, no trae `teamId` — solo el nombre) — así funciona el cruce
    venga de donde venga el jugador."""
    matches = (match_list_answer or {}).get("matches") or []
    index = {}
    for m in matches:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        home_id, away_id = home.get("id"), away.get("id")
        home_name, away_name = home.get("name"), away.get("name")
        probs = parse_match_odds(m, home_name, away_name)
        home_info = {
            "rival": away_name,
            "is_home": True,
            "date": m.get("date"),
            "win_prob": probs["home"] if probs else None,
            "draw_prob": probs["draw"] if probs else None,
        }
        away_info = {
            "rival": home_name,
            "is_home": False,
            "date": m.get("date"),
            "win_prob": probs["away"] if probs else None,
            "draw_prob": probs["draw"] if probs else None,
        }
        if home_id:
            index[home_id] = home_info
        if home_name:
            index[home_name] = home_info
        if away_id:
            index[away_id] = away_info
        if away_name:
            index[away_name] = away_info
    return index


def normalize_league_teams(raw):
    """De la respuesta de /2/championship/teams saca la lista de
    participantes (managers rivales) ordenada por valor de equipo, y la
    configuración real de la liga en campos con nombre claro."""
    answer = raw or {}
    teams = [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "photo": t.get("photo") or None,
            "team_value": t.get("teamValue"),
            "points": t.get("points"),
            "is_me": False,  # se marca desde fuera comparando con tu FUTMONDO_TEAM_ID
        }
        for t in (answer.get("teams") or [])
    ]
    teams.sort(key=lambda t: t.get("team_value") or 0, reverse=True)

    cfg = answer.get("configuration") or {}
    configuration = {
        "budget": cfg.get("budget"),
        "starting_players": cfg.get("numberOfPlayers"),
        "max_roster_size": cfg.get("maxPlayersInRoster"),
        "money_per_point": cfg.get("moneyPerPoint"),
        "money_per_ranking": cfg.get("moneyPerRanking"),
        "bid_duration_days": cfg.get("bidDuration"),
        "market_slots": cfg.get("marketPlayers"),
        "resale_lock_days": cfg.get("playerRetention"),
        "max_bid_over_funds_pct": cfg.get("mnmp"),
        "clause_increase_pct": cfg.get("enablingClause"),
    }
    return teams, configuration


def real_team_names_by_id(raw):
    """De /1/league/championshipteams (los 20 equipos reales de LaLiga,
    confirmado el 2026-08-02): {id de equipo real: nombre}, para traducir el
    `teamId` de `get_championship_players()` — ahí solo llega el id, no el
    nombre."""
    teams = raw if isinstance(raw, list) else []
    return {t.get("id"): t.get("name") for t in teams if isinstance(t, dict) and t.get("id")}


def normalize_championship_players(raw, team_names_by_id=None):
    """De /5/league/championshipplayers (confirmado el 2026-08-02): TODOS
    los jugadores de tu liga, fichados por algún rival o no, con la misma
    forma que normalize_roster() más `owner_team` (nombre del manager que lo
    tiene, ya viene como texto — es el campo `userteam`) y
    `owner_userteam_id`. Pasa `team_names_by_id` (de real_team_names_by_id)
    si necesitas el nombre del equipo real; si no, se queda con el id tal
    cual."""
    team_names_by_id = team_names_by_id or {}
    players = (raw or {}).get("players") or []
    prepared = [
        {**p, "team": team_names_by_id.get(p.get("teamId")) or p.get("teamId")}
        for p in players if isinstance(p, dict)
    ]
    normalized = normalize_roster(prepared)
    for out, src in zip(normalized, prepared):
        out["owner_team"] = src.get("userteam")
        out["owner_userteam_id"] = src.get("userteamId")
    return normalized


def collect_known_players(client):
    """Reúne TODOS los jugadores de tu liga (fichados por rivales, libres o
    en el mercado) en una sola llamada a /5/league/championshipplayers — tu
    propia plantilla la añade quien llame a esto, ya la tiene local. Es la
    base para comparar precios entre jugadores de la misma posición (ver
    scoring.price_percentile_base).

    Antes esto implicaba una llamada por rival (hasta 8-9 en esta liga), que
    fue justo lo que obligó a subir el timeout de gunicorn en producción —
    con el endpoint bulk (confirmado el 2026-08-02) es una sola llamada,
    igualmente cacheada unas horas."""
    def _fetch():
        try:
            team_names = real_team_names_by_id(client.get_real_teams())
        except FutmondoError:
            team_names = {}
        try:
            players = normalize_championship_players(client.get_championship_players(), team_names)
        except FutmondoError:
            return []
        return [p for p in players if p.get("owner_userteam_id") != client.team_id]

    key = f"known_players:{client.championship_id}"
    return cache.get_or_set(key, _fetch, ttl=cache.DEFAULT_TTL)


def normalize_pressroom(raw):
    """Actividad reciente del mercado de tu liga (de /1/locker/pressroom):
    quién ha puesto a quién en venta, a qué precio, y cuántas pujas lleva."""
    news = (raw or {}).get("news") or []
    items = []
    for n in news:
        items.append({
            "player_name": (n.get("_player") or {}).get("name"),
            "player_team": (n.get("_playerTeam") or {}).get("name"),
            "seller_name": (n.get("_seller") or {}).get("name"),
            "price": n.get("price"),
            "created": n.get("created"),
            "bids": len(n.get("bids") or []),
        })
    items.sort(key=lambda i: i.get("created") or "", reverse=True)
    return items


def get_price_history(client, player_id):
    """Historial de precio día a día de un jugador (campo `prices` de
    /1/player/summary) como lista de {date, price}, más antiguo primero.
    Se cachea porque no cambia más de una vez al día."""
    def _fetch():
        raw = client.get_player_summary(player_id)
        data = (raw or {}).get("data") or {}
        prices = (raw or {}).get("prices") or []
        history = [{"date": p.get("date"), "price": p.get("price")} for p in prices if p.get("price")]
        history.sort(key=lambda p: p.get("date") or "")
        return {"history": history, "current_price": data.get("value")}

    key = f"price_history:{player_id}"
    return cache.get_or_set(key, _fetch, ttl=cache.DEFAULT_TTL)
