import re
from pathlib import Path

import pytest

import zelador
from zelador import cli


def test_a_versao_do_codigo_e_a_do_pyproject():
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8")
    declarada = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)

    assert zelador.__version__ == declarada


def test_version_mostra_o_numero_e_sai_com_zero(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["--version"])

    assert saida.value.code == 0
    assert capsys.readouterr().out.strip() == f"zelador {zelador.__version__}"


def test_version_nao_e_confundida_com_opcao_do_limpar(capsys):
    """Sem subcomando, opções viram `limpar ...`; --version tem que escapar dessa regra."""
    with pytest.raises(SystemExit) as saida:
        cli.main(["--version"])

    assert saida.value.code == 0
