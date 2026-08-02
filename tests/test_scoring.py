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


def test_fixture_swing_uses_home_away_split_when_enough_games():
    # El rival (id=2) es sólido en general pero mucho más flojo como
    # visitante (le meten 3 goles/partido fuera vs 0.5 en casa). Nuestro
    # equipo (id=1) juega en casa, así que el rival visita: debe usarse su
    # desglose "away", no la media combinada.
    standings = {
        "2": {
            "all": {"played": 10, "goals": {"for": 15, "against": 10}},
            "home": {"played": 5, "goals": {"for": 10, "against": 2.5}},
            "away": {"played": 5, "goals": {"for": 5, "against": 15}},
        },
    }
    fixtures = [_fake_fixture(home_id=1, away_id=2)]
    swing = scoring.fixture_swing(fixtures, team_id=1, standings=standings)
    assert swing["avg_goals_against_rivals"] == 3.0  # 15/5, no 10/10=1.0 de la media combinada


def test_fixture_swing_falls_back_to_combined_with_few_split_games():
    # Solo 1 partido como visitante todavía: no nos fiamos del desglose,
    # usamos la media combinada de toda la temporada.
    standings = {
        "2": {
            "all": {"played": 10, "goals": {"for": 15, "against": 10}},
            "away": {"played": 1, "goals": {"for": 0, "against": 5}},
        },
    }
    fixtures = [_fake_fixture(home_id=1, away_id=2)]
    swing = scoring.fixture_swing(fixtures, team_id=1, standings=standings)
    assert swing["avg_goals_against_rivals"] == 1.0  # 10/10 de "all", no 5/1 de "away"


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


def test_player_score_titular_probability_overrides_starter_rate():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    # starter_rate histórico dice "siempre titular", pero el once probable
    # real de esta semana dice que casi seguro no juega -> debe mandar el
    # dato real y directo, no la media histórica.
    high_historical_low_real = scoring.player_score(
        "CEN", rating=7.0, starter_rate=1.0, swing=swing, titular_probability=5,
    )
    low_historical_high_real = scoring.player_score(
        "CEN", rating=7.0, starter_rate=0.0, swing=swing, titular_probability=95,
    )
    assert low_historical_high_real > high_historical_low_real


def test_player_score_titular_probability_scales_reliability_monotonically():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    low = scoring.player_score("DEL", rating=7.0, starter_rate=None, swing=swing, titular_probability=10)
    mid = scoring.player_score("DEL", rating=7.0, starter_rate=None, swing=swing, titular_probability=50)
    high = scoring.player_score("DEL", rating=7.0, starter_rate=None, swing=swing, titular_probability=100)
    assert low < mid < high


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


def test_team_motivation_trusts_provider_description_over_rank():
    # El campo `description` (si existe) manda sobre el rango de posición:
    # aquí el rank 10 caería en "zona media" por rango, pero la API dice
    # que sigue peleando Europa, así que motivación alta.
    row_with_europe_tag = {"rank": 10, "all": {"played": 32}, "description": "Europa League"}
    assert scoring.team_motivation_factor(row_with_europe_tag) == 1.0

    # Y al revés: description vacío/null confirma que no se juega nada,
    # aunque el rank (7) hubiera parecido zona alta por rango.
    row_confirmed_nothing = {"rank": 7, "all": {"played": 32}, "description": None}
    assert scoring.team_motivation_factor(row_confirmed_nothing) < 1.0


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


def test_attacking_output_bonus_weighs_defenders_more_than_forwards():
    # Mismo gol/asistencia por partido, pero vale más en la puntuación de un
    # defensa o portero que de un delantero (convención Fantasy: 6/5/4 pts).
    bonus_def = scoring.attacking_output_bonus("DEF", goals=5, assists=2, appearences=20)
    bonus_del = scoring.attacking_output_bonus("DEL", goals=5, assists=2, appearences=20)
    assert bonus_def > bonus_del


