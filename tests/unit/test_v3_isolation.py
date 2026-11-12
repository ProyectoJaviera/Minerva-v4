"""La v4 NO puede tocar la v3, que corre en el netbook y puede correr tambien en este PC.

La v3 usa estos nombres: proyecto `minerva-v3`, imagen `minerva-v3:local`, volumenes
`minerva-v3_data` y `minerva-v3_logs`, puerto 8002, tareas programadas con prefijo `MinervaV3`, la
marca `MIGRADO_A_DOCKER_V3.txt` y los archivos `history_v3.db`, `market_v3.db`, `live_v3.db`,
`ticks_v3.db` y `minute_v3.db`. Aqui se comprueba que ningun valor por defecto de la v4 coincide con
alguno y que los scripts que podrian actuar sobre ellos los rechazan (igual que
`test_v2_isolation.py`: estatico o con scripts que se detienen antes de hacer nada).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from scripts import docker_support
from scripts.download_minutes import refuse_foreign_db
from tests.unit.test_v1_isolation import COMPOSE, SCRIPTS, powershell

V3_NAMES = (
    "minerva-v3_data", "minerva-v3_logs", "minerva-v3_default", "minerva-v3:local",
    "MIGRADO_A_DOCKER_V3.txt", "history_v3.db", "market_v3.db", "live_v3.db", "ticks_v3.db",
)


def _code_without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_no_v3_name_survives_in_the_compose_once_comments_are_dropped():
    code = _code_without_comments(COMPOSE.read_text(encoding="utf-8"))

    for name in (*V3_NAMES, "minerva-v3", "8002"):
        assert name not in code, name


def test_the_default_files_are_the_v4_ones():
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    for path in (s.explorer_history_db, s.market_data_db, s.live_tests_db, s.live_ticks_db):
        assert path.endswith("_v4.db"), path


def test_the_v4_data_tasks_use_the_v4_prefix():
    code = (SCRIPTS / "register_v4_data_tasks.ps1").read_text(encoding="utf-8")

    assert '"MinervaV3' not in code and "MinervaV4OrderBook" in code


@pytest.mark.parametrize("path", [
    "D:/x/Minerva-v3/history/live_v4.db", "D:/x/Minerva-v4/history/live_v3.db",
    "D:/x/Minerva-v4/history/minute_v3.db",
])
def test_the_live_and_minute_commands_refuse_files_of_the_v3(path):
    assert refuse_foreign_db(Path(path)) is not None
    assert refuse_foreign_db(Path("D:/x/Minerva-v4/history/live_v4.db")) is None


def test_compose_refuses_the_project_of_the_v3(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_docker(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(docker_support, "run_docker", fake_run_docker)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "minerva-v3")

    with pytest.raises(docker_support.DockerError, match="RECHAZADO"):
        docker_support.compose(["ps"])
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
@pytest.mark.parametrize("script", ["start_bot.ps1", "stop_bot.ps1"])
def test_start_and_stop_refuse_the_port_of_the_v3_before_doing_anything(script):
    result = powershell(script, "-Port", "8002")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "RECHAZADO" in result.stdout and "8002" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
@pytest.mark.parametrize("script", ["migrate_to_docker.ps1", "rollback_from_docker.ps1"])
@pytest.mark.parametrize("bad_args", [
    ["-ProjectName", "minerva-v3"], ["-NativePort", "8002"], ["-DockerPort", "8002"],
])
def test_the_migration_scripts_refuse_the_project_and_ports_of_the_v3(script, bad_args):
    result = powershell(script, "-DryRun", *bad_args)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "RECHAZADO" in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
@pytest.mark.parametrize("task", ["MinervaV3StartBot", "MinervaV3OrderBook"])
def test_migrate_refuses_a_scheduled_task_of_the_v3(task):
    result = powershell("migrate_to_docker.ps1", "-DryRun", "-TaskName", task)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "RECHAZADO" in result.stdout + result.stderr
