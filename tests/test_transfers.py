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


class _FakeClient:
    team_id = "me"

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
