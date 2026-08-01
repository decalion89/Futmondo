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
    futmondo_enabled = FutmondoClient().enabled

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

    prices = [scoring.parse_price(r.get("price")) for r in rows]
    total_value = sum(v for v in prices if v)
    available_count = sum(1 for r in rows if r.get("status") == "ok")
    alert_count = sum(1 for r in rows if r.get("status") in ("lesionado", "sancionado", "duda"))
    concentration = scoring.budget_concentration(prices)
    concentration_warning = concentration is not None and concentration >= scoring.CONCENTRATION_WARNING_THRESHOLD

    return render_template(
        "index.html",
        players=rows,
        api_enabled=api_enabled,
        futmondo_enabled=futmondo_enabled,
        positions=store.POSITIONS,
        captain=captain,
        captain_enabled=captain_enabled,
        top_performer=top_performer,
        total_value=total_value,
        available_count=available_count,
        alert_count=alert_count,
        concentration=concentration,
        concentration_warning=concentration_warning,
        last_sync=last_sync,
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
        except FutmondoError as e:
            error = str(e)
    return render_template(
        "market.html",
        listings=listings,
        error=error,
        futmondo_enabled=client.enabled,
        raw_debug=raw if not listings else None,
    )


@app.route("/fichajes")
def transfers():
    futmondo_client = FutmondoClient()
    api_enabled = ApiFootballClient().enabled
    errors = []
    listings = []
    next_match_index = {}

    if futmondo_client.enabled:
        try:
            raw = futmondo_client.get_market()
            listings = normalize_roster(raw)
        except FutmondoError as e:
            errors.append(str(e))
        try:
            match_data = futmondo_client.get_match_list()
            next_match_index = next_match_by_team(match_data)
        except FutmondoError as e:
            errors.append(f"No se pudo leer el calendario de Futmondo: {e}")

    squad = store.load_squad()
    status_cache = store.load_status_cache()
    unavailable, worst_value = transfers_service.sell_candidates(squad, status_cache)
    squad_values = [status_cache.get(p["id"], {}).get("value") for p in squad]
    benchmark_value = scoring.squad_value_benchmark(squad_values)

    position_price_index = {}
    if futmondo_client.enabled:
        try:
            position_price_index = scoring.build_position_price_index(collect_known_players(futmondo_client) + squad)
        except FutmondoError as e:
            errors.append(f"No se pudo leer precios de referencia de la liga: {e}")

    # Tope real de puja según la configuración de tu liga (fondos + % sobre
    # el valor de tu equipo) — si no lo conseguimos, seguimos solo con el
    # tope por rentabilidad.
    real_budget_cap = None
    if futmondo_client.enabled:
        try:
            raw_teams = futmondo_client.get_league_teams()
            teams, configuration = normalize_league_teams(raw_teams)
            my_team = next((t for t in teams if t["id"] == futmondo_client.team_id), None)
            if my_team:
                real_budget_cap = scoring.real_budget_max_bid(
                    configuration.get("budget"), my_team.get("team_value"),
                    configuration.get("max_bid_over_funds_pct"),
                )
        except FutmondoError as e:
            errors.append(f"No se pudo leer la configuración de tu liga: {e}")

    ranked = []
    if listings:
        ranked, rank_errors = transfers_service.rank_market(
            listings, benchmark_value, squad, next_match_index, real_budget_cap, position_price_index,
        )
        errors.extend(rank_errors)

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
                prices = [h["price"] for h in history.get("history", [])]
                r["sparkline"] = viz.sparkline_svg(prices)
            except FutmondoError:
                r["sparkline"] = None

    return render_template(
        "transfers.html",
        ranked=ranked,
        unavailable=unavailable,
        worst_value=worst_value,
        errors=errors,
        futmondo_enabled=futmondo_client.enabled,
        api_enabled=api_enabled,
        benchmark_value=benchmark_value,
        real_budget_cap=real_budget_cap,
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
        except FutmondoError as e:
            error = str(e)
        try:
            raw_pressroom = client.get_pressroom()
            activity = normalize_pressroom(raw_pressroom)
        except FutmondoError as e:
            error = error or str(e)
    return render_template(
        "league.html",
        teams=teams,
        configuration=configuration,
        activity=activity,
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
