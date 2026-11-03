import sys
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
