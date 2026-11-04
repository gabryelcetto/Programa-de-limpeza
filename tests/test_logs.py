import logging

import pytest

from zelador import logs


@pytest.fixture(autouse=True)
def limpar_handlers():
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


def handler_de_arquivo():
    return [h for h in logs.logger.handlers if isinstance(h, logging.FileHandler)]


def test_configurar_grava_no_arquivo_do_dia(tmp_path):
    arquivo = logs.configurar_logging(tmp_path)

    logs.logger.info("olá do teste")
    for handler in logs.logger.handlers:
        handler.flush()

    assert arquivo.parent.parent == tmp_path
    assert "olá do teste" in arquivo.read_text(encoding="utf-8")


def test_configurar_de_novo_fecha_o_arquivo_anterior_e_nao_duplica(tmp_path):
    logs.configurar_logging(tmp_path / "um")
    (primeiro,) = handler_de_arquivo()

    logs.configurar_logging(tmp_path / "dois")

    assert primeiro.stream is None  # foi fechado, e não só esquecido
    assert primeiro not in logs.logger.handlers
    assert len(handler_de_arquivo()) == 1
    assert len(logs.logger.handlers) == 2  # console + arquivo, sem acumular


def test_pasta_de_log_inacessivel_segue_so_com_o_console(tmp_path):
    ocupado = tmp_path / "arquivo_no_lugar_da_pasta"
    ocupado.write_text("x")

    arquivo = logs.configurar_logging(ocupado)

    assert arquivo is None
    assert handler_de_arquivo() == []


def test_sem_localappdata_os_logs_ficam_na_pasta_do_usuario_e_nao_no_pacote(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(logs.Path, "home", lambda: tmp_path)

    pasta = logs.pasta_logs_padrao()

    assert pasta == tmp_path / ".local" / "state" / "zelador" / "logs"
