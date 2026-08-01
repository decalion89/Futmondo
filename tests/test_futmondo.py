"""Tests de las funciones puras de parseo de la API de Futmondo
(services/futmondo.py). Sin red: usan respuestas de ejemplo con la forma
real confirmada el 2026-08-01.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import futmondo


def _match(home_name, away_name, home_id="home1", away_id="away1", sels=None):
    return {
        "homeTeam": {"id": home_id, "name": home_name},
        "awayTeam": {"id": away_id, "name": away_name},
        "date": "2026-08-15T17:30:00.000Z",
        "odds": {"sels": sels or []},
    }


def _sel(name, odds_values):
    return {"sn": name, "odds": [{"c": v} for v in odds_values]}


def test_normalize_roster_maps_real_fields():
    raw = [{
        "id": "abc123",
        "name": "Diego Llorente",
        "role": "defensa",
        "team": "Betis",
        "teamId": "team-betis",
        "value": 11786886,
        "status": "injured2",
        "points": 12,
        "rating": 4,
        "average": {"average": 5.2, "averageLastFive": 6.1},
    }]
    result = futmondo.normalize_roster(raw)
    assert len(result) == 1
    p = result[0]
    assert p["position"] == "DEF"
    assert p["team"] == "Betis"
    assert p["futmondo_team_id"] == "team-betis"
    assert p["futmondo_status"] == "lesionado"
    assert p["futmondo_average"] == 5.2
    assert p["futmondo_average_last_five"] == 6.1
    assert p["futmondo_points"] == 12


def test_normalize_roster_handles_string_team_not_dict():
    raw = [{"name": "X", "role": "portero", "team": "Valencia", "value": 100}]
    result = futmondo.normalize_roster(raw)
    assert result[0]["team"] == "Valencia"
    assert result[0]["position"] == "POR"


def test_normalize_roster_maps_real_clause_price_and_fitness_history():
    # Confirmado contra el código fuente de futmondo-utils (get-teams-players.js):
    # `player.clause.price` (precio EXACTO de clausulazo) y
    # `player.average.fitness` (forma partido a partido) vienen en el roster.
    raw = [{
        "name": "Jugador Rival", "role": "delantero", "team": "Sevilla", "value": 5_000_000,
        "clause": {"price": 8_500_000, "transferred": False},
        "average": {"average": 6.5, "averageLastFive": 7.0, "fitness": [5, 6, 8, 7, 6]},
    }]
    result = futmondo.normalize_roster(raw)
    assert result[0]["futmondo_clause_price"] == 8_500_000
    assert result[0]["futmondo_fitness_history"] == [5, 6, 8, 7, 6]


def test_normalize_roster_clause_price_none_for_own_players():
    # Tu propia plantilla no trae `clause` (no te pagas una cláusula a ti mismo).
    raw = [{"name": "Mi Jugador", "role": "delantero", "team": "Sevilla", "value": 5_000_000}]
    result = futmondo.normalize_roster(raw)
    assert result[0]["futmondo_clause_price"] is None
    assert result[0]["futmondo_fitness_history"] is None


def test_parse_match_odds_averages_bookmakers_and_normalizes():
    sels = [
        _sel("Alaves", [2.4, 2.6]),   # avg 2.5 -> implied 0.4
        _sel("Draw", [3.0, 3.0]),      # avg 3.0 -> implied 0.333
        _sel("Getafe", [3.0, 3.4]),   # avg 3.2 -> implied 0.3125
    ]
    match = _match("Alaves", "Getafe", sels=sels)
    probs = futmondo.parse_match_odds(match, "Alaves", "Getafe")
    assert probs is not None
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    # El favorito (cuota más baja) debe tener la probabilidad más alta
    assert probs["home"] > probs["away"] > probs["draw"] or probs["home"] > probs["draw"]


def test_parse_match_odds_none_without_odds():
    match = _match("Alaves", "Getafe", sels=[])
    assert futmondo.parse_match_odds(match, "Alaves", "Getafe") is None


def test_parse_match_odds_matches_inconsistent_team_naming():
    # Confirmado con datos reales: la ficha del partido dice "Racing" y
    # "Atlético de Madrid", pero la cuota usa "Racing Santander" y
    # "Atlético Madrid" — nombres distintos para el mismo equipo.
    sels = [_sel("Draw", [3.0]), _sel("Racing Santander", [3.5]), _sel("Villarreal", [2.1])]
    match = _match("Racing", "Villarreal", sels=sels)
    probs = futmondo.parse_match_odds(match, "Racing", "Villarreal")
    assert probs is not None
    assert probs["home"] > 0  # antes del fix esto daba 0 por no encontrar "Racing"

    sels2 = [_sel("Atlético Madrid", [1.8]), _sel("Draw", [3.6]), _sel("Málaga", [4.5])]
    match2 = _match("Atlético de Madrid", "Málaga", sels=sels2)
    probs2 = futmondo.parse_match_odds(match2, "Atlético de Madrid", "Málaga")
    assert probs2 is not None
    assert probs2["home"] > probs2["away"]


def test_next_match_by_team_indexes_home_and_away():
    sels = [_sel("Alaves", [2.0]), _sel("Draw", [3.0]), _sel("Getafe", [4.0])]
    match_list = {"matches": [_match("Alaves", "Getafe", home_id="t-alaves", away_id="t-getafe", sels=sels)]}
    index = futmondo.next_match_by_team(match_list)
    assert index["t-alaves"]["rival"] == "Getafe"
    assert index["t-alaves"]["is_home"] is True
    assert index["t-getafe"]["rival"] == "Alaves"
    assert index["t-getafe"]["is_home"] is False
    # El favorito (Alaves, cuota más baja) debe tener win_prob más alta que el rival
    assert index["t-alaves"]["win_prob"] > index["t-getafe"]["win_prob"]


def test_next_match_by_team_also_indexes_by_name():
    # El mercado no trae teamId, solo el nombre del equipo — debe poder
    # buscarse por nombre igual que por id.
    match_list = {"matches": [_match("Alaves", "Getafe", home_id="t-alaves", away_id="t-getafe")]}
    index = futmondo.next_match_by_team(match_list)
    assert index["Alaves"]["rival"] == "Getafe"
    assert index["Getafe"]["rival"] == "Alaves"


def test_next_match_by_team_empty_without_matches():
    assert futmondo.next_match_by_team({}) == {}
    assert futmondo.next_match_by_team(None) == {}


def test_normalize_league_teams_sorts_by_value_and_maps_configuration():
    raw = {
        "teams": [
            {"id": "a", "name": "Pobre", "teamValue": 50, "points": 0},
            {"id": "b", "name": "Rico", "teamValue": 999, "points": 0},
        ],
        "configuration": {
            "budget": 350000000,
            "playerRetention": 7,
            "mnmp": 0.25,
        },
    }
    teams, cfg = futmondo.normalize_league_teams(raw)
    assert [t["name"] for t in teams] == ["Rico", "Pobre"]
    assert cfg["budget"] == 350000000
    assert cfg["resale_lock_days"] == 7
    assert cfg["max_bid_over_funds_pct"] == 0.25


def test_normalize_league_teams_empty_without_data():
    teams, cfg = futmondo.normalize_league_teams({})
    assert teams == []
    assert cfg["budget"] is None


def test_normalize_pressroom_maps_and_sorts_by_recent_first():
    raw = {
        "news": [
            {
                "_player": {"name": "Sergio M."}, "_playerTeam": {"name": "Racing"},
                "_seller": {"name": "R.C.G."}, "price": 640000, "created": "2026-08-01T05:00:00.000Z",
                "bids": [],
            },
            {
                "_player": {"name": "Isaac"}, "_playerTeam": {"name": "Sevilla"},
                "_seller": {"name": "Snoopy"}, "price": 2000000, "created": "2026-08-02T05:00:00.000Z",
                "bids": [{"x": 1}, {"x": 2}],
            },
        ]
    }
    items = futmondo.normalize_pressroom(raw)
    assert len(items) == 2
    assert items[0]["player_name"] == "Isaac"  # más reciente primero
    assert items[0]["bids"] == 2
    assert items[1]["seller_name"] == "R.C.G."


def test_normalize_pressroom_empty_without_data():
    assert futmondo.normalize_pressroom({}) == []
    assert futmondo.normalize_pressroom(None) == []
