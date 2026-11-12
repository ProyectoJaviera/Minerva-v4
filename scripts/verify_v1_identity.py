"""Comprueba que esta copia de Minerva mide exactamente lo mismo que la v1.0.

Antes de reemplazar a la v1 en el servidor (docs/v3/MUDANZA_NETBOOK_V3.md) hay que demostrar que
la medicion no cambia. Este script compara el codigo de `app/` con las huellas de la etiqueta v1.0
guardadas en `scripts/v1_measurement_manifest.json`:

- **Identicos:** los 141 archivos del nucleo (LLM y prompt congelado, estrategias, riesgo,
  scheduler, universo, base, ejecucion paper...) tienen que ser byte a byte los de la v1.0
  (ignorando solo el fin de linea).
- **Ampliados:** `app/config.py` y `app/main.py` solo pueden tener lineas AGREGADAS: todas las de
  la v1.0 siguen ahi, en el mismo orden.
- **Fuera de la medicion:** el motor de backtest (`app/backtesting/engine.py` y `report.py`) puede
  diferir; su prueba de regresion confirma que las estrategias de la v1 dan lo mismo.
- Y la constante del prompt congelado tiene que ser la de la v1.

Uso (en el PC o dentro de la imagen, en el netbook):

    python scripts/verify_v1_identity.py
    docker run --rm minerva-v4:local python scripts/verify_v1_identity.py

Sale con 0 si todo coincide y con 1 si algo difiere (y dice que). No usa red ni base.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().parent / "v1_measurement_manifest.json"
PROMPT_CONSTANT = re.compile(r'^FROZEN_PROMPT_SHA256\s*=\s*"([0-9a-f]{64})"', re.MULTILINE)


def _norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needles)


def check(root: Path = REPO_ROOT, manifest_path: Path = MANIFEST) -> list[str]:
    """Las diferencias entre el codigo de `root` y la v1.0.

    Args:
        root: raiz de la copia a revisar (la que contiene `app/`).
        manifest_path: el archivo de huellas.

    Returns:
        Una linea por problema; vacia si la medicion es identica a la de la v1.0.

    Efectos:
        Lee los archivos de `app/` y el manifiesto.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    for rel, digest in sorted(manifest["identical"].items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"falta {rel}")
            continue
        if hashlib.sha256(_norm(path.read_bytes())).hexdigest() != digest:
            problems.append(f"cambio {rel} (tiene que ser identico al de la v1.0)")
    for rel, line_hashes in sorted(manifest["extended_line_hashes"].items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"falta {rel}")
            continue
        lines = _norm(path.read_bytes()).split(b"\n")
        now = [hashlib.sha256(line).hexdigest()[:16] for line in lines]
        if not _is_subsequence(line_hashes, now):
            problems.append(
                f"cambio {rel} (solo se le pueden agregar lineas, no quitar ni editar)")
    labeler = root / "app" / "trading" / "shadow_live_labeler.py"
    text = labeler.read_text(encoding="utf-8") if labeler.is_file() else ""
    found = PROMPT_CONSTANT.search(text)
    if found is None or found.group(1) != manifest["frozen_prompt_sha256"]:
        problems.append("la huella del prompt congelado no es la de la v1")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Imprime el resultado de `check` y devuelve el codigo de salida.

    Args:
        argv: argumentos (no se usan; existe para las pruebas).

    Returns:
        0 si la medicion es identica a la v1.0, 1 si no.

    Efectos:
        Lee los archivos de `app/` y el manifiesto; imprime el resultado.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = check()
    n_ident = len(manifest["identical"])
    n_ext = len(manifest["extended_line_hashes"])
    if problems:
        print(f"NO COINCIDE con la v1.0 ({manifest['v1_commit'][:7]}): {len(problems)} problemas")
        for p in problems:
            print(f"  - {p}")
        print("No reemplaces la v1 con esta copia.")
        return 1
    commit = manifest["v1_commit"][:7]
    print(f"OK: la medicion es identica a la v1.0 ({commit}): {n_ident} archivos "
          f"identicos, {n_ext} ampliados solo con lineas nuevas y el mismo prompt congelado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
