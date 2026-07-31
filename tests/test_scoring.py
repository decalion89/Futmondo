"""Tests de la lógica que decide capitán/valor/fichajes (services/scoring.py).

Son funciones puras (sin red), así que se pueden verificar sin API keys.
Esto es lo que de verdad importa: si esta lógica está mal, cada
recomendación de la app está mal.
"""
import sys
import os

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