def test_attacking_output_bonus_zero_without_appearances():
    assert scoring.attacking_output_bonus("DEL", goals=5, assists=2, appearences=0) == 0.0
    assert scoring.attacking_output_bonus("DEL", goals=5, assists=2, appearences=None) == 0.0


def test_player_score_rewards_goal_contribution():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    prolific_defender = scoring.player_score(
        "DEF", rating=6.5, starter_rate=1.0, swing=swing, goals=6, assists=3, appearences=25,
    )
    quiet_defender = scoring.player_score(
        "DEF", rating=6.5, starter_rate=1.0, swing=swing, goals=0, assists=0, appearences=25,
    )
    assert prolific_defender > quiet_defender


def test_budget_concentration_computes_share_of_top_players():
    # 2 jugadores de 10M sobre un total de 20M -> 100% concentrado en ellos
    assert scoring.budget_concentration([10, 10], top_n=2) == 1.0
    # 15M en los 2 más caros sobre 20M totales -> 75%
    assert scoring.budget_concentration([10, 5, 3, 2], top_n=2) == 0.75


def test_budget_concentration_none_without_prices():
    assert scoring.budget_concentration([]) is None
    assert scoring.budget_concentration([None, None]) is None


def test_team_form_factor_rewards_winning_streak():
    hot = scoring.team_form_factor({"form": "WWWWW"})
    cold = scoring.team_form_factor({"form": "LLLLL"})
    mixed = scoring.team_form_factor({"form": "WDLWD"})
    assert hot > mixed > cold
    assert hot == 1.07
    assert cold == 0.93


def test_team_form_factor_defaults_without_data():
    assert scoring.team_form_factor(None) == 1.0
    assert scoring.team_form_factor({}) == 1.0
    assert scoring.team_form_factor({"form": ""}) == 1.0


def test_fixture_swing_includes_next_rival_form():
    standings = {
        "2": {"all": {"played": 10, "goals": {"for": 10, "against": 10}}, "form": "WWWWW"},
    }
    fixtures = [_fixture_at_days(0, home_id=2, away_id=1)]
    swing = scoring.fixture_swing(fixtures, team_id=1, standings=standings)
    assert swing["next_rival_form_factor"] == 1.07


def _fixture_at_days(days_from_now, home_id, away_id):
    return {
        "teams": {"home": {"id": home_id, "name": "Local"}, "away": {"id": away_id, "name": "Visitante"}},
        "fixture": {"date": "2026-08-20T20:00:00+00:00"},
    }


def test_player_score_penalizes_rival_in_good_form():
    swing_hot_rival = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None, "next_rival_form_factor": 1.07}
    swing_cold_rival = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None, "next_rival_form_factor": 0.93}
    vs_hot = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing_hot_rival)
    vs_cold = scoring.player_score("DEL", rating=7.0, starter_rate=1.0, swing=swing_cold_rival)
    assert vs_cold > vs_hot


def test_fixture_factor_from_win_prob_rewards_favourites():
    favourite = scoring.fixture_factor_from_win_prob(0.8)
    underdog = scoring.fixture_factor_from_win_prob(0.2)
    assert favourite > underdog
    assert scoring.fixture_factor_from_win_prob(None) == 1.0


def test_player_score_prefers_futmondo_form_over_api_rating():
    swing = {"avg_goals_against_rivals": None, "avg_goals_for_rivals": None}
    # Si hay forma real de Futmondo, manda sobre el rating de API-Football.
    score_futmondo = scoring.player_score(
        "DEL", rating=6.0, starter_rate=1.0, swing=swing, futmondo_form=9.0,
    )
    score_api_only = scoring.player_score(
        "DEL", rating=6.0, starter_rate=1.0, swing=swing,
    )
    assert score_futmondo > score_api_only


