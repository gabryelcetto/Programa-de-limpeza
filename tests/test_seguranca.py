import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from zelador import seguranca
from zelador.seguranca import motivo_pasta_protegida, pasta_do_sistema_que_contem


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Um 'sistema' falso dentro do tmp_path: nada depende da máquina em que o teste roda."""
    windows = tmp_path / "Windows"
    arquivos_de_programas = tmp_path / "Program Files"
    home = tmp_path / "Users" / "ana"
    for pasta in (windows / "System32", arquivos_de_programas / "App", home / "Downloads"):
        pasta.mkdir(parents=True)

    for variavel in seguranca._VARIAVEIS_DO_WINDOWS:
        monkeypatch.delenv(variavel, raising=False)
    monkeypatch.setenv("SystemRoot", str(windows))
    monkeypatch.setenv("ProgramFiles", str(arquivos_de_programas))
    monkeypatch.setattr(Path, "home", lambda: home)
    return SimpleNamespace(raiz=tmp_path, windows=windows, programas=arquivos_de_programas,
                           home=home)


def test_raiz_do_disco_e_protegida(ambiente):
    ancora = Path(os.path.realpath(ambiente.raiz)).anchor

    assert motivo_pasta_protegida(ancora) == "a raiz de um disco"


def test_pasta_pessoal_e_a_que_contem_os_usuarios_sao_protegidas(ambiente):
    assert "pessoal" in motivo_pasta_protegida(ambiente.home)
    assert "contém os usuários" in motivo_pasta_protegida(ambiente.home.parent)


def test_subpastas_da_pasta_pessoal_nao_sao_protegidas(ambiente):
    assert motivo_pasta_protegida(ambiente.home / "Downloads") is None
    assert motivo_pasta_protegida(ambiente.home / "Documentos" / "Projeto") is None


def test_pasta_do_sistema_e_tudo_dentro_dela_sao_protegidos(ambiente):
    for caminho in (ambiente.windows, ambiente.windows / "System32",
                    ambiente.windows / "nao" / "existe" / "ainda",
                    ambiente.programas, ambiente.programas / "App"):
        assert "pasta do sistema" in motivo_pasta_protegida(caminho), caminho


def test_nome_parecido_com_pasta_do_sistema_nao_e_protegido(ambiente):
    parecida = ambiente.raiz / "Windows2"
    parecida.mkdir()

    assert motivo_pasta_protegida(parecida) is None
    assert pasta_do_sistema_que_contem(parecida) is None


def test_pasta_comum_nao_e_protegida(ambiente, tmp_path):
    comum = tmp_path / "meus_arquivos"
    comum.mkdir()

    assert motivo_pasta_protegida(comum) is None


def test_caminho_relativo_e_com_barras_extras_e_normalizado(ambiente, monkeypatch):
    monkeypatch.chdir(ambiente.raiz)

    assert motivo_pasta_protegida("Windows") is not None
    assert motivo_pasta_protegida("./Windows/../Windows/System32") is not None
    assert motivo_pasta_protegida("Users/ana/Downloads") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows não diferencia maiúsculas")
def test_maiusculas_nao_enganam_no_windows(ambiente):
    assert motivo_pasta_protegida(str(ambiente.windows).upper()) is not None
    assert motivo_pasta_protegida(str(ambiente.windows / "SYSTEM32").lower()) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="junctions só existem no Windows")
def test_junction_para_pasta_do_sistema_nao_engana(ambiente):
    atalho = ambiente.home / "Downloads" / "atalho_inocente"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(atalho), str(ambiente.windows)],
                   check=True, capture_output=True)

    assert "pasta do sistema" in motivo_pasta_protegida(atalho)


def test_symlink_para_pasta_do_sistema_nao_engana(ambiente):
    atalho = ambiente.home / "Downloads" / "atalho_inocente"
    try:
        atalho.symlink_to(ambiente.windows, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar symlinks neste ambiente")

    assert "pasta do sistema" in motivo_pasta_protegida(atalho)


def test_sem_variaveis_do_windows_so_a_raiz_e_a_pasta_pessoal_valem(ambiente, monkeypatch):
    for variavel in seguranca._VARIAVEIS_DO_WINDOWS:
        monkeypatch.delenv(variavel, raising=False)
    monkeypatch.setattr(seguranca, "_PASTAS_DO_SISTEMA_POSIX", ())

    assert motivo_pasta_protegida(ambiente.windows) is None
    assert motivo_pasta_protegida(ambiente.home) is not None


@pytest.mark.skipif(sys.platform == "win32", reason="lista de pastas do Linux/macOS")
def test_pastas_do_sistema_do_linux(ambiente):
    for caminho in ("/etc", "/usr/bin", "/bin"):
        assert "pasta do sistema" in motivo_pasta_protegida(caminho), caminho
