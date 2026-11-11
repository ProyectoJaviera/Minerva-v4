"""El chequeo de identidad con la v1.0 pasa en esta copia y detecta cambios en la medicion."""

from __future__ import annotations

import shutil

from scripts import verify_v1_identity as v


def test_this_copy_measures_exactly_like_v1():
    assert v.check() == []
    assert v.main([]) == 0


def copy_app(tmp_path):
    shutil.copytree(v.REPO_ROOT / "app", tmp_path / "app",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_an_edited_core_file_is_detected(tmp_path):
    root = copy_app(tmp_path)
    target = root / "app" / "trading" / "risk_engine.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# cambio\n", encoding="utf-8")

    assert v.check(root) == [
        "cambio app/trading/risk_engine.py (tiene que ser identico al de la v1.0)"]


def test_crlf_line_endings_do_not_count_as_a_change(tmp_path):
    root = copy_app(tmp_path)
    target = root / "app" / "llm" / "prompts.py"
    target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    assert v.check(root) == []


def test_extended_files_may_only_gain_lines(tmp_path):
    root = copy_app(tmp_path)
    config = root / "app" / "config.py"
    text = config.read_text(encoding="utf-8")
    config.write_text(text + "\n# linea nueva\n", encoding="utf-8")
    assert v.check(root) == []

    config.write_text(text.replace("LEVERAGE", "APALANCAMIENTO", 1), encoding="utf-8")
    assert v.check(root) == [
        "cambio app/config.py (solo se le pueden agregar lineas, no quitar ni editar)"]


def test_a_missing_file_and_a_different_prompt_are_detected(tmp_path):
    root = copy_app(tmp_path)
    (root / "app" / "strategies" / "ema_cross.py").unlink()
    labeler = root / "app" / "trading" / "shadow_live_labeler.py"
    text = labeler.read_text(encoding="utf-8")
    labeler.write_text(text.replace("0fa0dd75", "1fa0dd75", 1), encoding="utf-8")

    problems = v.check(root)
    assert "falta app/strategies/ema_cross.py" in problems
    assert "la huella del prompt congelado no es la de la v1" in problems