def test_player_score_uses_futmondo_win_prob_over_swing():
    swing_would_say_hard = {"avg_goals_against_rivals": 0.3, "avg_goals_for_rivals": None}
    # Aunque el swing de API-Football diga que es difícil, si tenemos la
    # probabilidad real de las cuotas de Futmondo, esa manda.
    easy_by_odds = scoring.player_score(
        "DEL", rating=7.0, starter_rate=1.0, swing=swing_would_say_hard, futmondo_win_prob=0.9,
    )
    hard_by_swing_only = scoring.player_score(
        "DEL", rating=7.0, starter_rate=1.0, swing=swing_would_say_hard,
    )
    assert easy_by_odds > hard_by_swing_only


def test_real_budget_max_bid_matches_league_configuration():
    # Confirmado con datos reales: budget=350M, mnmp=0.25 ("Fondos +25%
    # valor del equipo"). Con un equipo de 100M de valor: fondos = 250M,
    # tope real = 250M + 0.25*100M = 275M.
    assert scoring.real_budget_max_bid(budget=350_000_000, team_value=100_000_000, max_bid_over_funds_pct=0.25) == 275_000_000


def test_real_budget_max_bid_none_without_data():
    assert scoring.real_budget_max_bid(None, 100, 0.25) is None
    assert scoring.real_budget_max_bid(350, None, 0.25) is None
    assert scoring.real_budget_max_bid(350, 100, None) is None


def test_build_position_price_index_groups_by_position():
    players = [
        {"position": "DEL", "price": 10_000_000},
        {"position": "DEL", "price": 5_000_000},
        {"position": "DEF", "price": 2_000_000},
        {"position": "DEL", "price": None},  # sin precio, se ignora
    ]
    index = scoring.build_position_price_index(players)
    assert sorted(index["DEL"]) == [5_000_000, 10_000_000]
    assert index["DEF"] == [2_000_000]


def test_price_percentile_base_ranks_cheap_and_expensive_players():
    position_prices = {"DEL": [1_000_000, 5_000_000, 10_000_000, 20_000_000]}
    cheapest = scoring.price_percentile_base(1_000_000, "DEL", position_prices)
    priciest = scoring.price_percentile_base(20_000_000, "DEL", position_prices)
    assert cheapest < priciest
    assert scoring.PRESEASON_BASE_MIN <= cheapest <= scoring.PRESEASON_BASE_MAX
    assert scoring.PRESEASON_BASE_MIN <= priciest <= scoring.PRESEASON_BASE_MAX
    assert priciest == scoring.PRESEASON_BASE_MAX  # el más caro de todos -> percentil 100%


def test_price_percentile_base_none_without_data():
    assert scoring.price_percentile_base(1_000_000, "DEL", {}) is None
    assert scoring.price_percentile_base(None, "DEL", {"DEL": [1, 2, 3]}) is None


def test_is_low_confidence_fringe_flags_floor_price_without_real_data():
    assert scoring.is_low_confidence_fringe(1_000_000, has_real_data=False) is True
    assert scoring.is_low_confidence_fringe(1_000_000, has_real_data=True) is False  # sí ha jugado, precio bajo no importa
    assert scoring.is_low_confidence_fringe(6_500_000, has_real_data=False) is False  # no está en el precio mínimo
    assert scoring.is_low_confidence_fringe(None, has_real_data=False) is False


def test_is_low_confidence_fringe_prioritizes_real_titular_probability():
    # Con dato real de once probable, manda sobre el proxy de precio, en
    # cualquier dirección: precio caro pero baja probabilidad real -> fringe;
    # precio mínimo pero alta probabilidad real -> NO es fringe.
    assert scoring.is_low_confidence_fringe(20_000_000, has_real_data=True, titular_probability=10) is True
    assert scoring.is_low_confidence_fringe(1_000_000, has_real_data=False, titular_probability=90) is False
    assert scoring.is_low_confidence_fringe(1_000_000, has_real_data=False, titular_probability=19) is True
    assert scoring.is_low_confidence_fringe(1_000_000, has_real_data=False, titular_probability=20) is False


def test_is_low_confidence_fringe_flags_not_in_contention_regardless_of_price():
    # No estar en la lista de futbolfantasy.com (equipo conocido, jugador
    # ausente) es señal real más fuerte que cualquier proxy de precio —
    # manda incluso con precio alto.
    assert scoring.is_low_confidence_fringe(20_000_000, has_real_data=True, not_in_contention=True) is True
    assert scoring.is_low_confidence_fringe(6_500_000, has_real_data=False, not_in_contention=True) is True


