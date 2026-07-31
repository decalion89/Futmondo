"""Tests de la lógica que decide capitán/valor/fichajes (services/scoring.py).

Son funciones puras (sin red), así que se pueden verificar sin API keys.
Esto es lo que de verdad importa: si esta lógica está mal, cada
recomendación de la app está mal.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import scoring


def test_parse_price_numeric():
    assert scoring.parse_price(25_000_000) == 25_000_000.0
    assert scoring.parse_price(0) == 0.0  # parseo fiel; value_for_money decide qué hacer con un 0
    assert scoring.parse_price(None) is None


def test_parse_price_spanish_format_string():
    assert scoring.parse_price("5.652.156€") == 5652156.0
    assert scoring.parse_price("1.234.567,89") == 1234567.89


def test_value_for_money_basic():
    # 8 de puntuación con un jugador de 20 millones -> 0.4 puntos por millón
    assert scoring.value_for_money(8, 20_000_000) == 0.4


def test_value_for_money_missing_data_is_none():
    assert scoring.value_for_money(None, 20_000_000) is None
    assert scoring.value_for_money(8, None) is None
    assert scoring.value_for_money(8, 0) is None


def test_fixture_swing_empty_without_data():
    swing = scoring.fixture_swing([], team_id=1, standings={})
    assert swing["avg_goals_against_rivals"] is None
    assert swing["avg_goals_for_rivals"] is None
    assert swing["fixtures"] == []


def _fake_fixture(home_id, away_id, home_name="Local", away_name="Visitante"):
    return {
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "fixture": {"date": "2026-08-20T20:00:00+00:00"},
    }


def test_fixture_swing_computes_rival_averages():
    # Nuestro equipo (id=1) juega fuera contra el equipo 2, que encaja
    # muchos goles (fácil para nuestros delanteros) y marca pocos (bueno
    # también para nuestros defensas).
    standings = {
        "2": {"all": {"played": 10, "goals": {"for": 5, "against": 20}}},
    }
    fixtures = [_fake_fixture(home_id=2, away_id=1)]
    swing = scoring.fixture_swing(fixtures, team_id=1, standings=standings)
    assert swing["avg_goals_against_rivals"] == 2.0  # 20/10 -> fácil marcarles
    assert swing["avg_goals_for_rivals"] == 0.5       # 5/10 -> rival poco peligroso
    assert swing["fixtures"][0]["rival"] == "Local"
    assert swing["fixtures"][0]["is_home"] is False


def test_player_score_rewards_easy_fixture_for_forward():
    easy = {"avg_goals_against_rivals": 2.5, "avg_goals_for_rivals": None}
    hard = {"avg_goals_against_rivals": 0.5, "avg_goals_for_rivals": None}
    score_easy = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=easy)
    score_hard = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=hard)
    assert score_easy > score_hard


def test_player_score_rewards_weak_attack_rival_for_defender():
    weak_rival_attack = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": 0.3}
    strong_rival_attack = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": 2.5}
    score_easy = scoring.player_score("DEF", rating=7.0, starter_rate=1.0, swing=weak_rival_attack)
    score_hard = scoring.player_score("DEF", rating=7.0, starter_rate=1.0, swing=strong_rival_attack)
    assert score_easy > score_hard


def test_player_score_penalizes_bench_risk():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    starter = scoring.player_score("CEN", rating=7.0, starter_rate=1.0, swing=swing)
    benchwarmer = scoring.player_score("CEN", rating=7.0, starter_rate=0.0, swing=swing)
    assert starter > benchwarmer


def test_player_score_defaults_when_no_rating():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    score = scoring.player_score("DEL", rating=None, starter_rate=None, swing=swing)
    assert score > 0


def test_squad_value_benchmark_median():
    assert scoring.squad_value_benchmark([0.2, 0.4, 0.6]) == 0.4
    assert scoring.squad_value_benchmark([0.2, 0.6]) == 0.4


def test_squad_value_benchmark_ignores_missing_data():
    assert scoring.squad_value_benchmark([None, None, 0.5]) == 0.5


def test_squad_value_benchmark_falls_back_when_empty():
    assert scoring.squad_value_benchmark([]) == scoring.DEFAULT_VALUE_BENCHMARK
    assert scoring.squad_value_benchmark([None, None]) == scoring.DEFAULT_VALUE_BENCHMARK


def test_max_recommended_bid_matches_value_for_money():
    # Si pagas justo el máximo recomendado, el valor resultante debe
    # coincidir con la referencia usada (ida y vuelta de la misma fórmula).
    score = 8.0
    benchmark = 0.4
    max_bid = scoring.max_recommended_bid(score, benchmark)
    assert max_bid == 20_000_000
    assert scoring.value_for_money(score, max_bid) == benchmark


def test_max_recommended_bid_none_without_data():
    assert scoring.max_recommended_bid(None, 0.4) is None
    assert scoring.max_recommended_bid(8.0, None) is None
    assert scoring.max_recommended_bid(8.0, 0) is None


def _fixture_at(days_ago, competition="LaLiga"):
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {"fixture": {"date": date}, "league": {"name": competition}}


def test_fixture_congestion_counts_matches_in_window():
    fixtures = [
        _fixture_at(1, "LaLiga"),
        _fixture_at(4, "Champions League"),
        _fixture_at(8, "Copa del Rey"),
        _fixture_at(15, "LaLiga"),  # fuera de la ventana de 10 días
    ]
    result = scoring.fixture_congestion(fixtures)
    assert result["count"] == 3
    assert result["competitions"] == ["Champions League", "Copa del Rey", "LaLiga"]


def test_fixture_congestion_empty_without_data():
    result = scoring.fixture_congestion([])
    assert result["count"] == 0
    assert result["competitions"] == []


def test_fixture_congestion_ignores_malformed_entries():
    result = scoring.fixture_congestion([{"fixture": {}}, {}])
    assert result["count"] == 0


def test_fatigue_factor_penalizes_congested_calendar():
    assert scoring.fatigue_factor(None) == 1.0
    assert scoring.fatigue_factor(1) == 1.0
    assert scoring.fatigue_factor(2) < 1.0
    assert scoring.fatigue_factor(3) < scoring.fatigue_factor(2)
    assert scoring.fatigue_factor(4) < scoring.fatigue_factor(3)


def test_player_score_penalizes_fixture_congestion():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    fresh = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing, congestion_count=1)
    congested = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing, congestion_count=4)
    assert congested < fresh


def _standings_row(rank, played):
    return {"rank": rank, "all": {"played": played}}


def test_team_motivation_high_stakes_positions_unaffected():
    # Pelea título/Europa (rank 3) o descenso (rank 18): motivación siempre alta
    assert scoring.team_motivation_factor(_standings_row(3, 30)) == 1.0
    assert scoring.team_motivation_factor(_standings_row(18, 30)) == 1.0


def test_team_motivation_mid_table_early_season_unaffected():
    # Zona media pero aún queda mucha temporada: podría cambiar todo
    assert scoring.team_motivation_factor(_standings_row(10, 15)) == 1.0


def test_team_motivation_mid_table_late_season_penalized():
    # Zona media, temporada muy avanzada: partido de trámite
    assert scoring.team_motivation_factor(_standings_row(10, 32)) < 1.0


def test_team_motivation_defaults_without_data():
    assert scoring.team_motivation_factor(None) == 1.0
    assert scoring.team_motivation_factor({}) == 1.0


def test_card_suspension_risk():
    assert scoring.card_suspension_risk(4) is True   # a una amarilla de la 5ª
    assert scoring.card_suspension_risk(9) is True   # a una del segundo ciclo
    assert scoring.card_suspension_risk(3) is False
    assert scoring.card_suspension_risk(0) is False
    assert scoring.card_suspension_risk(None) is False


def test_is_penalty_taker():
    assert scoring.is_penalty_taker(2, 0) is True
    assert scoring.is_penalty_taker(0, 1) is True
    assert scoring.is_penalty_taker(0, 0) is False
    assert scoring.is_penalty_taker(None, None) is False


def test_player_score_rewards_penalty_taker_and_motivation():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    baseline = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing)
    with_penalty = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing, penalty_taker=True)
    low_motivation = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing, motivation_factor=0.93)
    assert with_penalty > baseline
    assert low_motivation < baseline
