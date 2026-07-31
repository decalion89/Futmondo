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
        with open(path, "w") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)


def load_squad():
    _ensure_file(SQUAD_FILE, [])
    with open(SQUAD_FILE) as f:
        return json.load(f)


def save_squad(players):
    with open(SQUAD_FILE, "w") as f:
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
    with open(STATUS_CACHE_FILE) as f:
        return json.load(f)


def save_status_cache(cache):
    with open(STATUS_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