def test_detect_lineup_disagreement_none_without_lineup_info():
    assert scoring.detect_lineup_disagreement("ok", None) is None
    assert scoring.detect_lineup_disagreement("ok", {}) is None


def test_detect_lineup_disagreement_flags_not_in_contention_despite_futmondo_ok():
    lineup_info = {"probability": None, "not_in_contention": True}
    warning = scoring.detect_lineup_disagreement("ok", lineup_info)
    assert warning is not None
    assert "ni siquiera lo incluye" in warning.lower()


def test_detect_lineup_disagreement_flags_futmondo_lagging_behind_injury_news():
    lineup_info = {"probability": 0, "injured": True, "suspended": False, "unavailable": False}
    warning = scoring.detect_lineup_disagreement("ok", lineup_info)
    assert warning is not None
    assert "no disponible" in warning.lower()


def test_detect_lineup_disagreement_flags_low_probability_despite_futmondo_ok():
    lineup_info = {"probability": 10, "injured": False, "suspended": False, "unavailable": False}
    warning = scoring.detect_lineup_disagreement("ok", lineup_info)
    assert warning is not None
    assert "10%" in warning


def test_detect_lineup_disagreement_flags_possible_earlier_recovery():
    lineup_info = {"probability": 80, "injured": False, "suspended": False, "unavailable": False}
    warning = scoring.detect_lineup_disagreement("lesionado", lineup_info)
    assert warning is not None
    assert "recuperándose" in warning.lower()


def test_detect_lineup_disagreement_none_when_sources_agree():
    # Ambas fuentes de acuerdo: disponible y alta probabilidad.
    lineup_info = {"probability": 90, "injured": False, "suspended": False, "unavailable": False}
    assert scoring.detect_lineup_disagreement("ok", lineup_info) is None

    # Ambas fuentes de acuerdo: no disponible y con problema real marcado.
    lineup_info2 = {"probability": 0, "injured": True, "suspended": False, "unavailable": False}
    assert scoring.detect_lineup_disagreement("lesionado", lineup_info2) is None


def test_implied_games_played_derives_from_points_and_average():
    assert scoring.implied_games_played(30, 6.0) == 5
    assert scoring.implied_games_played(6.5, 6.5) == 1


def test_implied_games_played_none_without_data():
    assert scoring.implied_games_played(None, 6.0) is None
    assert scoring.implied_games_played(30, None) is None
    assert scoring.implied_games_played(0, 6.0) is None


def test_shrink_form_estimate_pulls_low_sample_toward_prior():
    # 1 solo partido contabilizado: el dato real pesa poco frente al prior.
    shrunk_low = scoring.shrink_form_estimate(observed=9.0, games_played=1, prior=6.0)
    assert 6.0 < shrunk_low < 9.0
    assert abs(shrunk_low - 6.75) < 0.01  # (1*9 + 3*6) / (1+3)

    # Con muchos partidos ya jugados, el dato real casi no se toca.
    shrunk_high = scoring.shrink_form_estimate(observed=9.0, games_played=20, prior=6.0)
    assert shrunk_high > shrunk_low
    assert abs(shrunk_high - 8.308) < 0.01  # (10*9 + 3*6) / (10+3), tope en 10 partidos


def test_shrink_form_estimate_returns_observed_without_games_or_prior():
    assert scoring.shrink_form_estimate(7.5, None, 6.0) == 7.5
    assert scoring.shrink_form_estimate(7.5, 5, None) == 7.5
    assert scoring.shrink_form_estimate(7.5, 0, 6.0) == 7.5


def test_price_trend_classifies_up_down_flat():
    assert scoring.price_trend(10_000_000, 500_000) == "up"    # +5%
    assert scoring.price_trend(10_000_000, -500_000) == "down"  # -5%
    assert scoring.price_trend(10_000_000, 50_000) == "flat"    # +0.5%, ruido


