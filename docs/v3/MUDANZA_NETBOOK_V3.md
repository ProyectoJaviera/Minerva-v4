# Mudanza: un solo Minerva en el netbook (la v3 reemplaza a la v1)

**Decisión del dueño (2026-11-11):** en el servidor tiene que existir **un solo Minerva**. Cada versión
reemplaza a la anterior. La v3 contiene todo: el bot y la medición de la v1 (sin un solo cambio), las
pestañas Indicadores y Estudio de la v2, y las pruebas en vivo de la v3.

**Quién lo hace: el dueño, a mano.** Ningún asistente entra al netbook. Esta guía reemplaza a
[NETBOOK_V3.md](NETBOOK_V3.md), que describía la v3 *al lado* de la v1 (opción descartada).

**Por qué no se pierde la medición.** El núcleo de la medición de la v3 es **idéntico** al de la v1.0:
`scripts/verify_v1_identity.py` lo demuestra archivo por archivo (141 archivos iguales, `app/config.py` y
`app/main.py` solo con líneas agregadas, el mismo prompt congelado). La mudanza equivale a un corte de luz de
unos minutos. Lo que sí hay que hacer bien es **llevar la base y el `.env`** (paso G): si la v3 arrancara con una
base vacía o con el LLM apagado, la medición se cortaría.

**Cuándo:** en la ventana segura, entre los minutos **:10 y :40**, evitando las **01, 05, 09, 13, 17 y 21 h**
(hora de Chile). Reserva una hora; los pasos A a F se pueden hacer antes, cualquier día.

Convenciones: **[PC]** es PowerShell en el PC, desde la carpeta de la v3 (`Minerva-v3`); **[NETBOOK]** es bash por SSH
en el netbook. Reemplaza `<usuario>` y `<IP>` por los del netbook.

Todo se hace desde la carpeta de la v3 con su propio entorno de Python (`.venv`). Las únicas referencias a otras
versiones son a propósito: el paso 0 trae por última vez los datos que grababa la v2 en tu PC, y en el netbook se
respalda y se detiene la v1 (`~/minerva`), que es justamente la que la v3 reemplaza.

---

## 0. [PC] Pasar la captura de datos de la v2 a la v3 (cualquier día antes)

