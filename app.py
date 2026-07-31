import os
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

load_dotenv()

from services import store
from services.sync import sync_all
from services.api_football import ApiFootballClient

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "futmondo-local-dev")


@app.route("/")
def dashboard():
    players = store.load_squad()
    status_cache = store.load_status_cache()
    api_enabled = ApiFootballClient().enabled

    rows = []
    for p in players:
        info = status_cache.get(p["id"], {})
        rows.append({**p, **info})

    order = {"sancionado": 0, "lesionado": 1, "duda": 2, "ok": 3}
    rows.sort(key=lambda r: order.get(r.get("status", "ok"), 3))

    return render_template(
        "index.html",
        players=rows,
        api_enabled=api_enabled,
        positions=store.POSITIONS,
    )


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
