"""Tests del sparkline de tendencia de precio (services/viz.py)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import viz


def test_sparkline_svg_none_with_insufficient_data():
    assert viz.sparkline_svg([]) is None
    assert viz.sparkline_svg([5]) is None
    assert viz.sparkline_svg(None) is None


def test_sparkline_svg_generates_right_number_of_points():
    spark = viz.sparkline_svg([1, 2, 3, 4, 5])
    assert len(spark["points"].split(" ")) == 5


def test_sparkline_svg_first_and_last_point_at_edges():
    spark = viz.sparkline_svg([10, 20, 30], width=64, padding=2)
    points = [p.split(",") for p in spark["points"].split(" ")]
    assert float(points[0][0]) == 2.0        # primer punto pegado al padding izquierdo
    assert float(points[-1][0]) == 62.0       # último punto pegado al padding derecho


def test_sparkline_svg_trend_class_matches_direction():
    rising = viz.sparkline_svg([10, 12, 15])
    falling = viz.sparkline_svg([15, 12, 10])
    assert rising["trend_class"] == "trend-up"
    assert falling["trend_class"] == "trend-down"


def test_sparkline_svg_ignores_none_values():
    spark = viz.sparkline_svg([10, None, 20, None, 30])
    assert len(spark["points"].split(" ")) == 3