Hasta hoy las tareas `MinervaV2*` de tu PC graban el libro de órdenes, el funding y el Fear & Greed en la carpeta
de la v2. La v2 está cerrada (`v2.0`): desde ahora graba la v3, en su propia carpeta `history\`. En PowerShell
como administrador (`Start-Process powershell -Verb RunAs`), apaga las de la v2:

```powershell
Get-ScheduledTask -TaskName "MinervaV2*" | Disable-ScheduledTask
```

En una ventana normal, desde la carpeta de la v3, trae por **última vez** lo que grabó la v2 (con sus tareas ya
apagadas nadie escribe el archivo) y registra las tareas de la v3:

```powershell
Copy-Item ..\Minerva-v2\history\market_v2.db history\market_v3.db -Force
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_v3_data_tasks.ps1
```

```powershell
Get-ScheduledTask -TaskName "MinervaV3*" | Select-Object TaskName, State
```

Tienen que aparecer `MinervaV3OrderBook`, `MinervaV3Funding`, `MinervaV3FearGreed` y `MinervaV3HistoryUpdate`.
En unos minutos, la tarjeta «Datos capturados» de la pestaña Indicadores (http://127.0.0.1:8002) deja de decir
«atrasado».

## A. [PC] Comprobar que la v3 mide lo mismo que la v1

```powershell
git status --short
```

No debe listar nada (la imagen se construye con lo que haya en la carpeta).

```powershell
.\.venv\Scripts\python.exe scripts\verify_v1_identity.py
```

Debe terminar en `OK: la medicion es identica a la v1.0`. Si dice `NO COINCIDE`, **para aquí** y avísame.

## B. [PC] Construir la imagen para el CPU del netbook y empaquetar

```powershell
docker build -t minerva-v3:old-cpu --build-arg REQUIREMENTS_FILE=docker/requirements.old-cpu.lock .
```

```powershell
docker run --rm minerva-v3:old-cpu python scripts/verify_v1_identity.py
```

(Lo mismo que en A, pero dentro de la imagen que va a correr: tiene que decir `OK`.)

```powershell
New-Item -ItemType Directory -Force ..\transfer
```

```powershell
docker save -o ..\transfer\minerva-v3_old-cpu.tar minerva-v3:old-cpu
```

```powershell
git archive --format=tar.gz -o ..\transfer\minerva-v3_repo.tar.gz HEAD
```

## C. [PC] Copiar al netbook

```powershell
scp ..\transfer\minerva-v3_old-cpu.tar <usuario>@<IP>:~/
```

```powershell
scp ..\transfer\minerva-v3_repo.tar.gz <usuario>@<IP>:~/
```

Los datos capturados (libro de órdenes, funding, Fear & Greed) no se pueden volver a bajar: llévalos para que el
netbook siga sumando sobre ellos. El historial de velas también, para no descargarlo de nuevo allá. Los dos están
en la carpeta `history\` de la v3 (paso 0):

```powershell
scp history\market_v3.db <usuario>@<IP>:~/
```

```powershell
scp history\history_v3.db <usuario>@<IP>:~/
```

(Si una tarea `MinervaV3*` está escribiendo justo en ese momento, repite la copia un minuto después.)

## D. [NETBOOK] Desempaquetar, cargar la imagen y probarla en este procesador

```bash
mkdir -p ~/minerva-v3/history && tar -xzf ~/minerva-v3_repo.tar.gz -C ~/minerva-v3
```

```bash
mv ~/market_v3.db ~/history_v3.db ~/minerva-v3/history/
```

```bash
docker load -i ~/minerva-v3_old-cpu.tar
```

```bash
docker tag minerva-v3:old-cpu minerva-v3:local
```

```bash
docker run --rm minerva-v3:local python -c "import numpy, pandas; print('OK numpy', numpy.__version__)"
```

Debe imprimir `OK numpy 2.3.5`. Si sale **Illegal instruction**, para y avísame.

```bash
docker run --rm minerva-v3:local python scripts/verify_v1_identity.py
```

Tiene que decir `OK`.

## E. [NETBOOK] El `.env` de la v3

La v3 **reemplaza** a la v1: usa las mismas claves (Anthropic, Telegram, usuario del dashboard), así que el
`.env` se copia del de la v1 **en el propio netbook** (nunca por chat ni por otro equipo):

```bash
cp ~/minerva/.env ~/minerva-v3/.env
```

Y se le agregan al final los interruptores del contenedor para el netbook (la medición encendida, topes de
recursos y las pruebas en vivo con pocas evaluaciones por segundo):

```bash
cat >> ~/minerva-v3/.env <<'EOF'

