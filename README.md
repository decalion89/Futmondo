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

1. Importa tu plantilla real con el botón **"Importar plantilla real de
   Futmondo"** (requiere configurar `FUTMONDO_TOKEN`/`FUTMONDO_USER_ID`,
   ver más abajo). Si no lo configuras, puedes añadir jugadores a mano como
   alternativa.
2. Pulsa **"Actualizar datos"** para sincronizar con API-Football: verás
   quién está sancionado, lesionado, en duda, y contra quién juega la
   próxima jornada (con una pista de dificultad basada en los goles que
   encaja el rival).
3. Repite ambas sincronizaciones 1-2 veces por jornada (antes de que cierre
   el mercado es el momento clave).

Los datos de tu plantilla se guardan en `data/squad.json`.

## Importar tu plantilla real desde Futmondo (sin meter nada a mano)

Futmondo no tiene una API pública ni login automatizable por
usuario/contraseña (su web bloquea peticiones automatizadas simples), así
que la forma fiable de conectar tu cuenta es capturar tu **token de sesión**
una vez desde el navegador — se basa en cómo lo hace
[vicenteqa/futmondo-utils](https://github.com/vicenteqa/futmondo-utils), un
proyecto de la comunidad que ya reversea esta misma API.

1. Abre https://app.futmondo.com y haz login normalmente.
2. Abre las herramientas de desarrollador (F12 o clic derecho →
   Inspeccionar) → pestaña **Network** (Red) → filtra por "Fetch/XHR".
3. Entra en "Mi equipo" dentro de Futmondo para que se dispare la petición.
4. Busca una request **POST** a un dominio `api.futmondo.com` (por ejemplo
   a `/1/userteam/roster`). Haz clic en ella y abre su **Payload / Request
   body** (no los headers HTTP — el token va dentro del JSON que se envía).
5. Verás algo con esta forma:

   ```json
   {
     "header": { "token": "xxxxxxxx", "userid": "1234567" },
     "query": { "championshipId": "7654321", "userteamId": "9876543" }
   }
   ```

6. Copia esos 4 valores a tu `.env`:

   ```
   FUTMONDO_TOKEN=xxxxxxxx
   FUTMONDO_USER_ID=1234567
   FUTMONDO_CHAMPIONSHIP_ID=7654321
   FUTMONDO_TEAM_ID=9876543
   ```

7. **No pegues estos valores en el chat conmigo ni los subas a git** —
   equivalen a las llaves de tu sesión. El `.env` ya está en `.gitignore`.
8. Reinicia la app y pulsa "Importar plantilla real de Futmondo".

**Si la importación falla o no reconoce bien tus jugadores**: la respuesta
de Futmondo no está documentada oficialmente, así que el mapeo de campos en
`services/futmondo.py::normalize_roster` es una aproximación. La app guarda
la respuesta cruda en `data/futmondo_raw_roster.json` — ábrelo, y si me
compartes su estructura (sin el token) puedo ajustar el mapeo.

**Notas importantes**:
- El token es una API no oficial y puede caducar o dejar de funcionar si
  Futmondo cambia algo — si eso pasa, vuelve a capturarlo (pasos de arriba).
- Úsalo solo para leer tus propios datos, con una frecuencia razonable (1-2
  veces por jornada), nunca para automatizar acciones masivas.

## Estructura del proyecto

```
app.py                  # rutas Flask
services/api_football.py  # cliente de API-Football
services/store.py         # guardado de tu plantilla en JSON
services/sync.py          # cruza plantilla + datos reales
templates/, static/       # interfaz web
data/squad.json           # tu plantilla (se crea al añadir el primer jugador)
```
