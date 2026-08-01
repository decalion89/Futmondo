import hmac
import json
import os
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from dotenv import load_dotenv

load_dotenv()

from services import store, scoring
from services.sync import sync_all
from services.api_football import ApiFootballClient
from services.futmondo import (
    FutmondoClient, FutmondoError, normalize_roster, normalize_league_teams,
    next_match_by_team, collect_known_players, normalize_pressroom, get_price_history,
)
from services import transfers as transfers_service
from services import viz
from services import futmondo_magazine

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "futmondo-local-dev")

APP_USERNAME = os.environ.get("APP_USERNAME")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def require_auth():
    """Si defines APP_USERNAME/APP_PASSWORD, protege toda la app con Basic
    Auth. Imprescindible en cuanto la despliegues en una URL pública (por
    ejemplo Render) para que nadie más pueda ver tu plantilla."""
    if not (APP_USERNAME and APP_PASSWORD):
        return None
    auth = request.authorization
    valid = (
        auth
        and hmac.compare_digest(auth.username, APP_USERNAME)
        and hmac.compare_digest(auth.password, APP_PASSWORD)
    )
    if not valid:
        return Response(
            "Acceso restringido", 401, {"WWW-Authenticate": 'Basic realm="Futmondo Manager"'}
        )
    return None


@app.route("/")
def dashboard():
    players = store.load_squad()
    status_cache = store.load_status_cache()
    api_enabled = ApiFootballClient().enabled
    futmondo_client = FutmondoClient()
    futmondo_enabled = futmondo_client.enabled

    rows = []
    for p in players:
        info = status_cache.get(p["id"], {})
        row = {**p, **info}
        # Si aún no se ha sincronizado con API-Football pero Futmondo ya
        # trajo su propio estado de lesión/sanción al importar, mostrarlo
        # igualmente en vez de "sin sincronizar".
        if "status" not in info and p.get("futmondo_status"):
            row["status"] = p["futmondo_status"]
            row["reason"] = "Marcado por Futmondo"
        row["price_trend"] = scoring.price_trend(p.get("price"), p.get("futmondo_price_change"))
        row["purchase_profit"] = scoring.purchase_profit(p.get("price"), p.get("futmondo_buy_price"))
        rows.append(row)

    order = {"sancionado": 0, "lesionado": 1, "duda": 2, "ok": 3}
    rows.sort(key=lambda r: order.get(r.get("status", "ok"), 3))

    # Algunas ligas de Futmondo desactivan la figura del capitán (sin
    # puntos dobles) — configúralo en .env si es tu caso.
    captain_enabled = os.environ.get("FUTMONDO_CAPTAIN_ENABLED", "true").lower() != "false"
    captain = max(
        (r for r in rows if r.get("status") == "ok" and r.get("score") is not None),
        key=lambda r: r["score"],
        default=None,
    ) if captain_enabled else None
    top_performer = None if captain_enabled else max(
        (r for r in rows if r.get("status") == "ok" and r.get("score") is not None),
        key=lambda r: r["score"],
        default=None,
    )

    last_sync = max((r["updated_at"] for r in rows if r.get("updated_at")), default=None)
    lineup = scoring.best_lineup(rows)
    pitch = viz.pitch_layout(lineup["starters"]) if lineup else None

    prices = [scoring.parse_price(r.get("price")) for r in rows]
    total_value = sum(v for v in prices if v)
    available_count = sum(1 for r in rows if r.get("status") == "ok")
    alert_count = sum(1 for r in rows if r.get("status") in ("lesionado", "sancionado", "duda"))
    concentration = scoring.budget_concentration(prices)
    concentration_warning = concentration is not None and concentration >= scoring.CONCENTRATION_WARNING_THRESHOLD

    # Resumen de decisiones: mejores fichajes disponibles ahora mismo en el
    # mercado real (sin contar a quienes no podrías fichar por el límite de
    # jugadores del mismo equipo) y a quién te conviene vender, con el
    # motivo de cada uno — para no tener que ir a Fichajes a buscarlo.
    market_result = transfers_service.full_market_ranking(futmondo_client, players, status_cache)
    top_buys = [
        r for r in market_result["ranked"]
        if not r.get("team_limit_reached") and not r.get("low_confidence_fringe")
    ][:3]
    for r in top_buys:
        r["status"] = r.get("futmondo_status") or "ok"
    _, benched_risk, worst_value_top = transfers_service.sell_candidates(players, status_cache, top=2)
    top_sells = (benched_risk + worst_value_top)[:2]

    # Alerta temprana: discrepancias entre el estado oficial de Futmondo y
    # la señal independiente de futbolfantasy.com — te enteras antes de que
    # Futmondo actualice su estado oficial, o antes que un rival que solo
    # mire una fuente.
    lineup_alerts = [r for r in rows if r.get("lineup_disagreement")]

    return render_template(
        "index.html",
        players=rows,
        api_enabled=api_enabled,
        futmondo_enabled=futmondo_enabled,
        positions=store.POSITIONS,
        captain=captain,
        captain_enabled=captain_enabled,
        lineup_alerts=lineup_alerts,
        top_performer=top_performer,
        total_value=total_value,
        available_count=available_count,
        alert_count=alert_count,
        concentration=concentration,
        concentration_warning=concentration_warning,
        last_sync=last_sync,
        lineup=lineup,
        pitch=pitch,
        top_buys=top_buys,
        top_sells=top_sells,
        my_rank=market_result["my_rank"],
        total_teams=market_result["total_teams"],
    )


