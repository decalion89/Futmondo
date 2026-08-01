"""Tests de las funciones puras de services/store.py (sin tocar disco)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import store


def test_add_score_entry_appends_new_entry():
    history = {}
    entries = store.add_score_entry(history, "p1", "2026-08-15", 6.5)
    assert entries == [{"date": "2026-08-15", "score": 6.5}]
    assert history["p1"] == entries


def test_add_score_entry_replaces_same_date_instead_of_duplicating():
    history = {"p1": [{"date": "2026-08-15", "score": 6.5}]}
    entries = store.add_score_entry(history, "p1", "2026-08-15", 7.2)
    assert entries == [{"date": "2026-08-15", "score": 7.2}]


def test_add_score_entry_keeps_entries_sorted_by_date():
    history = {}
    store.add_score_entry(history, "p1", "2026-08-22", 7.0)
    store.add_score_entry(history, "p1", "2026-08-15", 6.5)
    entries = store.add_score_entry(history, "p1", "2026-08-29", 5.0)
    assert [e["date"] for e in entries] == ["2026-08-15", "2026-08-22", "2026-08-29"]


def test_add_score_entry_caps_history_length():
    history = {}
    for i in range(store.SCORE_HISTORY_MAX_ENTRIES + 10):
        entries = store.add_score_entry(history, "p1", f"day-{i:03d}", 6.0 + i * 0.01)  # distinta cada vez
    assert len(entries) <= store.SCORE_HISTORY_MAX_ENTRIES


def test_add_score_entry_skips_duplicate_score_on_a_different_day():
    # Entre jornadas, Futmondo no actualiza la media/puntos hasta que se
    # juega el siguiente partido — sincronizar en días distintos sin que
    # haya cambiado nada no debe generar una entrada nueva por cada día.
    history = {"p1": [{"date": "2026-08-15", "score": 6.5}]}
    entries = store.add_score_entry(history, "p1", "2026-08-16", 6.5)
    assert entries == [{"date": "2026-08-15", "score": 6.5}]  # sin cambios, no se añade nada nuevo

    # En cuanto la puntuación cambia de verdad (nueva jornada jugada), sí
    # se añade como una entrada distinta.
    entries = store.add_score_entry(history, "p1", "2026-08-22", 7.1)
    assert entries == [
        {"date": "2026-08-15", "score": 6.5},
        {"date": "2026-08-22", "score": 7.1},
    ]


def test_add_score_entry_same_day_correction_ignores_value_dedup():
    # Corrección del mismo día: aunque el valor coincida con el anterior de
    # otro día, si la fecha es la misma que la última entrada, se sustituye
    # (no se compara con el valor).
    history = {"p1": [{"date": "2026-08-15", "score": 6.5}]}
    entries = store.add_score_entry(history, "p1", "2026-08-15", 6.9)
    assert entries == [{"date": "2026-08-15", "score": 6.9}]
