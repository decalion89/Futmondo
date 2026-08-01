"""Utilidades de visualización — hoy solo el sparkline de tendencia de
precio. Una sola serie por gráfico, sin ejes/leyenda (el número y la flecha
de al lado ya dan el valor exacto; el sparkline da la forma de la
tendencia), trazo fino de 2px con el extremo final marcado, color según si
la serie sube o baja en conjunto (mismas clases `trend-up`/`trend-down`
que el resto de la app, para no inventar una paleta nueva).
"""


def sparkline_svg(values, width=64, height=20, padding=2):
    """A partir de una lista de precios (más antiguo primero), devuelve los
    datos listos para dibujar un SVG compacto: puntos de la polilínea,
    coordenadas del punto final (para marcarlo) y la clase de color según
    la tendencia neta de la serie. None si no hay suficientes puntos."""
    values = [v for v in (values or []) if v is not None]
    if len(values) < 2:
        return None

    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)
    coords = []
    for i, v in enumerate(values):
        x = padding + (i / (n - 1)) * (width - 2 * padding)
        y = height - padding - ((v - lo) / span) * (height - 2 * padding)
        coords.append((round(x, 1), round(y, 1)))

    last_x, last_y = coords[-1]
    return {
        "points": " ".join(f"{x},{y}" for x, y in coords),
        "last_x": last_x,
        "last_y": last_y,
        "width": width,
        "height": height,
        "trend_class": "trend-up" if values[-1] >= values[0] else "trend-down",
    }
