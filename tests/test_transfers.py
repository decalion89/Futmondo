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


def test_build_reason_always_answers_jugara_never_silent(monkeypatch):
    # Sin dato de titularidad real, debe decirlo explícitamente en vez de
    # simplemente omitir la pregunta — precisión: no sabemos algo, se dice.
    r = {"value": 4.5}
    reason = transfers.build_reason(r)
    assert "¿jugará?" in reason.lower()
    assert "sin dato real de titularidad" in reason.lower()


def test_build_reason_jugara_question_comes_first_when_no_disagreement():
    r = {"value": 4.5, "titular_probability": 80}
    reason = transfers.build_reason(r)
    assert reason.lower().startswith("¿jugará?")


def test_build_reason_frames_clean_sheet_for_defenders_and_goalkeepers():
    for position in ("DEF", "POR"):
        r = {"value": 4.5, "position": position, "next_rival": "Getafe", "is_home": True, "win_prob": 0.7}
        reason = transfers.build_reason(r)
        assert "portería a cero" in reason.lower()


def test_build_reason_frames_goal_opportunity_for_midfielders_and_forwards():
    for position in ("CEN", "DEL"):
        r = {"value": 4.5, "position": position, "next_rival": "Getafe", "is_home": True, "win_prob": 0.7}
        reason = transfers.build_reason(r)
        assert "gol" in reason.lower()
        assert "portería a cero" not in reason.lower()


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
    """Simula el endpoint bulk /5/league/championshipplayers: `rosters` es
    {id de equipo manager: [jugadores]}, y aquí se les añade `userteamId`/
    `userteam` como haría Futmondo de verdad, y se renombra `team` (nombre
    de equipo real usado en los tests) a `teamId` — como no hay tabla de
    equipos reales en el fake, real_team_names_by_id() no resuelve nada y
    normalize_championship_players cae al propio `teamId` como nombre, que
    es justo el string que puso el test."""
    team_id = "me"
    championship_id = "champ1"

    def __init__(self, teams, rosters):
        self._teams = teams
        self._rosters = rosters

    def get_league_teams(self):
        return {"teams": self._teams, "configuration": {}}

    def get_real_teams(self):
        return []

    def get_championship_players(self):
        team_names = {t["id"]: t.get("name") for t in self._teams}
        players = []
        for owner_id, roster in self._rosters.items():
            for p in roster:
                item = dict(p)
                item["userteamId"] = owner_id
                item["userteam"] = team_names.get(owner_id, owner_id)
                item["teamId"] = item.pop("team", None)
                players.append(item)
        return {"players": players}


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


def _priced_player(name, position, price, average=6.0, points=30, team="Rival Team", clause_price=None):
    p = {
        "id": name,
        "name": name,
        "role": {"POR": "portero", "DEF": "defensa", "CEN": "centrocampista", "DEL": "delantero"}[position],
        "team": team,
        "value": price,
        "average": {"average": average, "averageLastFive": average},
        "points": points,
    }
    if clause_price is not None:
        p["clause"] = {"price": clause_price}
    return p


def test_scan_rival_targets_uses_real_clause_price_when_available(monkeypatch):
    # El precio real de clausulazo (clause.price de Futmondo) manda sobre
    # cualquier estimación por porcentaje — es el dato exacto, no un cálculo.
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Bueno", "teamValue": 1, "points": 0},
    ]
    rosters = {
        "rival1": [_priced_player("Estrella Rival", "DEL", 10_000_000, average=8.0, points=40, clause_price=18_750_000)],
    }
    client = _FakeClient(teams, rosters)
    candidates, _ = transfers.scan_rival_targets(
        client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
        position_price_index={}, clause_increase_pct=90,  # si se usara, daría un número muy distinto
    )
    assert candidates[0]["clause_estimate"] == 18_750_000
    assert candidates[0]["clause_is_estimate"] is False


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
        position_price_index={}, clause_increase_pct=25,
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
        position_price_index={}, clause_increase_pct=25,
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
        position_price_index={}, clause_increase_pct=25,
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


