# Futmondo Manager

Herramienta personal para gestionar tu equipo de Futmondo: llevas tu plantilla
a mano (nombre, posición, equipo, precio) y la app la cruza con datos reales
de fútbol para avisarte de lesiones, sanciones y el próximo rival de cada
jugador.

## Por qué funciona así

Futmondo no tiene una API pública, así que tu plantilla (quién tienes
fichado y a qué precio) se gestiona a mano desde la propia app. Los datos
reales de fútbol (lesiones, sanciones, calendario) sí se pueden automatizar
con [API-Football](https://www.api-football.com/), que tiene un plan
gratuito (100 peticiones/día, de sobra para actualizar 1-2 veces por
jornada).

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Consigue tu API key gratis (2 minutos)

1. Entra en https://dashboard.api-football.com/register y crea una cuenta
   gratuita (o vía RapidAPI: https://rapidapi.com/api-sports/api/api-football).
2. En tu panel verás una clave (API Key). Cópiala.
3. Pégala en el archivo `.env` que has creado:

   ```
   API_FOOTBALL_KEY=tu_clave_aqui
   ```

4. Por defecto la app usa LaLiga (`API_FOOTBALL_LEAGUE_ID=140`). Si tu liga
   de Futmondo es de otra competición, cambia ese ID en `.env` (Champions =
   2, Premier League = 39, etc. — la lista completa está en la
   documentación de API-Football).

## Arrancar la app

```bash
python app.py
```

Abre http://localhost:5000 en el navegador.

## Uso

1. Añade tus jugadores de Futmondo con el formulario ("Nombre", "Equipo",
   posición y precio si quieres llevarlo apuntado).
2. Pulsa **"Actualizar datos"** para sincronizar con API-Football: verás
   quién está sancionado, lesionado, en duda, y contra quién juega la
   próxima jornada (con una pista de dificultad basada en los goles que
   encaja el rival).
3. Repite la sincronización 1-2 veces por jornada (antes de que cierre el
   mercado es el momento clave).

Los datos de tu plantilla se guardan en `data/squad.json` — puedes editarlo
a mano si prefieres, es un archivo de texto simple.

## (Opcional, avanzado) Conectar con la API interna de Futmondo

Futmondo no publica una API oficial, pero como cualquier app web hace
peticiones a un backend que puedes ver tú mismo en el navegador. Si quieres
automatizar también la lectura de tu plantilla directamente desde Futmondo
(en vez de meterla a mano), estos son los pasos:

1. Abre https://www.futmondo.com y haz login normalmente.
2. Abre las herramientas de desarrollador del navegador (F12 o clic derecho
   → Inspeccionar) y ve a la pestaña **Network** (Red).
3. Filtra por "Fetch/XHR" y navega por tu plantilla, mercado, etc. dentro de
   Futmondo.
4. Verás peticiones a un dominio de API (algo como
   `api.futmondo.com/...`). Haz clic en una y mira:
   - La URL y el método (GET/POST).
   - Los **headers**, en particular si hay un token de autenticación
     (`Authorization`, una cookie de sesión, etc.).
   - La respuesta JSON (ahí verás la forma de los datos: id de jugador,
     precio, puntos...).
5. **No pegues el token/cookie en el chat** — es una credencial de tu cuenta.
   Guárdalo solo en tu `.env` local (por ejemplo como `FUTMONDO_TOKEN=...`),
   que ya está en `.gitignore` y nunca se sube al repositorio.
6. Con esa info puedo ayudarte a escribir un cliente
   (`services/futmondo.py`) parecido al de `services/api_football.py` que
   lea tu plantilla real automáticamente.

Ten en cuenta que al ser una API no oficial puede cambiar sin aviso, y que
solo debe usarse para leer tus propios datos (nunca para automatizar
acciones masivas ni acceder a cuentas ajenas).

## Estructura del proyecto

```
app.py                  # rutas Flask
services/api_football.py  # cliente de API-Football
services/store.py         # guardado de tu plantilla en JSON
services/sync.py          # cruza plantilla + datos reales
templates/, static/       # interfaz web
data/squad.json           # tu plantilla (se crea al añadir el primer jugador)
```
