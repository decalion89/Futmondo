"""Almacenamiento simple en JSON de tu plantilla (uso personal, sin base de datos)."""
import json
import os
import uuid

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SQUAD_FILE = os.path.join(DATA_DIR, "squad.json")
STATUS_CACHE_FILE = os.path.join(DATA_DIR, "status_cache.json")
SCORE_HISTORY_FILE = os.path.join(DATA_DIR, "score_history.json")
SCORE_HISTORY_MAX_ENTRIES = 60  # ~2 temporadas de jornadas de margen, para no crecer indefinidamente

POSITIONS = ["POR", "DEF", "CEN", "DEL"]


def _ensure_file(path, default):
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)


def load_squad():
    _ensure_file(SQUAD_FILE, [])
    with open(SQUAD_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_squad(players):
    with open(SQUAD_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)


def add_player(name, position, team, price, api_football_id=None):
    players = load_squad()
    players.append({
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "position": position,
        "team": team,
        "price": price,
        "api_football_id": api_football_id,
        "api_football_team_id": None,
    })
    save_squad(players)


def import_roster(normalized_players):
    """Reemplaza tu plantilla con la importada desde Futmondo, conservando
    el mapeo a API-Football ya resuelto para jugadores que sigan en el equipo
    (para no gastar peticiones re-buscándolos)."""
    existing_by_key = {(p["name"], p["team"]): p for p in load_squad()}
    players = []
    for p in normalized_players:
        key = (p["name"], p["team"])
        prev = existing_by_key.get(key, {})
        players.append({
            "id": prev.get("id") or str(uuid.uuid4())[:8],
            "name": p["name"],
            "position": p["position"],
            "team": p["team"],
            "price": p.get("price"),
            "futmondo_player_id": p.get("futmondo_player_id"),
            "futmondo_team_id": p.get("futmondo_team_id"),
            "futmondo_status": p.get("futmondo_status"),
            "futmondo_average": p.get("futmondo_average"),
            "futmondo_average_last_five": p.get("futmondo_average_last_five"),
            "futmondo_points": p.get("futmondo_points"),
            "futmondo_rating": p.get("futmondo_rating"),
            "futmondo_price_change": p.get("futmondo_price_change"),
            "futmondo_buy_price": p.get("futmondo_buy_price"),
            "photo_url": p.get("photo_url") or prev.get("photo_url"),
            "api_football_id": prev.get("api_football_id"),
            "api_football_team_id": prev.get("api_football_team_id"),
        })
    save_squad(players)
    return players


def remove_player(player_id):
    players = [p for p in load_squad() if p["id"] != player_id]
    save_squad(players)


def update_player(player_id, **fields):
    players = load_squad()
    for p in players:
        if p["id"] == player_id:
            p.update(fields)
    save_squad(players)


def load_status_cache():
    _ensure_file(STATUS_CACHE_FILE, {})
    with open(STATUS_CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_status_cache(cache):
    with open(STATUS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def load_score_history():
    _ensure_file(SCORE_HISTORY_FILE, {})
    with open(SCORE_HISTORY_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_score_history(history):
    with open(SCORE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def add_score_entry(history, player_id, date, score):
    """Añade (mutando en memoria, sin tocar disco) un punto {date, score}
    para un jugador — no hay forma honesta de estimar consistencia/varianza
    real (para elegir alineación a "suelo" o "techo") sin ir acumulando su
    puntuación jornada a jornada; esto empieza a construir ese histórico
    desde ya.

    Futmondo solo actualiza `average`/`points` cuando se juega un partido
    real, así que sincronizar varios días seguidos ENTRE jornadas daría la
    misma puntuación una y otra vez — si guardásemos una entrada por cada
    sync, el histórico se llenaría de días idénticos que no representan
    partidos distintos, y la varianza calculada saldría artificialmente
    baja justo cuando empezáramos a fiarnos de ella. Por eso: mismo día que
    la última entrada -> se sustituye (corrección del mismo día); día
    distinto pero MISMA puntuación que la última -> no se añade nada (no ha
    pasado nada nuevo desde la última vez); puntuación distinta -> se
    añade, sea cual sea la fecha."""
    entries = history.setdefault(player_id, [])
    if entries and entries[-1]["date"] == date:
        entries[-1]["score"] = score
    elif entries and entries[-1]["score"] == score:
        pass  # mismo rendimiento que la última entrada registrada, no es un dato nuevo
    else:
        entries.append({"date": date, "score": score})
        entries.sort(key=lambda e: e["date"])
        del entries[:-SCORE_HISTORY_MAX_ENTRIES]
    return entries