def test_scan_rival_targets_uses_real_league_enabling_clause_value(monkeypatch):
    # Confirmado el 2026-08-02 contra /1/userteam/information real: la liga
    # del usuario tiene "enablingClause": 10, es decir +10%, no 0.1.
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Bueno", "teamValue": 1, "points": 0},
    ]
    rosters = {"rival1": [_priced_player("Estrella Rival", "DEL", 10_000_000, average=8.0, points=40)]}
    client = _FakeClient(teams, rosters)
    candidates, _ = transfers.scan_rival_targets(
        client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
        position_price_index={}, clause_increase_pct=10,
    )
    assert candidates[0]["clause_estimate"] == 11_000_000
    assert candidates[0]["clause_is_estimate"] is True


def test_scan_rival_targets_clause_estimate_none_when_pct_implausible(monkeypatch):
    # enablingClause usa -1 como centinela "sin límite/desactivado" en varios
    # campos de configuración de Futmondo (visto también en rc, mcpw, rcp,
    # vmb de /1/userteam/information) y en producción con otra liga dio un
    # valor que generaba importes NEGATIVOS — sin un rango plausible, mejor
    # no mostrar número que mostrar uno erróneo.
    monkeypatch.setattr(cache, "get_or_set", lambda key, fn, ttl=None: fn())
    teams = [
        {"id": "me", "name": "Yo", "teamValue": 1, "points": 0},
        {"id": "rival1", "name": "Rival Bueno", "teamValue": 1, "points": 0},
    ]
    rosters = {"rival1": [_priced_player("Estrella Rival", "DEL", 10_000_000, average=8.0, points=40)]}
    client = _FakeClient(teams, rosters)

    for bad_pct in (-1, 500):
        candidates, _ = transfers.scan_rival_targets(
            client, squad=[], benchmark_value=0.3, next_match_index={}, real_budget_cap=None,
            position_price_index={}, clause_increase_pct=bad_pct,
        )
        assert candidates[0]["clause_estimate"] is None


def _candidate(name, price, value, kind_field=None):
    d = {"name": name, "price": price, "value": value}
    if kind_field:
        d.update(kind_field)
    return d


def _squad_player(player_id, name="X", position="DEL"):
    return {"id": player_id, "name": name, "position": position, "price": 5_000_000}


def test_sell_candidates_flags_unavailable_players():
    squad = [_squad_player("p1")]
    status_cache = {"p1": {"status": "lesionado", "value": 3.0}}
    unavailable, benched_risk, worst_value = transfers.sell_candidates(squad, status_cache)
    assert len(unavailable) == 1
    assert unavailable[0]["reason"] == "Lesionado según Futmondo, no puntúa mientras dure"
    assert benched_risk == []
    assert worst_value == []


def test_sell_candidates_flags_healthy_but_probably_benched():
    # Sano según Futmondo (status "ok"), pero el once probable real le da
    # muy poca probabilidad de jugar -> señal de venta distinta a lesión.
    squad = [_squad_player("p1", name="BancoProbable")]
    status_cache = {"p1": {"status": "ok", "value": 2.0, "titular_probability": 10}}
    unavailable, benched_risk, worst_value = transfers.sell_candidates(squad, status_cache)
    assert unavailable == []
    assert len(benched_risk) == 1
    assert benched_risk[0]["name"] == "BancoProbable"
    assert "10%" in benched_risk[0]["reason"]
    assert "sin estar lesionado" in benched_risk[0]["reason"]


def test_sell_candidates_does_not_flag_healthy_starters_as_benched():
    squad = [_squad_player("p1", name="TitularClaro")]
    status_cache = {"p1": {"status": "ok", "value": 5.0, "titular_probability": 90}}
    _, benched_risk, _ = transfers.sell_candidates(squad, status_cache)
    assert benched_risk == []


