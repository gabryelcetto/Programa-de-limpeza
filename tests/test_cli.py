import os
import time
from types import SimpleNamespace

import pytest

from zelador import arquivos, cli, limpeza, logs, regras

DIA = 24 * 3600


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    """Veja o mesmo fixture em test_limpeza.py: no Windows o os.utime não muda a criação."""
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


@pytest.fixture(autouse=True)
def fechar_logs():
    """Todo cli.main abre um arquivo de log; no Windows, handler aberto impede apagar o tmp_path."""
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Pasta 'Downloads' falsa com um arquivo antigo e um recente; nada toca o disco real."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    antigo = downloads / "antigo.txt"
    recente = downloads / "recente.txt"
    for arquivo, dias in ((antigo, 30), (recente, 1)):
        arquivo.write_text("x")
        t = time.time() - dias * DIA
        os.utime(arquivo, (t, t))

    pasta_logs = tmp_path / "logs"
    monkeypatch.setattr(limpeza, "obter_pasta_downloads", lambda: downloads)
    monkeypatch.setattr(logs, "pasta_logs_padrao", lambda: pasta_logs)
    return SimpleNamespace(downloads=downloads, antigo=antigo, recente=recente,
                           pasta_logs=pasta_logs)


def test_limpar_apaga_so_o_antigo(ambiente):
    codigo = cli.main(["limpar", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 0
    assert not ambiente.antigo.exists()
    assert ambiente.recente.exists()


def test_limpar_dry_run_nao_apaga(ambiente):
    codigo = cli.main(["limpar", "--dry-run", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 0
    assert ambiente.antigo.exists()


def test_limpar_respeita_o_limite_de_dias(ambiente):
    cli.main(["limpar", "--dias", "60", "--pasta", str(ambiente.downloads),
              "--log-dir", str(ambiente.pasta_logs)])

    assert ambiente.antigo.exists()  # tem 30 dias, abaixo do limite de 60


def test_limpar_usa_downloads_do_usuario_por_padrao(ambiente):
    cli.main(["limpar", "--log-dir", str(ambiente.pasta_logs)])

    assert not ambiente.antigo.exists()
    assert ambiente.recente.exists()


def test_retrocompatibilidade_so_opcoes_equivale_a_limpar(ambiente):
    codigo = cli.main(["--dry-run", "--dias", "7", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 0
    assert ambiente.antigo.exists()  # dry-run foi respeitado, ou seja, chegou em `limpar`


def test_retrocompatibilidade_sem_argumentos_equivale_a_limpar(ambiente):
    assert cli.main([]) == 0

    assert not ambiente.antigo.exists()
    assert ambiente.recente.exists()


def test_subcomando_desconhecido_e_rejeitado(ambiente, capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["organizar"])

    assert saida.value.code == 2
    assert ambiente.antigo.exists()
    assert "organizar" in capsys.readouterr().err


def test_help_lista_os_subcomandos(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["--help"])

    assert saida.value.code == 0
    assert "limpar" in capsys.readouterr().out


def test_help_do_subcomando_mostra_opcoes_comuns_e_proprias(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["limpar", "--help"])

    saida_texto = capsys.readouterr().out
    assert saida.value.code == 0
    for opcao in ("--dry-run", "--log-dir", "--dias", "--pasta"):
        assert opcao in saida_texto


@pytest.mark.parametrize("valor", ["0", "-3", "abc", "1.5"])
def test_dias_invalidos_sao_rejeitados(valor, ambiente):
    with pytest.raises(SystemExit) as saida:
        cli.main(["limpar", "--dias", valor, "--pasta", str(ambiente.downloads),
                  "--log-dir", str(ambiente.pasta_logs)])

    assert saida.value.code == 2
    assert ambiente.antigo.exists()


def test_pasta_inexistente_retorna_um(ambiente):
    codigo = cli.main(["limpar", "--pasta", str(ambiente.downloads / "nao_existe"),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 1


def test_erro_na_limpeza_resulta_em_codigo_de_saida_um(ambiente, monkeypatch):
    def falhar(item):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr(regras, "deletar_item", falhar)

    codigo = cli.main(["limpar", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 1
    assert ambiente.antigo.exists()


def test_relatorio_final_informa_quantidade_tamanho_e_mantidos(ambiente, caplog):
    cli.main(["limpar", "--pasta", str(ambiente.downloads), "--log-dir", str(ambiente.pasta_logs)])

    assert "Concluído: 1 apagados (1.0 B), 1 mantidos, 0 erros." in caplog.text


def test_relatorio_final_da_simulacao_diz_seriam_apagados(ambiente, caplog):
    cli.main(["limpar", "--dry-run", "--pasta", str(ambiente.downloads),
              "--log-dir", str(ambiente.pasta_logs)])

    assert "Concluído: 1 seriam apagados (1.0 B), 1 mantidos, 0 erros." in caplog.text
    assert "[SIMULAÇÃO] Arquivo deletado(a): antigo.txt (1.0 B)" in caplog.text


def test_grava_log_do_dia(ambiente):
    cli.main(["limpar", "--dry-run", "--pasta", str(ambiente.downloads),
              "--log-dir", str(ambiente.pasta_logs)])

    arquivos = list(ambiente.pasta_logs.glob("*/log.txt"))
    assert len(arquivos) == 1
    assert "antigo.txt" in arquivos[0].read_text(encoding="utf-8")


def test_rotaciona_logs_antigos_ao_final(ambiente):
    antigo = ambiente.pasta_logs / "2000-01-01"
    antigo.mkdir(parents=True)

    cli.main(["limpar", "--dry-run", "--pasta", str(ambiente.downloads),
              "--log-dir", str(ambiente.pasta_logs)])

    assert not antigo.exists()