def test_price_trend_none_without_data():
    assert scoring.price_trend(None, 500_000) is None
    assert scoring.price_trend(10_000_000, None) is None


def _hist(prices):
    return [{"date": f"2026-08-{i+1:02d}", "price": p} for i, p in enumerate(prices)]


def test_price_momentum_flag_detects_sustained_rise():
    # 3 días seguidos subiendo, +25% acumulado en la racha.
    flag = scoring.price_momentum_flag(_hist([1_000_000, 1_050_000, 1_150_000, 1_250_000]))
    assert flag is not None
    assert flag["streak_days"] == 3
    assert flag["cumulative_pct"] > scoring.BUBBLE_CUMULATIVE_THRESHOLD


def test_price_momentum_flag_none_when_streak_too_short():
    # Solo 2 días seguidos subiendo, por debajo del mínimo de 3.
    flag = scoring.price_momentum_flag(_hist([1_000_000, 1_100_000, 1_250_000]))
    assert flag is None


def test_price_momentum_flag_none_when_rise_too_small():
    # 4 días seguidos pero subida acumulada mínima (ruido, no racha real).
    flag = scoring.price_momentum_flag(_hist([1_000_000, 1_001_000, 1_002_000, 1_003_000]))
    assert flag is None


def test_price_momentum_flag_none_without_enough_history():
    assert scoring.price_momentum_flag(_hist([1_000_000, 1_100_000])) is None
    assert scoring.price_momentum_flag([]) is None
    assert scoring.price_momentum_flag(None) is None


def test_price_momentum_flag_none_when_last_day_drops():
    # La racha se mide desde el último día hacia atrás: si el último día ya
    # bajó, no hay racha activa ahora mismo aunque la hubiera antes.
    flag = scoring.price_momentum_flag(_hist([1_000_000, 1_100_000, 1_250_000, 1_200_000]))
    assert flag is None


def test_score_consistency_none_with_too_little_history():
    assert scoring.score_consistency([{"date": "d1", "score": 6.0}, {"date": "d2", "score": 7.0}]) is None
    assert scoring.score_consistency([]) is None
    assert scoring.score_consistency(None) is None


def test_score_consistency_low_for_steady_scores():
    steady = [{"date": f"d{i}", "score": s} for i, s in enumerate([6.0, 6.2, 5.9, 6.1])]
    boom_bust = [{"date": f"d{i}", "score": s} for i, s in enumerate([1.0, 10.0, 1.5, 9.5])]
    steady_stdev = scoring.score_consistency(steady)
    boom_bust_stdev = scoring.score_consistency(boom_bust)
    assert steady_stdev < boom_bust_stdev


def test_purchase_profit_computes_gain_and_loss():
    assert scoring.purchase_profit(current_value=12_000_000, buy_price=10_000_000) == 2_000_000
    assert scoring.purchase_profit(current_value=8_000_000, buy_price=10_000_000) == -2_000_000


def test_purchase_profit_none_when_not_actively_bought():
    # buyPrice=0 en Futmondo significa "no comprado por mercado" (reparto inicial)
    assert scoring.purchase_profit(current_value=12_000_000, buy_price=0) is None
    assert scoring.purchase_profit(current_value=12_000_000, buy_price=None) is None


def test_recommended_sale_price_defaults_to_current_value_without_momentum():
    assert scoring.recommended_sale_price(10_000_000) == 10_000_000


def test_recommended_sale_price_none_without_price():
    assert scoring.recommended_sale_price(None) is None


def test_recommended_sale_price_asks_more_when_rising():
    price = scoring.recommended_sale_price(10_000_000, momentum={"direction": "up", "cumulative_pct": 0.2})
    assert price == 10_500_000  # tope del 5%, no el 20% completo de la racha


def test_recommended_sale_price_asks_less_when_falling():
    price = scoring.recommended_sale_price(10_000_000, momentum={"direction": "down", "cumulative_pct": 0.2})
    assert price == 9_500_000


