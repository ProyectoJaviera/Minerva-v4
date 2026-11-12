# Minerva v4: bot de trading autónomo (PAPER TRADING)

## Qué es este repositorio
- Esta es la **versión 4** de Minerva. Parte del commit `5ee53bf` de **Minerva-v3** (el mismo código y el mismo historial de git); desde ahí sigue su propio camino.
- **La v3 sigue corriendo** en el netbook (con la medición de la v1 dentro) y en su propio repositorio (Minerva-v3). Esta v4 **no la toca ni la reemplaza**: puede convivir con ella en el PC porque todos sus nombres son otros.
- **La v1 sigue congelada** midiendo el filtro del LLM (su identidad se verifica con `scripts/verify_v1_identity.py`; no la rompas). La v2 tampoco se toca.
- Las correcciones de la v3 que importen aquí se traen con git (remoto `v3`, `git cherry-pick` o `merge`) y se anotan en docs/historial/PROGRESS.md.

## Pregunta de la v4
¿Cada prueba «En vivo» le gana **al azar**? Cada prueba tiene un **gemelo de control** que opera las mismas monedas, marco, margen, apalancamiento, dirección y salida, pero con entradas sorteadas a la misma frecuencia. El dashboard muestra si la prueba se distingue de su gemelo. Solo hacia adelante, sin dinero real y **sin ajustar las estrategias**. Detalle en docs/v4/CONTROL_AL_AZAR.md.

## Pregunta heredada de la v3
¿Una **cartera de fuerza relativa entre monedas** (varias monedas a la vez, peso parejo, rebalanceo periódico) tiene ventaja después de costos, sin depender de unas pocas operaciones? Y, como infraestructura, **datos en tiempo real de Bitunix** (WebSocket público) para entrar apenas se cumplan las condiciones, sin esperar el cierre de la vela. Plan en docs/v3/PLAN_V3.md.

## Contexto
Especificación de la v1 en docs/historial/SPEC.md. Resultados de la v2 en docs/v2/ (las cuatro rondas, la exploración y el examen con datos reservados). El índice de la documentación vigente está en docs/README.md; la técnica, en docs/dev/. Donde algo difiera, manda el código.

## Idioma
Todas las respuestas, resúmenes y documentos para este proyecto deben estar en español. Los identificadores de código (variables, funciones, clases, tablas, columnas) siguen en inglés.

## Reglas críticas
- La v3 es 100% paper trading. Prohibido escribir código que envíe órdenes reales a Bitunix. La ejecución real solo existe como interfaz/documentación (BitunixBackend desactivado). El WebSocket que se usa es solo el **público** de datos de mercado (`wss://fapi.bitunix.com/public/`); nunca el privado.
- Nunca leas, imprimas ni pidas archivos .env ni credenciales. Usa solo .env.example con valores vacíos. Nunca incluyas claves en código, tests, logs ni commits.
- **Jamás copies el `.env` de la v1, la v2 ni la v3 a la v4**: la v4 tiene su propio `.env`, que crea y llena su dueño a mano.
- No inventes endpoints, parámetros ni firmas de Bitunix/CoinGecko. Verifica con la documentación oficial o los ejemplos del repo oficial antes de implementar; si no puedes verificar, dilo.
- Los datos reservados de la v2 (2026-10-01 a 2026-11-10) **ya se usaron**: el examen final de cualquier idea tiene que hacerse con datos posteriores a su preregistro (prueba hacia adelante).

## Aislamiento de la v1, la v2 y la v3
- **Nunca** trabajes en la carpeta de la v1, en la de la v2, en la de la v3 ni en el netbook. Nunca ejecutes `docker compose up` en ellas. Lo que haya que hacer allí lo ejecuta su dueño o el hilo de esa versión.
- Los nombres de la v4 son otros a propósito: proyecto y contenedor `minerva-v4`, imagen `minerva-v4:local`, volúmenes `minerva-v4_data` y `minerva-v4_logs`, puerto `8003`, tareas programadas con prefijo `MinervaV4`, marca `MIGRADO_A_DOCKER_V4.txt` y archivos `history/history_v4.db`, `history/market_v4.db`, `history/live_v4.db`, `history/ticks_v4.db` e `history/minute_v4.db`.
- Los scripts rechazan los nombres de la v1 (proyecto `minerva`, puerto 8000, tareas `MinervaStartBot`, `MinervaHealthAlert`, `MinervaDailySummary`, `MinervaBackup`), los de la v2 (proyecto `minerva-v2`, puerto 8001, tareas `MinervaV2*`) y los de la v3 (proyecto `minerva-v3`, puerto 8002, tareas `MinervaV3*`, archivos `*_v3.db`). No los debilites ni los esquives.
- `MINERVA_REAL_BOT` vale `false` por defecto: la v4 arranca sin etiquetado del LLM, Telegram, brazo ejecutado, noticias ni push. Encenderlos es una decisión explícita de su dueño, con un `.env` propio de la v4.
- Todo desarrollo y prueba usa una **DB temporal** y el **puerto 8003** (el servidor de desarrollo y el contenedor de la v4 comparten ese puerto por defecto: no corras los dos a la vez). Nunca apuntes nada de la v4 a la base, al historial ni a los volúmenes de la v1, la v2 o la v3.

