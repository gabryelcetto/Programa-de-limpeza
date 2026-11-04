import sys
import uuid
from pathlib import Path

import pytest

from zelador import caminhos


def test_fora_do_windows_usa_downloads_da_pasta_pessoal(tmp_path, monkeypatch):
    monkeypatch.setattr(caminhos.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert caminhos.obter_pasta_downloads() == tmp_path / "Downloads"


@pytest.mark.skipif(sys.platform != "win32", reason="Known Folders só existem no Windows")
def test_no_windows_devolve_uma_pasta_que_existe():
    pasta = caminhos.obter_pasta_downloads()

    assert pasta.is_absolute()
    assert pasta.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Known Folders só existem no Windows")
def test_no_windows_cai_no_caminho_padrao_se_a_api_falhar(tmp_path, monkeypatch):
    class ApiQuebrada:
        @property
        def shell32(self):
            raise OSError("api indisponível")

    monkeypatch.setattr(caminhos.ctypes, "windll", ApiQuebrada())
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert caminhos.obter_pasta_downloads() == tmp_path / "Downloads"


FUNCOES = [
    ("obter_pasta_downloads", "Downloads"),
    ("obter_pasta_documentos", "Documents"),
    ("obter_pasta_imagens", "Pictures"),
]


@pytest.mark.parametrize("funcao, nome_padrao", FUNCOES)
def test_fora_do_windows_cada_pasta_cai_no_nome_padrao_da_pasta_pessoal(
        tmp_path, monkeypatch, funcao, nome_padrao):
    monkeypatch.setattr(caminhos.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert getattr(caminhos, funcao)() == tmp_path / nome_padrao


@pytest.mark.skipif(sys.platform != "win32", reason="Known Folders só existem no Windows")
@pytest.mark.parametrize("funcao, nome_padrao", FUNCOES)
def test_no_windows_cada_pasta_cai_no_padrao_se_a_api_falhar(
        tmp_path, monkeypatch, funcao, nome_padrao):
    class ApiQuebrada:
        @property
        def shell32(self):
            raise OSError("api indisponível")

    monkeypatch.setattr(caminhos.ctypes, "windll", ApiQuebrada())
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert getattr(caminhos, funcao)() == tmp_path / nome_padrao


@pytest.mark.skipif(sys.platform != "win32", reason="Known Folders só existem no Windows")
def test_no_windows_as_tres_pastas_sao_absolutas_e_diferentes_entre_si():
    pastas = [getattr(caminhos, funcao)() for funcao, _ in FUNCOES]

    assert all(p.is_absolute() for p in pastas)
    assert len({str(p).casefold() for p in pastas}) == 3  # um identificador trocado repetiria


def test_os_identificadores_sao_guids_validos_e_distintos():
    ids = [caminhos.FOLDERID_DOWNLOADS, caminhos.FOLDERID_DOCUMENTS, caminhos.FOLDERID_PICTURES]

    assert len({uuid.UUID(i) for i in ids}) == 3
    assert caminhos.FOLDERID_DOCUMENTS == "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
    assert caminhos.FOLDERID_PICTURES == "{33E28130-4E1E-4676-835A-98395C3BC3BB}"