def test_recommended_sale_price_urgent_applies_extra_discount():
    price = scoring.recommended_sale_price(10_000_000, momentum=None, urgent=True)
    assert price == 9_700_000  # 3% de descuento por urgencia


def test_recommended_sale_price_combines_urgency_and_falling_momentum():
    price = scoring.recommended_sale_price(
        10_000_000, momentum={"direction": "down", "cumulative_pct": 0.1}, urgent=True,
    )
    assert price == 9_200_000  # -5% (racha) - 3% (urgencia)


def _p(id_, position, score):
    return {"id": id_, "name": id_, "position": position, "score": score}


def test_best_lineup_picks_formation_with_highest_total():
    # Con solo 2 delanteros disponibles, una formación con 3 DEL queda
    # descartada aunque en teoría diera más puntos.
    players = (
        [_p(f"por{i}", "POR", 6.0) for i in range(1)]
        + [_p(f"def{i}", "DEF", 6.0 + i * 0.1) for i in range(5)]
        + [_p(f"cen{i}", "CEN", 6.0 + i * 0.1) for i in range(5)]
        + [_p(f"del{i}", "DEL", 7.0 + i * 0.1) for i in range(2)]
    )
    lineup = scoring.best_lineup(players)
    assert lineup is not None
    assert lineup["formation"].endswith("-2")  # solo 2 delanteros disponibles
    assert len(lineup["starters"]) == 11


def test_best_lineup_bench_excludes_starters():
    players = (
        [_p("por1", "POR", 6.0), _p("por2", "POR", 5.0)]
        + [_p(f"def{i}", "DEF", 6.0) for i in range(5)]
        + [_p(f"cen{i}", "CEN", 6.0) for i in range(5)]
        + [_p(f"del{i}", "DEL", 6.0) for i in range(3)]
    )
    lineup = scoring.best_lineup(players)
    starter_ids = {p["id"] for p in lineup["starters"]}
    bench_ids = {p["id"] for p in lineup["bench"]}
    assert starter_ids.isdisjoint(bench_ids)
    assert "por2" in bench_ids  # el segundo portero, peor puntuado, al banquillo


def test_best_lineup_includes_formation_comparison_sorted_best_first():
    players = (
        [_p(f"por{i}", "POR", 6.0) for i in range(1)]
        + [_p(f"def{i}", "DEF", 6.0 + i * 0.1) for i in range(5)]
        + [_p(f"cen{i}", "CEN", 6.0 + i * 0.1) for i in range(5)]
        + [_p(f"del{i}", "DEL", 7.0 + i * 0.1) for i in range(3)]
    )
    lineup = scoring.best_lineup(players)
    all_formations = lineup["all_formations"]
    assert len(all_formations) == len(scoring.FORMATIONS)  # todas caben con esta plantilla
    totals = [f["total"] for f in all_formations]
    assert totals == sorted(totals, reverse=True)  # de mejor a peor
    assert all_formations[0]["formation"] == lineup["formation"]
    assert all_formations[0]["total"] == lineup["total"]


def test_best_lineup_none_when_squad_incomplete():
    # Solo 1 defensa disponible: ninguna formación habitual cabe.
    players = [_p("por1", "POR", 6.0), _p("def1", "DEF", 6.0)]
    assert scoring.best_lineup(players) is None


def test_best_lineup_ignores_players_without_score():
    players = (
        [_p("por1", "POR", 6.0)]
        + [_p(f"def{i}", "DEF", 6.0) for i in range(5)]
        + [_p(f"cen{i}", "CEN", 6.0) for i in range(5)]
        + [_p(f"del{i}", "DEL", 6.0) for i in range(3)]
        + [{"id": "lesionado1", "name": "lesionado1", "position": "DEL", "score": None}]
    )
    lineup = scoring.best_lineup(players)
    starter_ids = {p["id"] for p in lineup["starters"]}
    bench_ids = {p["id"] for p in lineup["bench"]}
    assert "lesionado1" not in starter_ids
    assert "lesionado1" not in bench_ids
