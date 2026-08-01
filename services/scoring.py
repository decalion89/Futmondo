"""Lógica de puntuación y valor compartida entre tu plantilla y el mercado.

Objetivo: maximizar puntos por jornada (elección de capitán/alineación) y
maximizar puntos por euro gastado (decisiones de fichaje/venta). Es una
heurística transparente, no una predicción exacta — pondera datos objetivos
(forma reciente, calendario real, minutos jugados, congestión de calendario
por Champions/Europa League/Copa del Rey) que tú no puedes revisar partido
a partido para 15+ jugadores.
"""
import statistics
from datetime import datetime, timedelta, timezone

HORIZON = 5  # nº de próximos partidos que miramos para medir la racha de calendario
FATIGUE_WINDOW_DAYS = 10  # ventana para medir congestión de calendario (todas las competiciones)

MIN_HISTORY_FOR_CONSISTENCY = 4  # jornadas mínimas antes de fiarnos de una varianza calculada


def score_consistency(history):
    """Desviación estándar de la puntuación de un jugador a lo largo de las
    últimas jornadas (histórico que vamos guardando en cada sync) — cuanto
    más baja, más "suelo" (fiable, sin ceros); cuanto más alta, más "techo"
    (puede darte un pleno o un cero). None si todavía no hay jornadas
    suficientes para que el dato signifique algo — con 1-3 puntos no hay
    varianza real que calcular, solo ruido."""
    scores = [e["score"] for e in (history or []) if e.get("score") is not None]
    if len(scores) < MIN_HISTORY_FOR_CONSISTENCY:
        return None
    return round(statistics.pstdev(scores), 2)


def team_form_factor(standings_row):
    """Racha reciente del equipo (últimos resultados W/D/L) que API-Football
    ya incluye en la clasificación (campo `form`) — no cuesta ninguna
    llamada extra y capta algo que la media de toda la temporada no ve: un
    equipo puede tener buena media anual pero estar en mal momento ahora
    mismo (o al revés). Devuelve un factor entre ~0.93 (mala racha) y
    ~1.07 (buena racha)."""
    if not standings_row:
        return 1.0
    form = (standings_row.get("form") or "")[-5:]
    if not form:
        return 1.0
    points = sum({"W": 3, "D": 1, "L": 0}.get(c, 0) for c in form)
    ratio = points / (len(form) * 3)
    return round(0.93 + ratio * 0.14, 3)


MIN_SPLIT_GAMES = 3  # partidos mínimos en casa/fuera antes de fiarnos de ese desglose


def _rival_split_goals(rival_row, rival_plays_home):
    """Goles a favor/en contra del rival, usando su desglose como local o
    visitante (lo que le toque en ese partido concreto) en vez de la media
    combinada de toda la temporada — un equipo puede ser sólido en general
    pero mucho más flojo fuera de casa, por ejemplo. Con muy pocos partidos
    en ese desglose (inicio de temporada), cae de vuelta a la media general
    para no sacar conclusiones de 1-2 partidos."""
    split_key = "home" if rival_plays_home else "away"
    split = rival_row.get(split_key) or {}
    played = split.get("played") or 0
    if played < MIN_SPLIT_GAMES:
        split = rival_row.get("all") or {}
        played = max(split.get("played", 1), 1)
    goals = split.get("goals") or {}
    return (goals.get("against") or 0) / played, (goals.get("for") or 0) / played


