"""La v3 NO puede tocar la v2, que sigue midiendo en el mismo PC.

La v2 corre su prueba hacia adelante y sus tareas de captura con estos nombres: proyecto
`minerva-v2`, imagen `minerva-v2:local`, volumenes `minerva-v2_data` y `minerva-v2_logs`, puerto
8001, tareas programadas con prefijo `MinervaV2` y la marca `MIGRADO_A_DOCKER_V2.txt`. Aqui se
comprueba que ningun valor por defecto de la v3 coincide con alguno y que los scripts que podrian
actuar sobre ellos los rechazan. Igual que `test_v1_isolation.py`: estatico o con scripts que se
detienen antes de hacer nada.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from scripts import docker_support
from tests.unit.test_v1_isolation import COMPOSE, SCRIPTS, powershell

V2_NAMES = (
    "minerva-v2_data", "minerva-v2_logs", "minerva-v2_default", "minerva-v2:local",
    "MIGRADO_A_DOCKER_V2.txt", "history_v2.db", "market_v2.db",
)
V2_TASKS = (
    "MinervaV2StartBot", "MinervaV2HealthAlert", "MinervaV2DailySummary", "MinervaV2Backup",
    "MinervaV2OrderBook", "MinervaV2Funding", "MinervaV2FearGreed", "MinervaV2HistoryUpdate",
)


def _code_without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_no_v2_name_survives_in_the_compose_once_comments_are_dropped():
    code = _code_without_comments(COMPOSE.read_text(encoding="utf-8"))

    for name in (*V2_NAMES, "minerva-v2", "8001"):
        assert name not in code, name


@pytest.mark.parametrize("script", ["record_market_data.py", "update_history_file.py"])
def test_the_capture_scripts_write_to_the_v3_files(script):
    code = _code_without_comments((SCRIPTS / script).read_text(encoding="utf-8"))

    assert "history_v2.db" not in code and "market_v2.db" not in code


def test_the_v3_data_tasks_use_the_v3_prefix():
    code = (SCRIPTS / "register_v3_data_tasks.ps1").read_text(encoding="utf-8")

    for task in V2_TASKS:
        assert f'"{task}"' not in code
    assert "MinervaV3OrderBook" in code


def test_compose_refuses_the_project_of_the_v2(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_docker(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(docker_support, "run_docker", fake_run_docker)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "minerva-v2")

    with pytest.raises(docker_support.DockerError, match="RECHAZADO"):
        docker_support.compose(["ps"])
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
@pytest.mark.parametrize("script", ["start_bot.ps1", "stop_bot.ps1"])
def test_start_and_stop_refuse_the_port_of_the_v2_before_doing_anything(script):
    result = powershell(script, "-Port", "8001")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "RECHAZADO" in result.stdout and "8001" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
@pytest.mark.parametrize("script", ["migrate_to_docker.ps1", "rollback_from_docker.ps1"])
@pytest.mark.parametrize("bad_args", [
    ["-ProjectName", "minerva-v2"], ["-NativePort", "8001"], ["-DockerPort", "8001"],
])
def test_the_migration_scripts_refuse_the_project_and_ports_of_the_v2(script, bad_args):
    result = powershell(script, "-DryRun", *bad_args)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "RECHAZADO" in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell")
@pytest.mark.parametrize("task", ["MinervaV2StartBot", "MinervaV2OrderBook"])
def test_migrate_refuses_a_scheduled_task_of_the_v2(task):
    result = powershell("migrate_to_docker.ps1", "-DryRun", "-TaskName", task)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "RECHAZADO" in result.stdout + result.stderr
