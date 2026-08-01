"""Almacenamiento simple en JSON de tu plantilla (uso personal, sin base de datos)."""
import json
import os
import uuid

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SQUAD_FILE = os.path.join(DATA_DIR, "squad.json")
STATUS_CACHE_FILE = os.path.join(DATA_DIR, "status_cache.json")

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
