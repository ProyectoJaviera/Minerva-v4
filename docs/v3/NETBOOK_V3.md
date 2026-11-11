# Minerva v3 en el netbook: instalación de las pruebas en vivo

**Quién lo hace: el dueño, a mano.** Ningún asistente trabaja en el netbook mientras la v1 mide ahí.
Esta guía deja la v3 corriendo **al lado** de la v1, sin tocarla: otro proyecto de Docker, otro
puerto, otros volúmenes, otro `.env` y topes de CPU y memoria.

## 0. Lo que queda separado de la v1

| | v1 (no se toca) | v3 |
|---|---|---|
| Carpeta | `~/minerva` | `~/minerva-v3` |
| Proyecto y contenedor de Docker | `minerva`, `minerva-minerva-1` | `minerva-v3`, `minerva-v3-minerva-1` |
| Imagen | `minerva:local` | `minerva-v3:local` |
| Volúmenes | `minerva_data`, `minerva_logs` | `minerva-v3_data`, `minerva-v3_logs` |
| Puerto (solo `127.0.0.1`) | 8000 | 8002 |
| `.env` | el de la v1 | uno **nuevo**, creado a mano (nunca una copia del de la v1) |
| CPU y memoria | sin cambios | tope de 0,5 núcleos y 600 MB (`MINERVA_CPUS`, `MINERVA_MEM_LIMIT`) |

La v3 arranca con `MINERVA_REAL_BOT=false` (sin LLM, Telegram ni brazo ejecutado) y con las pruebas
en vivo encendidas. Solo usa datos públicos de Bitunix; nunca envía órdenes.

**Cuándo hacerlo:** en la ventana segura de la v1 (minutos :10 a :40, evitando las 01, 05, 09, 13,
17 y 21 h), igual que cualquier otra cosa en el netbook.

## 1. En el PC (PowerShell): construir la imagen para el CPU viejo

El netbook no tiene SSE4: la imagen se construye en el PC con el lock de NumPy compatible, igual que
la de la v1. Desde la carpeta de la v3, con el árbol limpio (`git status` sin cambios):

```powershell
docker build -t minerva-v3:old-cpu --build-arg REQUIREMENTS_FILE=docker/requirements.old-cpu.lock .
```

```powershell
docker run --rm minerva-v3:old-cpu python -c "import numpy; print(numpy.__version__)"
```

Debe imprimir `2.3.5`. Después se empaquetan la imagen y el código (sin `.env`, `data/`, `history/`
ni `logs/`):

```powershell
docker save -o ..\transfer\minerva-v3_old-cpu.tar minerva-v3:old-cpu
```

```powershell
git archive --format=tar.gz -o ..\transfer\minerva-v3_repo.tar.gz HEAD
```

Y se copian al netbook (reemplaza `<usuario>` y `<IP>` por los del netbook):

```powershell
scp ..\transfer\minerva-v3_old-cpu.tar <usuario>@<IP>:~/
```

```powershell
scp ..\transfer\minerva-v3_repo.tar.gz <usuario>@<IP>:~/
```

Cuando el repositorio público de la v3 exista en GitHub, el código también se puede bajar en el
netbook con `git clone`; la imagen igual se construye en el PC.

## 2. En el netbook (bash): instalar

```bash
mkdir -p ~/minerva-v3 && tar -xzf ~/minerva-v3_repo.tar.gz -C ~/minerva-v3
```

```bash
docker load -i ~/minerva-v3_old-cpu.tar
```

```bash
docker tag minerva-v3:old-cpu minerva-v3:local
```

### El `.env` propio de la v3

Se crea **a mano** a partir de `.env.example`. Para el dashboard hacen falta el usuario, el hash de
la contraseña y un `JWT_SECRET` largo; el resto puede quedar vacío (sin claves de Anthropic ni de
Telegram: la v3 no las usa). **Nunca copies el `.env` de la v1.**

```bash
cp ~/minerva-v3/.env.example ~/minerva-v3/.env
```

```bash
nano ~/minerva-v3/.env
```

El contenedor corre como el usuario 10001 y tiene que poder leerlo:

```bash
sudo chown "$(id -u)":10001 ~/minerva-v3/.env && chmod 640 ~/minerva-v3/.env
```

### Arrancar

Siempre desde `~/minerva-v3` y con `--no-build` (la imagen ya está cargada):

```bash
cd ~/minerva-v3 && docker compose up -d --no-build
```

```bash
docker compose ps
```

El contenedor `minerva-v3-minerva-1` tiene que quedar `healthy` (tarda hasta 5 minutos). La v1 debe
seguir igual:

```bash
docker ps --format '{{.Names}}  {{.Status}}'
```

## 3. Usarlo

- **Dashboard:** `http://127.0.0.1:8002` en el propio netbook. Para verlo desde el celular por
  Tailscale, en otro puerto HTTPS que no sea el de la v1 (443) ni el reservado para Tama (8443):
  `sudo tailscale serve --bg --https=8444 http://127.0.0.1:8002`. Nunca `tailscale funnel`.
- **Pestaña «En vivo»:** crear una prueba (monedas, marco, indicadores, salida y modo), ver el
  estado del corredor, los resultados y el detalle, y detenerla.
- **Sin el dashboard,** desde el netbook:

```bash
docker compose exec minerva python scripts/live_tests.py --db /app/data/live_v3.db report
```

## 4. Cuidar a la v1

- Mirar el consumo de los dos contenedores de vez en cuando:

```bash
docker stats --no-stream
```

  La v3 no puede pasar de 0,5 núcleos ni de 600 MB. Si el netbook se siente lento o la v1 muestra
  decisiones tardías, bajar las evaluaciones por segundo o usar menos pruebas `intrabar` con
  intervalos más largos. Las evaluaciones se bajan al arrancar:
  `cd ~/minerva-v3 && MINERVA_LIVE_EVALS=2 docker compose up -d --no-build`.
- **Disco:** la grabación de ticks ocupa del orden de decenas de MB por día. Revisar con
  `df -h` cada tanto; si falta espacio, `LIVE_RECORD_TICKS=false` en el `.env` de la v3 y reiniciar
  solo la v3.
- **Detener solo la v3** (nunca en la carpeta de la v1):

```bash
cd ~/minerva-v3 && docker compose stop
```

- `restart: unless-stopped`: la v3 vuelve sola tras un reinicio del netbook, igual que la v1.

## 5. Actualizar la v3 más adelante

Repetir el punto 1 en el PC (imagen y código nuevos), copiar, y en el netbook:

```bash
docker load -i ~/minerva-v3_old-cpu.tar && docker tag minerva-v3:old-cpu minerva-v3:local
```

```bash
cd ~/minerva-v3 && docker compose up -d --no-build
```

Las pruebas, sus resultados y los ticks grabados viven en el volumen `minerva-v3_data` y se
conservan entre actualizaciones.
