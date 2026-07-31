"""Lógica de puntuación y valor compartida entre tu plantilla y el mercado.

Objetivo: maximizar puntos por jornada (elección de capitán/alineación) y
maximizar puntos por euro gastado (decisiones de fichaje/venta). Es una
heurística transparente, no una predicción exacta — pondera datos objetivos
(forma reciente, calendario real, minutos jugados, congestión de calendario
por Champions/Europa League/Copa del Rey) que tú no puedes revisar partido
a partido para 15+ jugadores.
"""
from datetime import datetime, timedelta, timezone

HORIZON = 5  # nº de próximos partidos que miramos para medir la racha de calendario
FATIGUE_WINDOW_DAYS = 10  # ventana para medir congestión de calendario (todas las competiciones)


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


def fixture_congestion(recent_fixtures, reference_date=None, window_days=FATIGUE_WINDOW_DAYS):
    """Cuenta partidos jugados en CUALQUIER competición (Liga, Champions,
    Europa League, Copa del Rey...) en los últimos `window_days` días, para
    detectar riesgo de cansancio/rotación por calendario apretado que un
    vistazo solo al calendario de Liga no muestra."""
    reference_date = reference_date or datetime.now(timezone.utc)
    cutoff = reference_date - timedelta(days=window_days)
    played = []
    for fx in recent_fixtures or []:
        try:
            played_at = datetime.fromisoformat(fx["fixture"]["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        if cutoff <= played_at <= reference_date:
            played.append({
                "date": fx["fixture"]["date"],
                "competition": (fx.get("league") or {}).get("name"),
            })
    competitions = sorted({m["competition"] for m in played if m["competition"]})
    return {"count": len(played), "competitions": competitions, "matches": played}


def fatigue_factor(congestion_count):
    """Penalización por acumulación de partidos en poco tiempo. Jugar 3+
    partidos en ~10 días (típico de una semana con Champions/Europa/Copa de
    por medio) es exigente incluso para plantillas largas; 2 es manejable
    pero con algo más de riesgo de rotación puntual."""
    if congestion_count is None:
        return 1.0
    if congestion_count >= 4:
        return 0.82
    if congestion_count == 3:
        return 0.9
    if congestion_count == 2:
        return 0.97
    return 1.0


SEASON_TOTAL_GAMES = 38  # LaLiga a 20 equipos
LOW_STAKES_RANK_RANGE = (8, 14)  # zona media: ni Europa/título ni descenso
SEASON_PROGRESS_FOR_DEAD_RUBBER = 0.7  # a partir de qué % de jornadas jugadas empieza a notarse


def team_motivation_factor(standings_row, total_games=SEASON_TOTAL_GAMES):
    """¿Se juega algo el equipo? Con la clasificación (que ya pedimos para
    el calendario) miramos si está en pelea de título/Europa o de descenso
    (motivación alta) o si está instalado en la zona media sin nada en juego
    ya avanzada la temporada (motivación algo más baja: son los clásicos
    'partidos de trámite' que rinden menos, sobre todo defensivamente).

    Es una aproximación por posición en tabla, no un modelo de probabilidad
    de descenso/Europa real — pero capta el caso más claro: un equipo
    14º a falta de 3 jornadas no se juega nada.
    """
    if not standings_row:
        return 1.0
    rank = standings_row.get("rank")
    played = (standings_row.get("all") or {}).get("played")
    if rank is None or not played:
        return 1.0
    if not (LOW_STAKES_RANK_RANGE[0] <= rank <= LOW_STAKES_RANK_RANGE[1]):
        return 1.0  # pelea título/Europa o pelea el descenso
    season_progress = played / total_games
    if season_progress < SEASON_PROGRESS_FOR_DEAD_RUBBER:
        return 1.0  # aún puede cambiar mucho, no lo tratamos como trámite
    return 0.93


CARD_SUSPENSION_THRESHOLD = 5  # amarillas acumuladas que suelen disparar sanción en LaLiga


def card_suspension_risk(yellow_cards):
    """True si el jugador está a una amarilla de una sanción probable por
    acumulación. Aproximado: LaLiga aplica ciclos de acumulación con
    reinicios en fechas concretas de la temporada que no modelamos aquí, así
    que trátalo como aviso a vigilar, no como certeza."""
    if yellow_cards is None:
        return False
    return yellow_cards % CARD_SUSPENSION_THRESHOLD == CARD_SUSPENSION_THRESHOLD - 1


def is_penalty_taker(penalty_scored, penalty_missed):
    """Señal de rol importante: si ha lanzado penaltis esta temporada
    (marcados o fallados), es indicio fuerte de ser el lanzador designado."""
    return bool((penalty_scored or 0) + (penalty_missed or 0) > 0)


# Peso por gol/asistencia según posición: en los sistemas de puntuación tipo
# Fantasy (LaLiga Fantasy/Comunio/Biwenger/Futmondo comparten esta
# convención) un gol de portero o defensa vale más puntos que uno de
# delantero — un central contundente por arriba es un multiplicador de
# puntos que el rating genérico no refleja. Fuente: guías de estrategia de
# jugadores que han ganado estas ligas (ver README).
ATTACKING_BONUS_WEIGHT = {"POR": 1.4, "DEF": 1.2, "CEN": 1.0, "DEL": 0.8}
ATTACKING_BONUS_SCALE = 3.0


def attacking_output_bonus(position, goals, assists, appearences):
    """Pequeño extra de puntuación por aportación ofensiva (goles + 0.7 x
    asistencias por partido), ponderado más para posiciones donde esa
    aportación vale más puntos en el sistema de puntuación."""
    if not appearences:
        return 0.0
    contributions_per_game = ((goals or 0) + 0.7 * (assists or 0)) / appearences
    weight = ATTACKING_BONUS_WEIGHT.get(position, 1.0)
    return round(contributions_per_game * weight * ATTACKING_BONUS_SCALE, 3)


def player_score(
    position, rating, starter_rate, swing, congestion_count=None, motivation_factor=1.0,
    penalty_taker=False, goals=None, assists=None, appearences=None,
):
    """Puntuación relativa para comparar tus propios jugadores disponibles
    entre sí (no es una predicción de puntos Futmondo)."""
    base = rating if rating is not None else 6.0
    base += attacking_output_bonus(position, goals, assists, appearences)
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
    fatigue = fatigue_factor(congestion_count)
    penalty_bonus = 1.05 if penalty_taker else 1.0
    return round(base * fixture_factor * reliability * fatigue * (motivation_factor or 1.0) * penalty_bonus, 2)


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


CONCENTRATION_WARNING_THRESHOLD = 0.6  # % del valor total en pocos jugadores a partir del cual avisamos


def budget_concentration(prices, top_n=2):
    """% del valor total de la plantilla que está en tus `top_n` jugadores
    más caros. Las guías de estrategia de ganadores de estas ligas
    recomiendan dedicar un 40-50% del presupuesto a 1-2 jugadores clave y
    repartir el resto — concentrar demasiado en pocas estrellas deja el
    resto de la plantilla débil."""
    values = sorted((v for v in prices if v), reverse=True)
    total = sum(values)
    if not total:
        return None
    return round(sum(values[:top_n]) / total, 3)


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
