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
        entries = store.add_score_entry(history, "p1", f"day-{i:03d}", 6.0)
    assert len(entries) <= store.SCORE_HISTORY_MAX_ENTRIES