# --- contenedor de la v3 en el netbook (lo lee docker compose) ---
MINERVA_REAL_BOT=true
MINERVA_CPUS=1.5
MINERVA_MEM_LIMIT=1g
MINERVA_LIVE_TESTS=true
MINERVA_LIVE_EVALS=2
EOF
```

```bash
sudo chown "$(id -u)":10001 ~/minerva-v3/.env && chmod 640 ~/minerva-v3/.env
```

`MINERVA_REAL_BOT=true` es lo que enciende el etiquetado del LLM, Telegram y el brazo ejecutado, igual que en la
v1. **Sin esa línea la medición no avanza.**

## F. [NETBOOK] Entorno de Python para las tareas del host

Lo mismo que tiene la v1, en la carpeta de la v3 (tarda varios minutos):

```bash
cd ~/minerva-v3 && python3 -m venv .venv
```

```bash
.venv/bin/pip install --only-binary=:all: -r docker/requirements.old-cpu.lock
```

---

## G. [NETBOOK] La mudanza (ventana segura)

### G.1 Respaldo de la v1 y detenerla

```bash
cd ~/minerva && .venv/bin/python scripts/docker_db.py run measurement_status.py
```

Anota los números (APROBADA y RECHAZADA efectivas, filas): después tienen que seguir iguales.

```bash
.venv/bin/python scripts/docker_db.py backup
```

```bash
ls -t ~/minerva/data/backups/docker_*.db | head -1
```

```bash
docker compose stop
```

```bash
docker update --restart=no minerva-minerva-1
```

(Así la v1 no vuelve sola tras un reinicio: nunca dos bots a la vez.) Copia el respaldo más reciente a la v3 y
anota su huella:

```bash
cp "$(ls -t ~/minerva/data/backups/docker_*.db | head -1)" ~/minerva-v3/respaldo_mudanza.db && sha256sum ~/minerva-v3/respaldo_mudanza.db
```

### G.2 Restaurar la base en el volumen de la v3

```bash
cd ~/minerva-v3 && docker compose run --rm --no-deps -v "$PWD/respaldo_mudanza.db:/in/respaldo.db:ro" minerva python scripts/restore_db.py /in/respaldo.db
```

Tiene que terminar con la integridad en verde. Después, el marcador que usan los scripts del host:

```bash
mkdir -p ~/minerva-v3/data && echo "Mudanza v1 -> v3 $(date -Is)" > ~/minerva-v3/data/MIGRADO_A_DOCKER_V3.txt
```

### G.3 Arrancar la v3

```bash
cd ~/minerva-v3 && docker compose up -d --no-build
```

```bash
docker compose ps
```

Espera `healthy` (5 a 15 minutos la primera vez en este procesador). Después:

```bash
curl -s http://127.0.0.1:8002/health | python3 -m json.tool
```

Tienen que aparecer `"status": "ok"`, **`"frozen_hash_ok": true`**, `"paused": false` y, dentro de
`llm_labeling`, **`"enabled": true`**. En Telegram envía `/status`.

```bash
.venv/bin/python scripts/docker_db.py run measurement_status.py
```

Los números tienen que ser **los mismos que anotaste en G.1** (o mayores, si ya llegó una señal nueva).

### G.4 Cambiar las tareas automáticas (cron)

Las tres tareas de la v1 pasan a la carpeta de la v3, y se suman las de captura de datos que hasta hoy corrían
en tu PC:

```bash
crontab -l > ~/crontab_v1_respaldo.txt
```

```bash
(cat <<'EOF'
*/10 * * * * cd $HOME/minerva-v3 && .venv/bin/python scripts/telegram_alert.py > /dev/null 2>> $HOME/cron.log
15 9 * * *   cd $HOME/minerva-v3 && .venv/bin/python scripts/docker_db.py run telegram_daily.py >> $HOME/cron.log 2>&1
30 3 * * *   cd $HOME/minerva-v3 && .venv/bin/python scripts/docker_db.py backup --keep 14 >> $HOME/cron.log 2>&1
*/5 * * * *  cd $HOME/minerva-v3 && .venv/bin/python scripts/record_market_data.py orderbook > /dev/null 2>> $HOME/cron.log
7 * * * *    cd $HOME/minerva-v3 && .venv/bin/python scripts/record_market_data.py funding > /dev/null 2>> $HOME/cron.log
10 2 * * *   cd $HOME/minerva-v3 && .venv/bin/python scripts/record_market_data.py feargreed > /dev/null 2>> $HOME/cron.log
20 */6 * * * cd $HOME/minerva-v3 && .venv/bin/python scripts/update_history_file.py --via-copy >> $HOME/cron.log 2>&1
EOF
) | crontab -
```

(Si tu `crontab -l` tenía otras líneas que no son de Minerva, por ejemplo de Tama, agrégalas de nuevo desde
`~/crontab_v1_respaldo.txt`.) Prueba a mano:

```bash
cd ~/minerva-v3 && .venv/bin/python scripts/check_health.py
```

```bash
.venv/bin/python scripts/telegram_daily.py --dry-run
```

```bash
.venv/bin/python scripts/record_market_data.py orderbook
```

### G.5 Tailscale: el mismo enlace de siempre, ahora a la v3

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8002
```

