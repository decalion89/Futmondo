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


def test_find_player_probability_exact_and_partial_match(monkeypatch):
    lineup = {"federico-valverde": {"probability": 80, "injured": False, "suspended": False, "unavailable": False}}
    monkeypatch.setattr(ff, "get_team_lineup_probabilities", lambda team_name: lineup)

    exact = ff.find_player_probability("Federico Valverde", "Real Madrid")
    assert exact["probability"] == 80

    partial = ff.find_player_probability("Valverde", "Real Madrid")
    assert partial["probability"] == 80


def test_find_player_probability_none_when_not_found(monkeypatch):
    monkeypatch.setattr(ff, "get_team_lineup_probabilities", lambda team_name: {})
    assert ff.find_player_probability("Nadie De Nadie", "Real Madrid") is None


def test_get_team_lineup_probabilities_empty_for_unknown_team():
    assert ff.get_team_lineup_probabilities("Equipo Inventado FC") == {}