def test_sell_candidates_no_false_positive_without_titular_data():
    # Sin dato real de titularidad, no se puede afirmar que esté en el
    # banco -> no debe aparecer como riesgo de banquillo sin pruebas.
    squad = [_squad_player("p1")]
    status_cache = {"p1": {"status": "ok", "value": 3.0}}
    _, benched_risk, _ = transfers.sell_candidates(squad, status_cache)
    assert benched_risk == []


def test_sell_candidates_worst_value_still_sorted_ascending():
    squad = [_squad_player("p1", name="Peor"), _squad_player("p2", name="Mejor")]
    status_cache = {
        "p1": {"status": "ok", "value": 0.2},
        "p2": {"status": "ok", "value": 3.0},
    }
    _, _, worst_value = transfers.sell_candidates(squad, status_cache, top=5)
    assert [p["name"] for p in worst_value] == ["Peor", "Mejor"]


def test_build_transfer_plan_buys_what_fits_within_funds():
    fichar = [_candidate("Barato", 5_000_000, 4.0)]
    plan = transfers.build_transfer_plan(fichar, [], [], available_funds=10_000_000)
    assert len(plan) == 1
    assert plan[0]["kind"] == "fichar"
    assert plan[0]["player"]["name"] == "Barato"
    assert plan[0]["cost"] == 5_000_000
    assert plan[0]["sell_player"] is None


def test_build_transfer_plan_sells_worst_to_afford_target_that_does_not_fit():
    fichar = [_candidate("Caro", 12_000_000, 5.0)]
    vender = [_candidate("Prescindible", 5_000_000, 0.1)]
    plan = transfers.build_transfer_plan(fichar, vender, [], available_funds=10_000_000)
    assert len(plan) == 1
    assert plan[0]["sell_player"]["name"] == "Prescindible"
    assert plan[0]["cost"] == 12_000_000


def test_build_transfer_plan_skips_target_when_even_selling_is_not_enough():
    fichar = [_candidate("Inalcanzable", 50_000_000, 9.0)]
    vender = [_candidate("Poca_cosa", 1_000_000, 0.1)]
    plan = transfers.build_transfer_plan(fichar, vender, [], available_funds=10_000_000)
    assert plan == []


def test_build_transfer_plan_prioritizes_by_value_across_fichar_and_clausulazo():
    fichar = [_candidate("MenosValor", 1_000_000, 2.0)]
    clausulazo = [{"name": "MasValor", "clause_estimate": 1_000_000, "value": 8.0}]
    plan = transfers.build_transfer_plan(fichar, [], clausulazo, available_funds=1_000_000)
    # Solo cabe uno (el dinero se agota con el primero) -> debe ser el de más valor.
    assert len(plan) == 1
    assert plan[0]["player"]["name"] == "MasValor"
    assert plan[0]["kind"] == "clausulazo"


def test_build_transfer_plan_does_not_reuse_the_same_seller_twice():
    fichar = [_candidate("A", 12_000_000, 5.0), _candidate("B", 12_000_000, 4.0)]
    vender = [_candidate("UnicoVendible", 5_000_000, 0.1)]
    plan = transfers.build_transfer_plan(fichar, vender, [], available_funds=10_000_000)
    # Solo el primero (mejor valor) puede completarse vendiendo; para el
    # segundo ya no queda a quién vender.
    assert len(plan) == 1
    assert plan[0]["player"]["name"] == "A"


def test_build_transfer_plan_empty_without_available_funds():
    assert transfers.build_transfer_plan([_candidate("X", 1, 1.0)], [], [], available_funds=None) == []


def test_build_transfer_plan_skips_candidates_without_parseable_cost():
    clausulazo = [{"name": "SinClausula", "clause_estimate": None, "value": 9.0}]
    plan = transfers.build_transfer_plan([], [], clausulazo, available_funds=10_000_000)
    assert plan == []