def fixture_swing(fixtures, team_id, standings, n=HORIZON):
    """Dificultad media de los próximos `n` partidos de un equipo, separada
    en dos lecturas porque afecta distinto según la posición:
    - avg_goals_against_rivals: goles que suele encajar el rival (alto = fácil marcarle, bueno para DEL/CEN)
    - avg_goals_for_rivals: goles que suele marcar el rival (alto = peligroso para nuestra portería, malo para DEF/POR)

    También calcula `next_rival_form_factor`: la racha reciente (no solo la
    media de temporada) del rival del PRÓXIMO partido concretamente, que es
    el que más peso debería tener en la decisión de esta jornada.
    """
    upcoming = (fixtures or [])[:n]
    attack_vals, defense_vals, detail = [], [], []
    next_rival_form_factor = 1.0
    for i, fx in enumerate(upcoming):
        teams = fx["teams"]
        is_home = teams["home"]["id"] == team_id
        rival_team = teams["away"] if is_home else teams["home"]
        rival_row = standings.get(str(rival_team["id"])) if standings else None
        if rival_row:
            rival_plays_home = not is_home  # si nosotros jugamos fuera, el rival juega en casa
            goals_against, goals_for = _rival_split_goals(rival_row, rival_plays_home)
            attack_vals.append(goals_against)
            defense_vals.append(goals_for)
            if i == 0:
                next_rival_form_factor = team_form_factor(rival_row)
        detail.append({
            "rival": rival_team["name"],
            "is_home": is_home,
            "date": fx["fixture"]["date"],
        })
    return {
        "avg_goals_against_rivals": round(sum(attack_vals) / len(attack_vals), 2) if attack_vals else None,
        "avg_goals_for_rivals": round(sum(defense_vals) / len(defense_vals), 2) if defense_vals else None,
        "next_rival_form_factor": next_rival_form_factor,
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
    """¿Se juega algo el equipo? API-Football suele incluir en la
    clasificación un campo `description` con la lectura oficial del
    proveedor sobre esa posición (p.ej. "Promotion - Champions League",
    "Relegation - LaLiga 2"). Si existe, es más fiable que adivinarlo por
    rango de posición, así que se usa primero; si viene vacío (zona media
    sin etiqueta) o no está disponible, caemos al rango de posición como
    aproximación.

    Motivación algo más baja = partido de trámite avanzada la temporada
    (rinde menos, sobre todo defensivamente); un equipo 14º a falta de 3
    jornadas no se juega nada.
    """
    if not standings_row:
        return 1.0
    played = (standings_row.get("all") or {}).get("played")
    if not played:
        return 1.0
    season_progress = played / total_games
    if season_progress < SEASON_PROGRESS_FOR_DEAD_RUBBER:
        return 1.0  # aún puede cambiar mucho, no lo tratamos como trámite

    if "description" in standings_row:
        # La clave existe en la respuesta real de la API: un valor vacío o
        # `null` ahí SÍ significa "sin etiqueta de zona", no "dato ausente".
        description = standings_row.get("description")
        return 1.0 if description else 0.93

    rank = standings_row.get("rank")
    if rank is None:
        return 1.0
    if not (LOW_STAKES_RANK_RANGE[0] <= rank <= LOW_STAKES_RANK_RANGE[1]):
        return 1.0  # pelea título/Europa o pelea el descenso
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


PRESEASON_BASE_MIN = 5.0
PRESEASON_BASE_MAX = 8.5


def price_percentile_base(price, position, position_prices):
    """En pretemporada no hay partidos jugados todavía, así que no hay
    rating/forma real de nadie — pero el precio que ya le pone Futmondo a
    cada jugador SÍ es una señal real (encierra su reputación/potencial,
    puesto por el propio mercado). Situamos el precio del jugador dentro de
    los precios de su misma posición que conocemos (tu plantilla + tus
    rivales + el mercado) y lo convertimos en una base en la misma escala
    que un rating real (5.0 flojo - 8.5 top), para no dar la misma
    puntuación a una estrella que a un suplente solo por falta de datos."""
    prices = sorted(p for p in (position_prices or {}).get(position, []) if p)
    parsed_price = parse_price(price)
    if not prices or not parsed_price:
        return None
    rank = sum(1 for p in prices if p <= parsed_price)
    percentile = rank / len(prices)
    return round(PRESEASON_BASE_MIN + percentile * (PRESEASON_BASE_MAX - PRESEASON_BASE_MIN), 2)


FUTMONDO_FLOOR_PRICE = 1_000_000  # precio mínimo de la plataforma: decenas de jugadores de relleno lo comparten


def is_low_confidence_fringe(price, has_real_data):
    """Un jugador al precio MÍNIMO de la plataforma (1M€, el mismo que
    comparten decenas de suplentes/canteranos de todos los equipos) y sin
    ni un partido real jugado no es "barato y con potencial" — es "sin
    apenas señal de que vaya a jugar". El problema no es solo de confianza:
    dividir cualquier puntuación entre un precio así de bajo dispara
    matemáticamente su pts/M€ por encima de jugadores reales bien
    valorados, aunque el motor no tenga ningún indicio de que vaya a pisar
    el campo. Se usa para NO dejar que estos casos ganen el ranking por
    pura aritmética del precio."""
    parsed = parse_price(price)
    return bool(parsed and parsed <= FUTMONDO_FLOOR_PRICE and not has_real_data)


def build_position_price_index(players):
    """A partir de una lista de jugadores ({position, price}), agrupa los
    precios por posición — la "materia prima" para price_percentile_base."""
    index = {}
    for p in players or []:
        price = parse_price(p.get("price"))
        position = p.get("position")
        if price and position:
            index.setdefault(position, []).append(price)
    return index


SHRINKAGE_PRIOR_GAMES = 3  # "peso" del precio como prior — a 10 partidos reales ya casi no influye
SHRINKAGE_FULL_TRUST_GAMES = 10  # a partir de aquí, tratamos el dato real como plenamente fiable
LOW_SAMPLE_GAMES_THRESHOLD = 3  # con menos partidos contabilizados, avisamos de que el dato tiene margen


def implied_games_played(points, average):
    """Futmondo no nos da directamente cuántos partidos lleva contabilizados
    un jugador en su media (`average`), pero si tenemos también sus puntos
    totales de temporada (`points`), el número de partidos se puede despejar
    (media = puntos / partidos). Nos hace falta para saber si un dato de
    forma es ya fiable (muchos partidos jugados) o todavía ruidoso (1-2
    partidos, donde un doblete puntual puede disparar la media de un
    suplente ocasional) — sin necesidad de ninguna llamada extra."""
    if not points or not average:
        return None
    return round(points / average)


def shrink_form_estimate(observed, games_played, prior):
    """Regresiona la media real hacia el precio-base (`prior`) cuando todavía
    respaldan pocos partidos, con un peso que crece según cuántos partidos
    ya hay detrás del dato — así no anticipamos titularidad segura a partir
    de 1-2 partidos sueltos, que es justo lo que no podemos saber todavía
    sin datos de alineaciones probables. Con muchos partidos, el dato real
    manda casi del todo."""
    if games_played is None or games_played <= 0 or prior is None:
        return observed
    weight_games = min(games_played, SHRINKAGE_FULL_TRUST_GAMES)
    return round((weight_games * observed + SHRINKAGE_PRIOR_GAMES * prior) / (weight_games + SHRINKAGE_PRIOR_GAMES), 2)


def fixture_factor_from_win_prob(win_prob):
    """Convierte la probabilidad de victoria implícita en las cuotas reales
    de Futmondo (mercado de apuestas, ya incorpora lesiones/forma/todo) en
    un factor de dificultad. Sirve igual para ataque y defensa: ser gran
    favorito suele significar rival flojo tanto atrás como delante, así
    que no hace falta separar por posición como con el cálculo antiguo
    basado en goles a favor/en contra."""
    if win_prob is None:
        return 1.0
    return round(0.75 + min(max(win_prob, 0.0), 1.0) * 0.6, 3)


def player_score(
    position, rating, starter_rate, swing, congestion_count=None, motivation_factor=1.0,
    penalty_taker=False, goals=None, assists=None, appearences=None,
    futmondo_form=None, futmondo_win_prob=None,
):
    """Puntuación relativa para comparar tus propios jugadores disponibles
    entre sí (no es una predicción de puntos Futmondo).

    Si hay datos propios de Futmondo (media de puntos reales del jugador,
    probabilidad de victoria de las cuotas de su próximo partido), se usan
    con prioridad por ser más directos/fiables; si no, cae en el cálculo
    basado en API-Football (rating genérico + goles del rival)."""
    base = futmondo_form if futmondo_form is not None else (rating if rating is not None else 6.0)
    base += attacking_output_bonus(position, goals, assists, appearences)

    if futmondo_win_prob is not None:
        fixture_factor = fixture_factor_from_win_prob(futmondo_win_prob)
    else:
        fixture_factor = 1.0
        if position in ("DEL", "CEN"):
            ga = swing.get("avg_goals_against_rivals")
            if ga is not None:
                fixture_factor = 0.85 + min(ga, 2.5) * 0.15
        elif position in ("DEF", "POR"):
            gf = swing.get("avg_goals_for_rivals")
            if gf is not None:
                fixture_factor = 1.15 - min(gf, 2.5) * 0.15
        # Un rival en buena racha reciente es más peligroso de lo que dice su
        # media anual (y viceversa), nos beneficie el partido en la dirección
        # que sea: por eso se invierte (racha rival alta = peor para nosotros).
        next_rival_form = swing.get("next_rival_form_factor", 1.0) or 1.0
        fixture_factor *= (2.0 - next_rival_form)

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


def real_budget_max_bid(budget, team_value, max_bid_over_funds_pct):
    """Tope real de puja que tu liga permite físicamente pagar, según su
    configuración real (confirmado en /2/championship/teams): tus fondos
    disponibles (presupuesto inicial menos el valor actual de tu plantilla
    — así calcula Futmondo los fondos cuando la liga "quita el valor del
    equipo del presupuesto") más un % extra sobre el valor de tu equipo.
    No es una recomendación de rentabilidad, es un límite físico: por
    mucho que un jugador valga la pena, no puedes pujar más de esto."""
    if budget is None or team_value is None or max_bid_over_funds_pct is None:
        return None
    funds = budget - team_value
    return round(funds + max_bid_over_funds_pct * team_value)


PRICE_TREND_THRESHOLD = 0.02  # % de variación a partir del cual lo consideramos una tendencia, no ruido


def price_trend(price, price_change):
    """Clasifica la variación de precio reciente (campo real `change` de
    Futmondo) en "subiendo"/"bajando"/"estable", relativa al precio actual
    para que un mismo cambio en € pese distinto en un jugador barato que en
    uno caro. Útil para especular: comprar antes de que siga subiendo,
    vender antes de que empiece a bajar."""
    parsed_price = parse_price(price)
    parsed_change = parse_price(price_change) if price_change is not None else None
    if not parsed_price or parsed_change is None:
        return None
    ratio = parsed_change / parsed_price
    if ratio >= PRICE_TREND_THRESHOLD:
        return "up"
    if ratio <= -PRICE_TREND_THRESHOLD:
        return "down"
    return "flat"


BUBBLE_MIN_STREAK_DAYS = 3  # días consecutivos de subida para considerarlo una racha, no ruido
BUBBLE_CUMULATIVE_THRESHOLD = 0.08  # % de subida acumulada en la racha a partir del cual avisamos


def price_momentum_flag(history):
    """Detecta una subida de precio sostenida varios días seguidos (histórico
    real día a día de Futmondo) — la "regla del 5% diario" que usan quienes
    especulan en el mercado: una racha así puede ser un jugador que de verdad
    está mejorando, o puede ser hype de la comunidad comprando por su cuenta
    sin que haya cambiado nada en su rendimiento (eso no lo sabemos con solo
    el histórico de precio, así que lo avisamos como racha a vigilar, no
    como burbuja confirmada).

    Devuelve None si no hay racha, o un dict {"streak_days", "cumulative_pct"}
    con la racha de subidas consecutivas más reciente (termina en el último
    día del histórico) si supera el mínimo de días y de subida acumulada."""
    prices = [h["price"] for h in (history or []) if h.get("price")]
    if len(prices) < BUBBLE_MIN_STREAK_DAYS + 1:
        return None

    streak_days = 0
    i = len(prices) - 1
    while i > 0 and prices[i] > prices[i - 1]:
        streak_days += 1
        i -= 1

    if streak_days < BUBBLE_MIN_STREAK_DAYS:
        return None

    start_price = prices[len(prices) - 1 - streak_days]
    end_price = prices[-1]
    if not start_price:
        return None
    cumulative_pct = (end_price - start_price) / start_price
    if cumulative_pct < BUBBLE_CUMULATIVE_THRESHOLD:
        return None
    return {"streak_days": streak_days, "cumulative_pct": round(cumulative_pct, 3)}


def purchase_profit(current_value, buy_price):
    """Ganancia (o pérdida) de valor desde que compraste el jugador por
    mercado. `buyPrice` de Futmondo viene a 0 cuando el jugador no fue una
    compra activa tuya (reparto inicial de la plantilla), así que en ese
    caso no tiene sentido calcular nada."""
    parsed_value = parse_price(current_value)
    parsed_buy = parse_price(buy_price)
    if not parsed_buy or parsed_value is None:
        return None
    return round(parsed_value - parsed_buy)


# Formaciones habituales (Portero, Defensas, Centrocampistas, Delanteros).
# Se prueban todas y se elige la que más puntuación total permite con los
# jugadores que tienes disponibles ahora mismo — no una formación fija.
FORMATIONS = [
    (1, 3, 4, 3), (1, 3, 5, 2), (1, 4, 3, 3), (1, 4, 4, 2),
    (1, 4, 5, 1), (1, 5, 3, 2), (1, 5, 4, 1),
]


def best_lineup(available_players, formations=FORMATIONS):
    """Elige la formación que maximiza la puntuación total con tus
    jugadores disponibles (status ok, con puntuación calculada) y quién
    ocupa cada puesto. `available_players` son dicts con al menos
    `position` y `score`. Devuelve None si no hay jugadores suficientes
    para completar NINGUNA formación habitual (once incompleto).

    Es una heurística de "quién puntúa más", no un plan táctico real — no
    sabemos qué XI juega cada equipo, solo comparamos tus propios jugadores
    entre sí.
    """
    by_position = {"POR": [], "DEF": [], "CEN": [], "DEL": []}
    for p in available_players or []:
        if p.get("score") is not None and p.get("position") in by_position:
            by_position[p["position"]].append(p)
    for pos in by_position:
        by_position[pos].sort(key=lambda p: p["score"], reverse=True)

    best = None
    tried = []
    for por, de, ce, dl in formations:
        counts = {"POR": por, "DEF": de, "CEN": ce, "DEL": dl}
        if any(len(by_position[pos]) < n for pos, n in counts.items()):
            continue
        starters = []
        for pos, n in counts.items():
            starters.extend(by_position[pos][:n])
        total = round(sum(p["score"] for p in starters), 2)
        tried.append({"formation": f"{por}-{de}-{ce}-{dl}", "total": total})
        if best is None or total > best["total"]:
            best = {"formation": f"{por}-{de}-{ce}-{dl}", "starters": starters, "total": total}

    if best is None:
        return None

    starter_keys = {p.get("id") or p.get("name") for p in best["starters"]}
    bench = [
        p for p in available_players
        if p.get("score") is not None and (p.get("id") or p.get("name")) not in starter_keys
    ]
    bench.sort(key=lambda p: p["score"], reverse=True)
    best["bench"] = bench
    # Comparativa de todas las formaciones habituales probadas, ordenada de
    # mejor a peor — así se ve CUÁNTO se gana eligiendo la óptima frente a
    # otras formaciones típicas, no solo cuál es.
    tried.sort(key=lambda f: f["total"], reverse=True)
    best["all_formations"] = tried
    return best
