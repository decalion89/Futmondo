"""Utilidades de visualización — hoy solo el sparkline de tendencia de
precio. Una sola serie por gráfico, sin ejes/leyenda (el número y la flecha
de al lado ya dan el valor exacto; el sparkline da la forma de la
tendencia), trazo fino de 2px con el extremo final marcado, color según si
la serie sube o baja en conjunto (mismas clases `trend-up`/`trend-down`
que el resto de la app, para no inventar una paleta nueva).

Cada punto lleva su fecha y precio para un hover nativo (SVG `<title>`,
sin JS) — la evolución de precio no es solo una forma, también quieres
saber "¿qué día pasó esto?" al pasar el ratón.
"""
import datetime


def _format_price(v):
    return f"{v:,.0f}".replace(",", ".") + "€"


def _format_date(value):
    """Fechas ISO de Futmondo -> "15 ago", legible en un tooltip corto.
    Si no es parseable, se devuelve tal cual (mejor un dato crudo que
    ninguno) — este sparkline vive dentro de tablas densas, así que el
    formato debe ser breve."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{parsed.day} {meses[parsed.month - 1]}"


def sparkline_svg(values, width=72, height=24, padding=3, dates=None):
    """A partir de una lista de precios (más antiguo primero) y, opcional,
    sus fechas correspondientes, devuelve los datos listos para dibujar un
    SVG compacto: puntos de la polilínea, un punto interactivo por valor
    (con su tooltip nativo "fecha: precio"), coordenadas del punto final
    (para marcarlo) y la clase de color según la tendencia neta de la
    serie. None si no hay suficientes puntos."""
    values = [v for v in (values or []) if v is not None]
    if len(values) < 2:
        return None
    dates = (dates or [])[-len(values):]

    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)
    coords = []
    for i, v in enumerate(values):
        x = padding + (i / (n - 1)) * (width - 2 * padding)
        y = height - padding - ((v - lo) / span) * (height - 2 * padding)
        date = _format_date(dates[i]) if i < len(dates) else None
        label = f"{date}: {_format_price(v)}" if date else _format_price(v)
        coords.append({"x": round(x, 1), "y": round(y, 1), "label": label})

    last = coords[-1]
    return {
        "points": " ".join(f"{c['x']},{c['y']}" for c in coords),
        "coords": coords,
        "last_x": last["x"],
        "last_y": last["y"],
        "width": width,
        "height": height,
        "trend_class": "trend-up" if values[-1] >= values[0] else "trend-down",
    }