@app.route("/mercado")
def market():
    client = FutmondoClient()
    listings, error, raw = [], None, None
    if client.enabled:
        try:
            raw = client.get_market()
            listings = normalize_roster(raw)
            for p in listings:
                p["price_trend"] = scoring.price_trend(p.get("price"), p.get("futmondo_price_change"))
                player_id = p.get("futmondo_player_id")
                if player_id:
                    try:
                        history = get_price_history(client, player_id)
                        p["sparkline"] = viz.sparkline_svg(
                            [h["price"] for h in history.get("history", [])],
                            dates=[h["date"] for h in history.get("history", [])],
                        )
                        p["price_momentum"] = scoring.price_momentum_flag(history.get("history", []))
                    except FutmondoError:
                        p["sparkline"] = None
        except FutmondoError as e:
            error = str(e)

    # Mismo "qué hacer" que Fichajes, para no dejar el mercado como una
    # tabla en bruto sin ninguna guía — el detalle completo sigue en
    # Fichajes, esto es solo el titular.
    top_picks = []
    if client.enabled:
        squad = store.load_squad()
        status_cache = store.load_status_cache()
        result = transfers_service.full_market_ranking(client, squad, status_cache)
        top_picks = [
            r for r in result["ranked"]
            if not r.get("team_limit_reached") and not r.get("low_confidence_fringe")
        ][:3]

    return render_template(
        "market.html",
        listings=listings,
        top_picks=top_picks,
        error=error,
        futmondo_enabled=client.enabled,
        raw_debug=raw if not listings else None,
    )


@app.route("/fichajes")
def transfers():
    futmondo_client = FutmondoClient()
    api_enabled = ApiFootballClient().enabled

    squad = store.load_squad()
    status_cache = store.load_status_cache()
    unavailable, benched_risk, worst_value = transfers_service.sell_candidates(squad, status_cache)

    result = transfers_service.full_market_ranking(futmondo_client, squad, status_cache)
    ranked = result["ranked"]

    # Sparkline de tendencia de precio real (histórico día a día de
    # /1/player/summary, cacheado) para los mejores candidatos — no lo
    # pedimos para los 16 si hay muchos, solo para los que se ven primero.
    if futmondo_client.enabled:
        for r in ranked[:10]:
            player_id = r.get("futmondo_player_id")
            if not player_id:
                continue
            try:
                history = get_price_history(futmondo_client, player_id)
                r["sparkline"] = viz.sparkline_svg(
                    [h["price"] for h in history.get("history", [])],
                    dates=[h["date"] for h in history.get("history", [])],
                )
                r["price_momentum"] = scoring.price_momentum_flag(history.get("history", []))
                r["reason"] = transfers_service.build_reason(r)
            except FutmondoError:
                r["sparkline"] = None

    # Objetivos en plantillas rivales (clausulazo) — mismos criterios que el
    # mercado abierto, para que un buen fichaje no se te escape solo porque
    # nunca sale a subasta libre.
    rival_targets = []
    if futmondo_client.enabled:
        rival_targets, rival_target_errors = transfers_service.scan_rival_targets(
            futmondo_client, squad, result["benchmark_value"], result["next_match_index"],
            result["real_budget_cap"], result["position_price_index"], result["clause_increase_pct"],
        )
        result["errors"].extend(rival_target_errors)

    # "Qué hacer ahora": el resumen accionable de arriba de la página — el
    # detalle completo (todas las tablas) sigue disponible más abajo, pero
    # colapsado, para quien quiera revisarlo entero.
    top_fichar = [
        r for r in ranked
        if not r.get("team_limit_reached") and not r.get("low_confidence_fringe")
    ][:5]
    top_clausulazo = rival_targets[:5]
    top_vender = unavailable + benched_risk + worst_value[:3]

    # El plan de HOY: qué te cabe de verdad con tu dinero disponible ahora
    # mismo, y si vender a alguien te permite llegar a un objetivo mejor.
    # Solo se puede calcular con fondos reales (no el tope con margen de
    # puja, que infla lo que "cabe" simultáneamente en varias compras).
    daily_plan = transfers_service.build_transfer_plan(
        top_fichar, top_vender, top_clausulazo, result["available_funds"],
    )

    return render_template(
        "transfers.html",
        ranked=ranked,
        rival_targets=rival_targets,
        unavailable=unavailable,
        benched_risk=benched_risk,
        worst_value=worst_value,
        top_fichar=top_fichar,
        top_clausulazo=top_clausulazo,
        top_vender=top_vender,
        daily_plan=daily_plan,
        available_funds=result["available_funds"],
        errors=result["errors"],
        futmondo_enabled=futmondo_client.enabled,
        api_enabled=api_enabled,
        benchmark_value=result["benchmark_value"],
        real_budget_cap=result["real_budget_cap"],
        resale_lock_days=result["resale_lock_days"],
    )


