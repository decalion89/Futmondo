"""Captura automáticamente tu token de sesión de Futmondo sin tocar F12.

Abre un navegador de verdad en tu ordenador. Tú inicias sesión con tu
usuario y contraseña normales de Futmondo (nunca se comparten con este
script, ni se envían a ningún sitio) y navegas a tu liga como siempre. En
cuanto Futmondo pida tu plantilla ("Mi equipo"), este script intercepta esa
petición de red y guarda automáticamente en tu `.env`:

    FUTMONDO_TOKEN, FUTMONDO_USER_ID, FUTMONDO_CHAMPIONSHIP_ID, FUTMONDO_TEAM_ID

Uso:
    pip install playwright
    playwright install chromium
    python scripts/capture_futmondo_token.py

Importante: entra en la liga con tus amigos ANTES de abrir "Mi equipo",
para que capture el championshipId/userteamId correctos (ver README).
"""
import re
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Falta playwright. Instálalo con: pip install playwright && playwright install chromium")
    sys.exit(1)

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
WAIT_TIMEOUT_SECONDS = 300


def _set_var(lines, key, value):
    pattern = re.compile(rf"^{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def main():
    captured = {}

    def handle_request(request):
        if captured.get("token"):
            return
        if request.method != "POST" or "api.futmondo.com" not in request.url:
            return
        if "roster" not in request.url:
            return
        try:
            body = request.post_data_json
        except Exception:
            return
        if not body:
            return
        header = body.get("header") or {}
        query = body.get("query") or {}
        if header.get("token") and header.get("userid"):
            captured["token"] = header["token"]
            captured["userid"] = header["userid"]
            captured["championshipId"] = query.get("championshipId")
            captured["userteamId"] = query.get("userteamId")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.on("request", handle_request)
        page.goto("https://app.futmondo.com")

        print("Navegador abierto. Pasos:")
        print("  1. Haz login con tu usuario/contraseña de Futmondo.")
        print("  2. Entra en la liga con tus amigos (si tienes varias ligas).")
        print("  3. Abre 'Mi equipo'.")
        print("Detectando automáticamente... (no cierres esta terminal)")

        waited = 0
        while not captured.get("token") and waited < WAIT_TIMEOUT_SECONDS:
            page.wait_for_timeout(1000)
            waited += 1

        browser.close()

    if not captured.get("token"):
        print(f"\nNo se detectó nada en {WAIT_TIMEOUT_SECONDS}s. ¿Llegaste a abrir 'Mi equipo'? Vuelve a intentarlo.")
        sys.exit(1)

    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    _set_var(lines, "FUTMONDO_TOKEN", captured["token"])
    _set_var(lines, "FUTMONDO_USER_ID", captured["userid"])
    if captured.get("championshipId"):
        _set_var(lines, "FUTMONDO_CHAMPIONSHIP_ID", captured["championshipId"])
    if captured.get("userteamId"):
        _set_var(lines, "FUTMONDO_TEAM_ID", captured["userteamId"])
    ENV_PATH.write_text("\n".join(lines) + "\n")

    print(f"\n¡Listo! Guardado en {ENV_PATH}")
    print("Ya puedes arrancar la app (python app.py) e importar tu plantilla.")


if __name__ == "__main__":
    main()
