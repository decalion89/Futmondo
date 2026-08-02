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


def test_map_status_covers_real_values_seen_2026_08_02():
    # Confirmado contra /5/league/championshipplayers real: "redcard" existe
    # como valor de status (tarjeta roja = sancionado el próximo partido) y
    # "ok" es un valor explícito, no solo el vacío visto hasta ahora.
    assert futmondo._map_status("redcard") == "sancionado"
    assert futmondo._map_status("doubt") == "duda"
    assert futmondo._map_status("ok") is None
    assert futmondo._map_status("") is None
    assert futmondo._map_status("injured") == "lesionado"


def test_real_team_names_by_id_maps_real_teams_list():
    raw = [
        {"id": "504e581e4d8bec9a670000c7", "name": "Barcelona", "logo": "barcelona.png"},
        {"id": "504e581e4d8bec9a670000c8", "name": "Atlético de Madrid", "logo": "x.png"},
    ]
    names = futmondo.real_team_names_by_id(raw)
    assert names["504e581e4d8bec9a670000c7"] == "Barcelona"
    assert len(names) == 2


def test_normalize_championship_players_maps_ownership_and_team_name():
    # Forma real confirmada el 2026-08-02 en /5/league/championshipplayers:
    # jugadores fichados llevan userteamId/userteam, los libres no.
    raw = {
        "players": [
            {
                "id": "p1", "name": "Pere Milla", "role": "centrocampista",
                "value": 4919579, "status": "", "points": 0,
                "average": {"average": 0, "averageLastFive": 0},
                "userteamId": "5a428257685d790214ee8a12", "userteam": "DRINK NEWTEAM",
                "teamId": "504e581e4d8bec9a670000d0",
            },
            {
                "id": "p2", "name": "Ivan Villar", "role": "portero",
                "value": 1000000, "status": "", "points": 0,
                "average": {"average": 0, "averageLastFive": 0},
                "teamId": "504e581e4d8bec9a670000d9",
            },
        ]
    }
    team_names = {"504e581e4d8bec9a670000d0": "Espanyol", "504e581e4d8bec9a670000d9": "Celta de Vigo"}
    result = futmondo.normalize_championship_players(raw, team_names)
    assert len(result) == 2
    owned, free = result
    assert owned["owner_team"] == "DRINK NEWTEAM"
    assert owned["owner_userteam_id"] == "5a428257685d790214ee8a12"
    assert owned["team"] == "Espanyol"
    assert free["owner_team"] is None
    assert free["owner_userteam_id"] is None
    assert free["team"] == "Celta de Vigo"


def _lastseasons_entry(mode, games, points, season="2025/2026"):
    return {
        "league": {"season": season},
        "points": [{"t": {"games": games, "p": points}, "mode": mode}],
    }


def test_normalize_lastseasons_prior_picks_presstats_mode():
    # Forma real confirmada el 2026-08-02 con Dimitrievski: 115.1 puntos en
    # 20 partidos en modo presstats — coincidió exacto con el dato real que
    # dio el usuario, ningún otro de los 9 modos cuadraba.
    raw = {
        "seasons": [
            {
                "league": {"season": "2025/2026"},
                "points": [
                    {"t": {"games": 20, "p": 101}, "mode": "press"},
                    {"t": {"games": 20, "p": 115.1}, "mode": "presstats"},
                    {"t": {"games": 20, "p": 88}, "mode": "picas"},
                ],
            },
        ]
    }
    prior = futmondo.normalize_lastseasons_prior(raw)
    assert prior == {"average": 5.75, "games": 20, "season": "2025/2026"}


def test_normalize_lastseasons_prior_none_without_seasons():
    assert futmondo.normalize_lastseasons_prior({}) is None
    assert futmondo.normalize_lastseasons_prior({"seasons": []}) is None


def test_normalize_lastseasons_prior_none_when_zero_games():
    raw = {"seasons": [_lastseasons_entry("presstats", games=0, points=0)]}
    assert futmondo.normalize_lastseasons_prior(raw) is None


def test_normalize_lastseasons_prior_none_when_mode_missing():
    raw = {"seasons": [_lastseasons_entry("press", games=20, points=101)]}
    assert futmondo.normalize_lastseasons_prior(raw) is None


def test_normalize_lastseasons_prior_uses_most_recent_season_only():
    raw = {
        "seasons": [
            _lastseasons_entry("presstats", games=20, points=115.1, season="2025/2026"),
            _lastseasons_entry("presstats", games=37, points=175.9, season="2023/2024"),
        ]
    }
    prior = futmondo.normalize_lastseasons_prior(raw)
    assert prior["season"] == "2025/2026"
    assert prior["games"] == 20


def test_normalize_championship_players_falls_back_to_team_id_without_name_map():
    raw = {"players": [{"id": "p1", "name": "X", "role": "delantero", "value": 1_000_000, "teamId": "unknown-id"}]}
    result = futmondo.normalize_championship_players(raw)
    assert result[0]["team"] == "unknown-id"


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
    # Forma real confirmada el 2026-08-02: el comprador viene en `_buyer`
    # (no `_seller`, que no existe en la respuesta real), y `bids` trae el
    # detalle completo (quién pujó y cuánto), no solo un contador.
    raw = {
        "news": [
            {
                "_player": {"name": "Sergio M."}, "_playerTeam": {"name": "Racing"},
                "_buyer": {"name": "R.C.G."}, "price": 640000, "created": "2026-08-01T05:00:00.000Z",
                "bids": [],
            },
            {
                "_player": {"name": "Isaac"}, "_playerTeam": {"name": "Sevilla"},
                "_buyer": {"name": "Snoopy"}, "price": 2000000, "created": "2026-08-02T05:00:00.000Z",
                "bids": [
                    {"u": {"name": "DRINK NEWTEAM"}, "bid": 1800000},
                    {"u": {"name": "R.C.G."}, "bid": 1500000},
                ],
            },
        ]
    }
    items = futmondo.normalize_pressroom(raw)
    assert len(items) == 2
    assert items[0]["player_name"] == "Isaac"  # más reciente primero
    assert items[0]["bid_count"] == 2
    assert items[0]["bids"][0] == {"bidder": "DRINK NEWTEAM", "amount": 1800000}
    assert items[1]["buyer_name"] == "R.C.G."


def test_normalize_pressroom_empty_without_data():
    assert futmondo.normalize_pressroom({}) == []
    assert futmondo.normalize_pressroom(None) == []
