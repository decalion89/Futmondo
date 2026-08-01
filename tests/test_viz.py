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


def test_sparkline_svg_coords_include_price_label_without_dates():
    spark = viz.sparkline_svg([1_000_000, 1_200_000])
    assert len(spark["coords"]) == 2
    assert spark["coords"][0]["label"] == "1.000.000€"
    assert spark["coords"][1]["label"] == "1.200.000€"


def test_sparkline_svg_coords_include_date_when_given():
    spark = viz.sparkline_svg([1_000_000, 1_200_000], dates=["2026-08-10", "2026-08-11"])
    assert spark["coords"][0]["label"] == "10 ago: 1.000.000€"
    assert spark["coords"][1]["label"] == "11 ago: 1.200.000€"


def test_sparkline_svg_dates_align_to_last_values_when_mismatched_length():
    # Si vienen más fechas que precios (no debería pasar, pero por seguridad),
    # nos quedamos con las últimas N fechas para que sigan alineadas al final.
    spark = viz.sparkline_svg([1_000_000, 1_200_000], dates=["2026-08-08", "2026-08-10", "2026-08-11"])
    assert spark["coords"][0]["label"] == "10 ago: 1.000.000€"
    assert spark["coords"][1]["label"] == "11 ago: 1.200.000€"


def test_format_date_handles_bad_input_gracefully():
    assert viz._format_date(None) is None
    assert viz._format_date("not-a-date") == "not-a-date"
    assert viz._format_date("2026-01-05T00:00:00.000Z") == "5 ene"
