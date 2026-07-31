"""Lógica de puntuación y valor compartida entre tu plantilla y el mercado.

Objetivo: maximizar puntos por jornada (elección de capitán/alineación) y
maximizar puntos por euro gastado (decisiones de fichaje/venta). Es una
heurística transparente, no una predicción exacta — pondera datos objetivos
(forma reciente, calendario real, minutos jugados) que tú no puedes revisar
partido a partido para 15+ jugadores.
"""

HORIZON = 5  # nº de próximos partidos que miramos para medir la racha de calendario


def fixture_swing(fixtures, team_id, standings, n=HORIZON):
    """Dificultad media de los próximos `n` partidos de un equipo, separada
    en dos lecturas porque afecta distinto según la posición:
    - avg_goals_against_rivals: goles que suele encajar el rival (alto = fácil marcarle, bueno para DEL/CEN)
    - avg_goals_for_rivals: goles que suele marcar el rival (alto = peligroso para nuestra portería, malo para DEF/POR)
    """
    upcoming = (fixtures or [])[:n]
    attack_vals, defense_vals, detail = [], [], []
    for fx in upcoming:
        teams = fx["teams"]
        is_home = teams["home"]["id"] == team_id
        rival_team = teams["away"] if is_home else teams["home"]
        rival_row = standings.get(str(rival_team["id"])) if standings else None
        if rival_row:
            played = max(rival_row["all"]["played"], 1)
            attack_vals.append(rival_row["all"]["goals"]["against"] / played)
            defense_vals.append(rival_row["all"]["goals"]["for"] / played)
        detail.append({
            "rival": rival_team["name"],
            "is_home": is_home,
            "date": fx["fixture"]["date"],
        })
    return {
        "avg_goals_against_rivals": round(sum(attack_vals) / len(attack_vals), 2) if attack_vals else None,
        "avg_goals_for_rivals": round(sum(defense_vals) / len(defense_vals), 2) if defense_vals else None,
        "fixtures": detail,
    }


def player_score(position, rating, starter_rate, swing):
    """Puntuación relativa para comparar tus propios jugadores disponibles
    entre sí (no es una predicción de puntos Futmondo)."""
    base = rating if rating is not None else 6.0
    fixture_factor = 1.0
    if position in ("DEL", "CEN"):
        ga = swing.get("avg_goals_against_rivals")
        if ga is not None:
            fixture_factor = 0.85 + min(ga, 2.5) * 0.15
    elif position in ("DEF", "POR"):
        gf = swing.get("avg_goals_for_rivals")
        if gf is not None:
            fixture_factor = 1.15 - min(gf, 2.5) * 0.15
    reliability = 0.7 + 0.3 * (starter_rate if starter_rate is not None else 0.5)
    return round(base * fixture_factor * reliability, 2)


def parse_price(price):
    """Los precios pueden llegar como número (API) o como texto con
    separadores de miles al estilo español ('5.652.156€') si algún día se
    pegan a mano. Intenta ambos formatos con cuidado."""
    if price is None or price == "":
        return None
    if isinstance(price, (int, float)):
        return float(price)
    text = str(price).replace("€", "").strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return float(text.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def value_for_money(score, price):
    """Puntos de forma por cada millón de euros. None si falta un dato."""
    parsed_price = parse_price(price)
    if not parsed_price or score is None:
        return None
    price_millions = parsed_price / 1_000_000
    if price_millions <= 0:
        return None
    return round(score / price_millions, 3)


DEFAULT_VALUE_BENCHMARK = 0.3  # pts/M€ de referencia si aún no hay datos de tu plantilla


def squad_value_benchmark(values, default=DEFAULT_VALUE_BENCHMARK):
    """Referencia de 'buena relación puntos/precio' a partir de tu propia
    plantilla (mediana de los jugadores disponibles), para no comparar
    fichajes contra un número inventado sino contra lo que tú ya pagas."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return default
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return round((values[mid - 1] + values[mid]) / 2, 3)


def max_recommended_bid(score, benchmark_value):
    """Precio máximo (subasta) que tendría sentido pagar para que el fichaje
    siga siendo, como mínimo, tan rentable (puntos por millón) como tu
    referencia. Pujar por encima es pagar más de lo que rinde en forma —
    puede compensar igualmente si es una posición sin alternativas, pero
    entonces es una decisión tuya, no una ganga."""
    if score is None or not benchmark_value or benchmark_value <= 0:
        return None
    return round((score / benchmark_value) * 1_000_000)
