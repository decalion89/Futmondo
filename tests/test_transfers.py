"""Tests de services/transfers.py que no dependen de red real: build_reason
(pura) y scan_rival_weaknesses con un cliente de mentira que devuelve
respuestas de ejemplo con la forma real de la API de Futmondo."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import transfers, cache


def test_build_reason_flags_price_momentum():
    r = {"value": 5.0, "price_momentum": {"streak_days": 4, "cumulative_pct": 0.15}}
    reason = transfers.build_reason(r)
    assert "4 días" in reason
    assert "15%" in reason
    assert "hype" in reason.lower()


def test_build_reason_falls_back_to_plain_trend_without_momentum():
    r = {"value": 5.0, "price_trend": "up"}
    reason = transfers.build_reason(r)
    assert "subiendo" in reason.lower()


def test_build_reason_warns_before_anything_else_for_low_confidence_fringe():
    # Un jugador a precio mínimo sin datos reales no debe presumir de su
    # pts/M€ (inflado por dividir entre un precio casi nulo) como si fuera
    # una ganga real.
    r = {"value": 99.0, "score_from_price": True, "low_confidence_fringe": True}
    reason = transfers.build_reason(r)
    assert "sin apenas señal" in reason.lower() or "no es de fiar" in reason.lower()
    assert "99.0 pts/m€" not in reason.lower()


def test_build_reason_fringe_message_cites_real_probability_when_available():
    # Con dato real de once probable, el motivo debe citar el porcentaje
    # real en vez del genérico "precio mínimo" (más preciso y verificable).
    r = {"value": 99.0, "low_confidence_fringe": True, "titular_probability": 5}
    reason = transfers.build_reason(r)
    assert "5%" in reason
    assert "no es de fiar" in reason.lower()


def test_build_reason_includes_real_probability_for_non_fringe_candidates():
    r = {"value": 4.5, "titular_probability": 90}
    reason = transfers.build_reason(r)
    assert "90%" in reason
    assert "probabilidad real" in reason.lower()


def test_build_reason_leads_with_lineup_disagreement_when_present():
    r = {"value": 4.5, "lineup_disagreement": "⚠️ aviso de prueba"}
    reason = transfers.build_reason(r)
    assert reason.startswith("⚠️")


def test_build_reason_combines_disagreement_with_fringe_message():
    r = {
        "low_confidence_fringe": True,
        "titular_probability": 5,
        "lineup_disagreement": "⚠️ aviso de prueba",
    }
    reason = transfers.build_reason(r)
    assert reason.startswith("⚠️ aviso de prueba;")
    assert "5%" in reason


class _FakeClient:
    team_id = "me"
    championship_id = "champ1"

    def __init__(self, teams, rosters):
        self._teams = teams
        self._rosters = rosters

    def get_league_teams(self):
        return {"teams": self._teams, "configuration": {}}

    def get_roster(self, team_id=None):
        return {"players": self._rosters.get(team_id, [])}


def _rival_player(position, status=None):
    p = {"name": "X", "role": {"POR": "portero", "DEF": "defensa", "CEN": "centrocampista", "DEL": "delantero"}[position], "team": "Equipo", "value": 1_000_000}
    if status:
        p["status"] = status
    return p


def test_scan_rival_weaknesses_flags_team_below_minimum(monkeypatch):
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Corto de Porteros", "teamValue": 1, "points": 0},
    ]
    # Rival sin ningún portero disponible (el único que tiene está lesionado).
    rosters = {
        "rival1": [_rival_player("POR", status="injured2")] + [_rival_player("DEF") for _ in range(5)]
        + [_rival_player("CEN") for _ in range(5)] + [_rival_player("DEL") for _ in range(3)],
    }
    client = _FakeClient(teams, rosters)
    weaknesses = transfers.scan_rival_weaknesses(client)
    assert len(weaknesses) == 1
    assert weaknesses[0]["team_name"] == "Rival Corto de Porteros"
    gap_positions = {g["position"] for g in weaknesses[0]["gaps"]}
    assert "POR" in gap_positions


def test_scan_rival_weaknesses_skips_own_team_and_healthy_rivals(monkeypatch):
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Completo", "teamValue": 1, "points": 0},
    ]
    rosters = {
        "me": [_rival_player("POR")],  # no debería ni consultarse, pero por si acaso no debe aparecer
        "rival1": [_rival_player("POR")] + [_rival_player("DEF") for _ in range(5)]
        + [_rival_player("CEN") for _ in range(5)] + [_rival_player("DEL") for _ in range(3)],
    }
    client = _FakeClient(teams, rosters)
    weaknesses = transfers.scan_rival_weaknesses(client)
    assert weaknesses == []


def _priced_player(name, position, price, average=6.0, points=30, team="Rival Team"):
    return {
        "id": name,
        "name": name,
        "role": {"POR": "portero", "DEF": "defensa", "CEN": "centrocampista", "DEL": "delantero"}[position],
        "team": team,
        "value": price,
        "average": {"average": average, "averageLastFive": average},
        "points": points,
    }


def test_scan_rival_targets_returns_scored_candidates_with_clause_estimate(monkeypatch):
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Bueno", "teamValue": 1, "points": 0},
    ]
    rosters = {
        "rival1": [_priced_player("Estrella Rival", "DEL", 10_000_000, average=8.0, points=40)],
    }
    client = _FakeClient(teams, rosters)
    candidates, errors = transfers.scan_rival_targets(
        client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
        position_price_index={}, clause_increase_pct=0.25,
    )
    assert errors == []
    assert len(candidates) == 1
    assert candidates[0]["name"] == "Estrella Rival"
    assert candidates[0]["owner_team"] == "Rival Bueno"
    assert candidates[0]["clause_estimate"] == round(10_000_000 * 1.25)


def test_scan_rival_targets_excludes_own_team_and_fringe_candidates(monkeypatch):
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Bueno", "teamValue": 1, "points": 0},
    ]
    rosters = {
        "me": [_priced_player("MiJugador", "DEL", 10_000_000, average=8.0, points=40)],
        "rival1": [
            _priced_player("Estrella Rival", "DEL", 10_000_000, average=8.0, points=40),
            # Precio mínimo y sin datos reales -> fringe, debe quedar fuera.
            {"id": "fringe1", "name": "Suplente Relleno", "role": "delantero", "team": "Rival Team", "value": 1_000_000},
        ],
    }
    client = _FakeClient(teams, rosters)
    candidates, _ = transfers.scan_rival_targets(
        client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
        position_price_index={}, clause_increase_pct=0.25,
    )
    names = {c["name"] for c in candidates}
    assert "MiJugador" not in names  # nunca tu propia plantilla
    assert "Suplente Relleno" not in names  # fringe, sin señal real
    assert "Estrella Rival" in names


def test_scan_rival_targets_empty_without_rival_players(monkeypatch):
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [{"id": "me", "name": "Yo", "teamValue": 1, "points": 0}]
    client = _FakeClient(teams, {})
    candidates, errors = transfers.scan_rival_targets(
        client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
        position_price_index={}, clause_increase_pct=0.25,
    )
    assert candidates == []
    assert errors == []


def test_scan_rival_targets_clause_estimate_none_without_pct(monkeypatch):
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Bueno", "teamValue": 1, "points": 0},
    ]
    rosters = {"rival1": [_priced_player("Estrella Rival", "DEL", 10_000_000, average=8.0, points=40)]}
    client = _FakeClient(teams, rosters)
    candidates, _ = transfers.scan_rival_targets(
        client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
        position_price_index={}, clause_increase_pct=None,
    )
    assert candidates[0]["clause_estimate"] is None