```bash
tailscale serve status
```

El dashboard queda en la misma dirección de siempre. Revisa que estén las pestañas **Indicadores**, **Estudio**
y **En vivo** (con el bot de pruebas activo).

### G.6 [PC] Quitar las tareas de captura de tu PC

Desde ahora la captura corre en el netbook. En el PC, desde la carpeta de la v3:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_v3_data_tasks.ps1 -Remove
```

```powershell
Get-ScheduledTask -TaskName "MinervaV3*" | Select-Object TaskName, State
```

(No debe listar nada.) Si quieres seguir probando la v3 en el PC después, sus pestañas funcionan igual: el
historial de tu PC simplemente deja de actualizarse.

---

## H. Vigilar los primeros días

- `docker stats --no-stream`: el contenedor no puede pasar de 1,5 núcleos ni de 1 GB.
- En la pestaña **Brazo ejecutado**, que no aparezcan decisiones tardías (`LATE_DECISION`) que antes no había. Si
  aparecen, baja las pruebas en vivo: `MINERVA_LIVE_EVALS=1` en el `.env` y
  `cd ~/minerva-v3 && docker compose up -d --no-build`, o apaga el bot de pruebas desde la pestaña En vivo.
- `df -h`: los ticks grabados ocupan unos 180 MB por día con 10 monedas; se guardan los últimos 14 días
  (unos 2,5 GB) y los más viejos se borran solos. Con poco disco, `MINERVA_TICKS_DAYS=7` en el `.env` (o
  `LIVE_RECORD_TICKS=false` para no grabar) y `docker compose up -d --no-build`.
- **Indicadores en el netbook:** una prueba de 10 monedas en 15m tarda unos 2 minutos en el PC y bastante más en
  el netbook, y usa el mismo procesador que la medición. Ahí conviene usar 1h o 4h, o pocas monedas.
- `~/cron.log` debería quedar vacío o casi.

## I. Si algo sale mal: volver a la v1

**Dentro de la misma ventana** (la v3 casi no escribió nada):

```bash
cd ~/minerva-v3 && docker compose stop
```

```bash
docker update --restart=no minerva-v3-minerva-1
```

```bash
cd ~/minerva && docker update --restart=unless-stopped minerva-minerva-1 && docker compose up -d --no-build
```

```bash
crontab ~/crontab_v1_respaldo.txt && sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
```

La base de la v1 quedó intacta en su volumen; se pierden solo los minutos de la prueba.

**Si la v3 ya corrió horas o días:** no arranques la v1 con su base vieja (perderías lo que midió la v3).
Primero hay que llevar la base más reciente de la v3 al volumen de la v1. Avísame y lo hacemos con cuidado.

## J. Después de la mudanza

- **Nunca** `docker compose up` en `~/minerva` (la v1): serían dos bots.
- El volumen `minerva_data` y la imagen `minerva:local` de la v1 se conservan como respaldo hasta el veredicto.
  No los borres.
- **Veredicto de enero:** el procedimiento es el mismo, ahora desde la v3:
  `cd ~/minerva-v3 && .venv/bin/python scripts/docker_db.py export --out-dir data/backups --prefix veredicto`,
  y el registro oficial `docs/VEREDICTO_3_6_REGISTRO_*.txt` se copia a `~/minerva-v3/docs/`.
- **Actualizar la v3 más adelante:** repetir A, B y C (sin los archivos de `history\`), y en el netbook
  `tar -xzf ~/minerva-v3_repo.tar.gz -C ~/minerva-v3`, `docker load`, `docker tag` y
  `docker compose up -d --no-build`. Antes de cada actualización, `verify_v1_identity.py` tiene que decir `OK`
  mientras la medición de la v1 siga abierta.
