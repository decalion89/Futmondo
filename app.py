import hmac
import json
import os
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from dotenv import load_dotenv

load_dotenv()

from services import store, scoring
from services.sync import sync_all
from services.api_football import ApiFootballClient
from services.futmondo import FutmondoClient, FutmondoError, normalize_roster
from services import transfers as transfers_service

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
        rows.append({**p, **info})

    order = {"sancionado": 0, "lesionado": 1, "duda": 2, "ok": 3}
    rows.sort(key=lambda r: order.get(r.get("status", "ok"), 3))

    captain = max(
        (r for r in rows if r.get("status") == "ok" and r.get("score") is not None),
        key=lambda r: r["score"],
        default=None,
    )

    last_sync = max((r["updated_at"] for r in rows if r.get("updated_at")), default=None)

    return render_template(
        "index.html",
        players=rows,
        api_enabled=api_enabled,
        futmondo_enabled=futmondo_enabled,
        positions=store.POSITIONS,
        captain=captain,
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

    if futmondo_client.enabled:
        try:
            raw = futmondo_client.get_market()
            listings = normalize_roster(raw)
        except FutmondoError as e:
            errors.append(str(e))

    squad = store.load_squad()
    status_cache = store.load_status_cache()
    unavailable, worst_value = ([], [])
    benchmark_value = scoring.DEFAULT_VALUE_BENCHMARK
    if api_enabled:
        unavailable, worst_value = transfers_service.sell_candidates(squad, status_cache)
        squad_values = [status_cache.get(p["id"], {}).get("value") for p in squad]
        benchmark_value = scoring.squad_value_benchmark(squad_values)

    ranked = []
    if listings and api_enabled:
        ranked, rank_errors = transfers_service.rank_market(listings, benchmark_value)
        errors.extend(rank_errors)

    return render_template(
        "transfers.html",
        ranked=ranked,
        unavailable=unavailable,
        worst_value=worst_value,
        errors=errors,
        futmondo_enabled=futmondo_client.enabled,
        api_enabled=api_enabled,
        benchmark_value=benchmark_value,
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
    with open(raw_path, "w") as f:
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
