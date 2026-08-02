"""Tests de services/futbolfantasy.py: parseo del HTML real de las fichas
de equipo (estructura confirmada a mano el 2026-08-01 contra Real Madrid y
Deportivo) y el matching de nombres. Sin red: usa fragmentos de HTML de
ejemplo con la forma real confirmada, no fixtures inventadas a ciegas.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import futbolfantasy as ff


def _player_tag(slug, probability, lesion="-1", sancionado="0", nodisponible="0"):
    """Reproduce la forma real confirmada del elemento <a class="camiseta">
    de una ficha de equipo real de futbolfantasy.com."""
    href = f"https://www.futbolfantasy.com/jugadores/{slug}" if slug else "#"
    return (
        f'<a class="camiseta" href="{href}" data-probabilidad="{probability}%" '
        f'data-lesion="{lesion}" data-sancionado="{sancionado}" data-nodisponible="{nodisponible}">x</a>'
    )


def _page(*tags):
    return f"<html><body>{''.join(tags)}</body></html>"


def test_parse_team_lineup_extracts_probability_per_player():
    html = _page(
        _player_tag("federico-valverde", 80),
        _player_tag("thibaut-courtois", 70, lesion="2"),
    )
    lineup = ff.parse_team_lineup(html)
    assert lineup["federico-valverde"]["probability"] == 80
    assert lineup["federico-valverde"]["injured"] is False
    assert lineup["thibaut-courtois"]["probability"] == 70
    assert lineup["thibaut-courtois"]["injured"] is True


def test_parse_team_lineup_dedupes_field_and_list_view_duplicates():
    # Cada jugador real aparece dos veces en la página (vista de campo +
    # vista de lista) — debe contarse una sola vez.
    html = _page(
        _player_tag("federico-valverde", 80),
        _player_tag("federico-valverde", 80),
    )
    lineup = ff.parse_team_lineup(html)
    assert len(lineup) == 1


def test_parse_team_lineup_skips_placeholder_entries():
    html = _page(_player_tag(None, 50), _player_tag("federico-valverde", 80))
    lineup = ff.parse_team_lineup(html)
    assert list(lineup.keys()) == ["federico-valverde"]


def test_parse_team_lineup_flags_suspended_and_unavailable():
    html = _page(_player_tag("jugador-sancionado", 0, sancionado="1", nodisponible="1"))
    lineup = ff.parse_team_lineup(html)
    assert lineup["jugador-sancionado"]["suspended"] is True
    assert lineup["jugador-sancionado"]["unavailable"] is True


def test_parse_team_lineup_empty_without_probability_data():
    assert ff.parse_team_lineup("<html><body>sin nada</body></html>") == {}


def test_slugify_strips_accents_and_normalizes():
    assert ff._slugify("Kylian Mbappé") == "kylian-mbappe"
    assert ff._slugify("Aurélien Tchouaméni") == "aurelien-tchouameni"
    assert ff._slugify(None) == ""


def test_resolve_team_slug_known_team_names():
    assert ff._resolve_team_slug("Real Madrid") == "real-madrid"
    assert ff._resolve_team_slug("Athletic de Bilbao") == "athletic"
    assert ff._resolve_team_slug("Deportivo de la Coruña") == "deportivo"


def test_resolve_team_slug_none_for_unknown_team():
    assert ff._resolve_team_slug("Equipo Inventado FC") is None
    assert ff._resolve_team_slug(None) is None


def _team_data(lineup=None, injuries=None, incoming=None, outgoing=None):
    return {
        "lineup": lineup or {}, "injuries": injuries or {},
        "incoming": incoming or [], "outgoing": outgoing or [],
    }


def test_find_player_probability_exact_and_partial_match(monkeypatch):
    lineup = {"federico-valverde": {"probability": 80, "injured": False, "suspended": False, "unavailable": False}}
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data(lineup=lineup))

    exact = ff.find_player_probability("Federico Valverde", "Real Madrid")
    assert exact["probability"] == 80

    partial = ff.find_player_probability("Valverde", "Real Madrid")
    assert partial["probability"] == 80


def test_find_player_probability_none_when_not_found(monkeypatch):
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data())
    assert ff.find_player_probability("Nadie De Nadie", "Real Madrid") is None


def test_get_team_lineup_probabilities_empty_for_unknown_team():
    assert ff.get_team_lineup_probabilities("Equipo Inventado FC") == {}


def _injury_block(slug, name, reason, expected_return):
    return f'''
    <div class="elemento lesionado">
      <div class="datos">
        <a class="jugador" href="https://www.futbolfantasy.com/jugadores/{slug}">{name}</a>
        <div class="comentario">
          <span class="lesion">{reason}</span>
          <span><i class="far fa-calendar"></i> Desde 21/04 (102 días)</span>
          <span class="gravedad-0">{expected_return}</span>
        </div>
      </div>
    </div>
    '''


def test_parse_team_injuries_extracts_reason_and_return_date():
    html = f'''<html><body><div class="lesionados_wrapper"><section class="mod lesionados">
      {_injury_block("der-milito", "Éder Militao", "Lesión en el bíceps femoral", "Baja hasta finales de septiembre")}
    </section></div></body></html>'''
    injuries = ff.parse_team_injuries(html)
    assert injuries["der-milito"]["reason"] == "Lesión en el bíceps femoral"
    assert injuries["der-milito"]["expected_return"] == "Baja hasta finales de septiembre"


def test_parse_team_injuries_empty_without_section():
    assert ff.parse_team_injuries("<html><body>sin nada</body></html>") == {}


def _transfer_block(slug, name, status, team_names):
    teams_html = "".join(f'<span class="mercado-equipo-nombre">{t}</span>' for t in team_names)
    return f'''
    <div class="elemento sancionado mercado">
      <div class="datos">
        <a class="jugador" href="https://www.futbolfantasy.com/jugadores/{slug}">{name}</a>
        <span class="sancion">
          <span class="mercado-tags"><span class="mercado-tag-label">{status}</span></span>
          <span class="mercado-ruta">{teams_html}</span>
        </span>
      </div>
    </div>
    '''


def test_parse_team_transfers_extracts_incoming_and_outgoing():
    html = f'''<html><body>
      <section><header class="title">Posibles fichajes</header>
        {_transfer_block("yan-diomande", "Yan Diomande", "Cerrado", ["RB Leipzig", "Real Madrid"])}
      </section>
      <section><header class="title">Posibles salidas</header>
        {_transfer_block("gonzalo-garcia", "Gonzalo García", "Cerrado", ["Real Madrid", "Fulham"])}
      </section>
    </body></html>'''
    incoming, outgoing = ff.parse_team_transfers(html)
    assert len(incoming) == 1
    assert incoming[0]["name"] == "Yan Diomande"
    assert incoming[0]["status"] == "Cerrado"
    assert incoming[0]["other_team"] == "RB Leipzig"  # el equipo de origen, no el propio
    assert len(outgoing) == 1
    assert outgoing[0]["other_team"] == "Fulham"  # el equipo de destino, no el propio


def test_parse_team_transfers_empty_without_sections():
    incoming, outgoing = ff.parse_team_transfers("<html><body>sin nada</body></html>")
    assert incoming == []
    assert outgoing == []


def test_find_player_injury_detail_exact_match(monkeypatch):
    injuries = {"rodrygo-goes": {"reason": "Lesión muscular", "expected_return": "Baja hasta septiembre"}}
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data(injuries=injuries))
    detail = ff.find_player_injury_detail("Rodrygo Goes", "Real Madrid")
    assert detail["expected_return"] == "Baja hasta septiembre"


def test_find_player_injury_detail_none_when_not_injured(monkeypatch):
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data())
    assert ff.find_player_injury_detail("Nadie Lesionado", "Real Madrid") is None


def test_get_team_transfer_rumors_returns_both_lists(monkeypatch):
    incoming = [{"name": "X"}]
    outgoing = [{"name": "Y"}]
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data(incoming=incoming, outgoing=outgoing))
    result_in, result_out = ff.get_team_transfer_rumors("Real Madrid")
    assert result_in == incoming
    assert result_out == outgoing


def test_find_player_transfer_rumor_matches_outgoing_player(monkeypatch):
    outgoing = [{"name": "Gonzalo García", "slug": "gonzalo-garcia", "status": "Cerrado", "other_team": "Fulham"}]
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data(outgoing=outgoing))
    rumor = ff.find_player_transfer_rumor("Gonzalo García", "Real Madrid")
    assert rumor["other_team"] == "Fulham"


def test_find_player_transfer_rumor_none_when_not_leaving(monkeypatch):
    monkeypatch.setattr(ff, "get_team_page_data", lambda team_name: _team_data())
    assert ff.find_player_transfer_rumor("Nadie", "Real Madrid") is None


def _market_row(name, valor=5_000_000, tendencia=0, diff_pct7=0):
    """Reproduce la forma real confirmada el 2026-08-02 de una fila de
    /analytics/futmondo/mercado/social (tr.elemento_jugador con
    atributos data-*)."""
    return (
        f'<tr class="elemento_jugador" data-id="1" data-nombre="{name}" '
        f'data-posicion="Delantero" data-equipo="1" data-valor="{valor}" '
        f'data-tendencia="{tendencia}" data-diferencia-pct7="{diff_pct7}"></tr>'
    )


def test_parse_futmondo_market_extracts_value_and_trend():
    html = f"<html><body><table><tbody>{_market_row('isaac romero', valor=11111111, tendencia=8, diff_pct7=20.5)}</tbody></table></body></html>"
    data = ff.parse_futmondo_market(html)
    assert data["isaac-romero"]["value"] == 11111111.0
    assert data["isaac-romero"]["trend_streak_days"] == 8.0
    assert data["isaac-romero"]["change_pct_7d"] == 20.5


def test_parse_futmondo_market_empty_without_rows():
    assert ff.parse_futmondo_market("<html><body>sin nada</body></html>") == {}


def test_find_player_market_momentum_flags_rising_streak(monkeypatch):
    data = {"isaac-romero": {"value": 11111111, "trend_streak_days": 8, "change_pct_7d": 20.5}}
    monkeypatch.setattr(ff, "get_futmondo_market_data", lambda: data)
    momentum = ff.find_player_market_momentum("Isaac Romero")
    assert momentum["streak_days"] == 8
    assert momentum["cumulative_pct"] == 0.205
    assert momentum["direction"] == "up"


def test_find_player_market_momentum_flags_falling_streak(monkeypatch):
    data = {"jugador-bajista": {"value": 1000000, "trend_streak_days": -5, "change_pct_7d": -15}}
    monkeypatch.setattr(ff, "get_futmondo_market_data", lambda: data)
    momentum = ff.find_player_market_momentum("Jugador Bajista")
    assert momentum["streak_days"] == 5
    assert momentum["direction"] == "down"
    assert momentum["cumulative_pct"] == -0.15


def test_find_player_market_momentum_none_below_thresholds(monkeypatch):
    # Racha corta (menos de BUBBLE_MIN_STREAK_DAYS) o variación pequeña
    # (menos de BUBBLE_CUMULATIVE_THRESHOLD) no debe avisar — sería ruido.
    data = {
        "racha-corta": {"value": 5_000_000, "trend_streak_days": 1, "change_pct_7d": 20},
        "variacion-pequena": {"value": 5_000_000, "trend_streak_days": 8, "change_pct_7d": 1},
    }
    monkeypatch.setattr(ff, "get_futmondo_market_data", lambda: data)
    assert ff.find_player_market_momentum("Racha Corta") is None
    assert ff.find_player_market_momentum("Variacion Pequena") is None


def test_find_player_market_momentum_none_when_not_found(monkeypatch):
    monkeypatch.setattr(ff, "get_futmondo_market_data", lambda: {})
    assert ff.find_player_market_momentum("Nadie") is None


def _set_piece_row(name, penaltis=0, faltas_directas=0, corners=0):
    """Reproduce la forma real confirmada el 2026-08-02 de una fila de
    /analytics/balon-parado/jugadores."""
    return (
        f'<tr class="elemento_jugador" data-nombre="{name}" data-posicion="Delantero" '
        f'data-equipo="1" data-penaltis="{penaltis}" data-faltas-directas="{faltas_directas}" '
        f'data-corners-colgados="{corners}"></tr>'
    )


def test_parse_set_piece_takers_extracts_attempt_counts():
    html = f"<html><body><table><tbody>{_set_piece_row('mikel oyarzabal', penaltis=7)}</tbody></table></body></html>"
    data = ff.parse_set_piece_takers(html)
    assert data["mikel-oyarzabal"]["penalties_taken"] == 7
    assert data["mikel-oyarzabal"]["direct_free_kicks_taken"] == 0


def test_parse_set_piece_takers_empty_without_rows():
    assert ff.parse_set_piece_takers("<html><body>sin nada</body></html>") == {}


def test_find_player_set_pieces_matches_by_name(monkeypatch):
    data = {"mikel-oyarzabal": {"penalties_taken": 7, "direct_free_kicks_taken": 0, "corners_taken": 0}}
    monkeypatch.setattr(ff, "get_set_piece_data", lambda: data)
    result = ff.find_player_set_pieces("Mikel Oyarzabal")
    assert result["penalties_taken"] == 7


def test_find_player_set_pieces_none_when_not_found(monkeypatch):
    monkeypatch.setattr(ff, "get_set_piece_data", lambda: {})
    assert ff.find_player_set_pieces("Nadie") is None