## Flujo de trabajo
- Antes de un cambio grande: investiga y propón un plan. Para los pequeños, decide con criterio y sigue.
- Haz commits pequeños con mensajes claros (Conventional Commits).
- Pruebas: pytest + pytest-asyncio. Toda función de riesgo y de simulación (SL/TP/trailing, liquidación, comisiones, funding, rebalanceo de la cartera, entradas en tiempo real) debe tener tests con casos límite.
- Antes de dar por terminada cualquier tarea que toque el pipeline de datos o la persistencia, ejecuta un smoke test de punta a punta con un subconjunto pequeño; que los tests unitarios pasen no es suficiente.
- Todo script que lea una base real debe ejecutarse antes de entregarlo contra una copia temporal de esa base (ruta distinta de `settings.database_path`, verificada con un assert explícito); los tests con datos sintéticos no bastan para esto.
- Preregistro: ninguna corrida que decida si una estrategia de la v3 pasa se hace sin un preregistro aprobado por el dueño y congelado en un commit propio. Tras ver resultados fuera de muestra no se cambia nada.
- Cualquier cambio de comportamiento respecto de la v3 (estrategias, niveles SL/TP, riesgo, esquema de la base, configuración por defecto, entradas dentro de la vela, el gemelo al azar) **debe quedar anotado en docs/historial/PROGRESS.md**, con el motivo.

## Autonomía
- Trabaja de corrido hasta terminar la tarea completa. No pidas confirmación ni hagas preguntas intermedias: decide con criterio, anota la decisión en docs/historial/PROGRESS.md y sigue. Solo te detienes al terminar, o si algo exige una decisión irreversible del dueño del proyecto.
- Los scripts de prueba temporales van en el directorio scratchpad y se reutilizan; no crees un archivo de prueba nuevo por intento.
- Límites absolutos aunque ningún permiso los frene: nunca arrancar, detener, matar, reiniciar ni reconstruir nada de la v1 (puerto 8000, proyecto `minerva`), de la v2 (puerto 8001, proyecto `minerva-v2`, tareas `MinervaV2*`) ni de la v3 (puerto 8002, proyecto `minerva-v3`, tareas `MinervaV3*`); nunca tocar sus bases, volúmenes, historiales ni tareas programadas; nunca modificar, mover ni borrar archivos en `data/` ni en `history/` de otro repositorio; nunca leer ni imprimir `.env`.
- Push: al terminar cada tarea, con pytest, ruff y `scripts/check_measurement_safety.py` en verde, haz `git push origin master` **de este repositorio (Minerva-v4)**; nunca `--force`, nunca otra rama, y nunca subas `.env`, `data/`, `history/` ni `logs/`.

## Stack y convenciones
- Python 3.11+, asyncio, FastAPI, SQLite (aiosqlite). Tipado con type hints; formateo con ruff.
- Toda decisión del LLM se valida con esquema (pydantic) y se registra completa en la base de datos.
- El bot corre en Docker Compose en el PC de su dueño, no dentro de Claude Code. Puedes levantarlo para probar solo si el dueño lo pide, siempre en modo paper y con el proyecto `minerva-v4`.

## Comandos
- Tests: pytest
- Lint: ruff check .
- Levantar (lo hace su dueño): docker compose up --build

## Restricción de costos (obligatoria)
- Lo único que puede costar dinero es la API de Claude (Anthropic), con presupuesto diario y corte duro configurables.
- Todo lo demás debe ser gratuito y sin tarjeta de crédito: APIs, librerías, datos, hosting (PC local) y herramientas.
- Antes de integrar cualquier servicio externo, verifica en su documentación oficial: plan gratuito real, límites de uso, términos (uso no comercial, atribución) y si pide tarjeta. Si no cumple, propón una alternativa gratuita o descártalo.
- Diseña para fuentes gratuitas poco fiables: caché local, reintentos con backoff, degradación controlada, registro de salud por fuente y bloqueo de nuevas operaciones si los datos críticos están obsoletos.
- Respeta límites de uso y atribuciones (p. ej. "Data provided by CoinGecko" visible en el frontend). El WebSocket público de Bitunix acepta como máximo 5 mensajes por segundo y 300 suscripciones por conexión.

## Formato de comandos (para evitar solicitudes de permiso)
- El directorio de trabajo ya es la raíz del proyecto: NO uses `cd`.
- Un solo comando por llamada. No encadenes con `&&`, `;` ni `&`.
- Para cambiar de carpeta dentro de un comando usa las opciones propias de la herramienta (p. ej. `npm --prefix frontend run build`, `git -C ...`), no `cd`.
- Mensajes de commit de una sola línea con `git commit -m "tipo: descripción"`, sin saltos de línea ni trailers.
- No redirijas salidas a archivos temporales (`> build.log`, `> out.txt`) ni borres archivos después; lee la salida directamente.