@app.route("/liga")
def league():
    client = FutmondoClient()
    teams, configuration, error = [], {}, None
    activity = []
    if client.enabled:
        try:
            raw = client.get_league_teams()
            teams, configuration = normalize_league_teams(raw)
            for t in teams:
                t["is_me"] = t.get("id") == client.team_id
            # Clasificación real por puntos (no por valor de equipo, que es
            # el orden por defecto de `teams`) — la necesitamos para saber
            # si te conviene jugar a "suelo" (vas líder) o a "techo" (vas
            # remontando). En pretemporada todos están a 0 puntos, así que
            # esto no dice nada todavía hasta que arranque la liga.
            points_ranking = sorted(teams, key=lambda t: t.get("points") or 0, reverse=True)
            for idx, t in enumerate(points_ranking, start=1):
                t["points_rank"] = idx
        except FutmondoError as e:
            error = str(e)
        try:
            raw_pressroom = client.get_pressroom()
            activity = normalize_pressroom(raw_pressroom)
        except FutmondoError as e:
            error = error or str(e)
    weaknesses = []
    if client.enabled:
        try:
            weaknesses = transfers_service.scan_rival_weaknesses(client)
        except FutmondoError as e:
            error = error or str(e)
    # Fuente pública oficial (magazine.futmondo.com), no depende de tu
    # token — funciona aunque Futmondo no esté configurado.
    magazine_posts = futmondo_magazine.get_latest_posts(limit=5)
    return render_template(
        "league.html",
        teams=teams,
        configuration=configuration,
        activity=activity,
        weaknesses=weaknesses,
        magazine_posts=magazine_posts,
        error=error,
        futmondo_enabled=client.enabled,
    )


@app.route("/liga/<team_id>")
def league_team(team_id):
    client = FutmondoClient()
    listings, team_name, error = [], None, None
    if client.enabled:
        try:
            raw_teams = client.get_league_teams()
            teams, _ = normalize_league_teams(raw_teams)
            match = next((t for t in teams if t["id"] == team_id), None)
            team_name = match["name"] if match else None

            raw_roster = client.get_roster(team_id=team_id)
            listings = normalize_roster(raw_roster)
            for p in listings:
                p["status"] = p.get("futmondo_status") or "ok"
        except FutmondoError as e:
            error = str(e)
    return render_template(
        "league_team.html",
        listings=listings,
        team_name=team_name,
        team_id=team_id,
        error=error,
        futmondo_enabled=client.enabled,
        positions=store.POSITIONS,
    )


@app.route("/importar-futmondo", methods=["POST"])
def import_futmondo():
    client = FutmondoClient()
    try:
        raw = client.get_roster()
    except FutmondoError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard"))

    raw_path = os.path.join(store.DATA_DIR, "futmondo_raw_roster.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    normalized = normalize_roster(raw)
    if not normalized:
        flash(
            "Se conectó con Futmondo pero no se pudo interpretar tu plantilla. "
            "Revisa data/futmondo_raw_roster.json y comparte su estructura para ajustar el mapeo.",
            "error",
        )
        return redirect(url_for("dashboard"))

    store.import_roster(normalized)
    flash(f"Importados {len(normalized)} jugadores desde Futmondo", "ok")
    return redirect(url_for("dashboard"))


@app.route("/jugadores/nuevo", methods=["POST"])
def add_player():
    name = request.form["name"].strip()
    position = request.form["position"]
    team = request.form["team"].strip()
    price = request.form.get("price", "").strip()
    if not name or not team:
        flash("Nombre y equipo son obligatorios", "error")
        return redirect(url_for("dashboard"))
    store.add_player(name, position, team, price or None)
    flash(f"{name} añadido a la plantilla", "ok")
    return redirect(url_for("dashboard"))


@app.route("/jugadores/<player_id>/eliminar", methods=["POST"])
def remove_player(player_id):
    store.remove_player(player_id)
    flash("Jugador eliminado", "ok")
    return redirect(url_for("dashboard"))


@app.route("/sync", methods=["POST"])
def sync():
    _, errors = sync_all()
    if errors:
        for e in errors:
            flash(e, "error")
    else:
        flash("Datos actualizados correctamente", "ok")
    return redirect(url_for("dashboard"))


@app.route("/debug/fullprofile/<player_id>")
def debug_fullprofile(player_id):
    """TEMPORAL: inspeccionar /1/player/fullprofile, encontrado por el
    usuario en la pestaña Red del navegador. Se retira tras confirmar la
    estructura."""
    client = FutmondoClient()
    try:
        raw = client.get_player_fullprofile(player_id)
    except FutmondoError as e:
        return {"error": str(e)}, 500
    return raw


if __name__ == "__main__":
    app.run(debug=True, port=5000)
