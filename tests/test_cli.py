import os
import time
from pathlib import Path
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


def test_sem_argumentos_mostra_a_ajuda_e_nao_mexe_em_nada(ambiente, capsys, monkeypatch):
    """Duplo clique no .exe chama o programa sem argumentos: isso nunca pode limpar Downloads."""
    monkeypatch.setattr(cli, "_console_proprio", lambda: False)

    assert cli.main([]) == 0

    saida = capsys.readouterr().out
    assert "COMANDO" in saida and "Nada foi alterado" in saida
    assert ambiente.antigo.exists() and ambiente.recente.exists()


def test_sem_argumentos_com_console_proprio_espera_antes_de_fechar(ambiente, monkeypatch):
    esperou = []
    monkeypatch.setattr(cli, "_console_proprio", lambda: True)
    monkeypatch.setattr("builtins.input", lambda texto="": esperou.append(texto) or "")

    cli.main([])

    assert len(esperou) == 1


def test_com_terminal_ja_aberto_nao_espera_enter(ambiente, monkeypatch):
    def nao_deveria_esperar(texto=""):
        raise AssertionError("não deveria esperar")

    monkeypatch.setattr(cli, "_console_proprio", lambda: False)
    monkeypatch.setattr("builtins.input", nao_deveria_esperar)

    cli.main([])


def test_console_proprio_so_existe_no_windows(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")

    assert cli._console_proprio() is False


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


def test_pasta_que_nao_pode_ser_lida_retorna_um_sem_traceback(ambiente, monkeypatch, caplog):
    def acesso_negado(self):
        raise PermissionError(13, "Acesso negado", str(self))

    monkeypatch.setattr(Path, "iterdir", acesso_negado)

    codigo = cli.main(["limpar", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 1
    assert "Não foi possível ler a pasta" in caplog.text
    assert ambiente.antigo.exists()


def test_erro_ao_apagar_de_vez_resulta_em_codigo_de_saida_um(ambiente, monkeypatch):
    def falhar(item):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr(regras, "deletar_item", falhar)

    codigo = cli.main(["limpar", "--permanente", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 1
    assert ambiente.antigo.exists()


def test_erro_ao_enviar_para_a_lixeira_resulta_em_codigo_de_saida_um(ambiente, monkeypatch):
    def falhar(item):
        raise OSError("arquivo em uso")

    monkeypatch.setattr(arquivos, "enviar_para_lixeira", falhar)

    codigo = cli.main(["limpar", "--pasta", str(ambiente.downloads),
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 1
    assert ambiente.antigo.exists()


def executar_limpar(ambiente, *extras):
    return cli.main(["limpar", *extras, "--pasta", str(ambiente.downloads),
                     "--log-dir", str(ambiente.pasta_logs)])


def test_relatorio_padrao_diz_que_foi_para_a_lixeira_e_avisa_do_espaco(ambiente, caplog):
    executar_limpar(ambiente)

    assert "enviando para a Lixeira" in caplog.text  # a linha de início diz o que vai acontecer
    assert "Concluído: 1 enviados para a Lixeira (1.0 B), 1 mantidos, 0 erros." in caplog.text
    assert "O espaço em disco só volta quando você esvaziar a Lixeira." in caplog.text


def test_relatorio_da_simulacao_padrao_diz_seriam_enviados_e_nao_avisa_do_espaco(ambiente, caplog):
    executar_limpar(ambiente, "--dry-run")

    assert ("Concluído: 1 seriam enviados para a Lixeira (1.0 B), 1 mantidos, 0 erros."
            in caplog.text)
    assert "[SIMULAÇÃO] Arquivo enviado(a) para a Lixeira: antigo.txt (1.0 B)" in caplog.text
    assert "esvaziar a Lixeira" not in caplog.text  # nada aconteceu: não há espaço a explicar


def test_relatorio_permanente_diz_apagados_e_nao_fala_de_lixeira(ambiente, caplog):
    executar_limpar(ambiente, "--permanente")

    assert "apagando de vez" in caplog.text
    assert "Concluído: 1 apagados (1.0 B), 1 mantidos, 0 erros." in caplog.text
    assert "esvaziar a Lixeira" not in caplog.text


def test_relatorio_da_simulacao_permanente_diz_seriam_apagados(ambiente, caplog):
    executar_limpar(ambiente, "--permanente", "--dry-run")

    assert "Concluído: 1 seriam apagados (1.0 B), 1 mantidos, 0 erros." in caplog.text
    assert "[SIMULAÇÃO] Arquivo deletado(a): antigo.txt (1.0 B)" in caplog.text


def test_sem_nada_para_limpar_nao_avisa_do_espaco(ambiente, caplog):
    executar_limpar(ambiente, "--dias", "60")  # o "antigo" tem 30 dias: não é antigo o bastante

    assert "Concluído: 0 enviados para a Lixeira" in caplog.text
    assert "esvaziar a Lixeira" not in caplog.text


def test_padrao_usa_a_lixeira_e_permanente_nao(ambiente, lixeira_falsa):
    executar_limpar(ambiente)
    assert lixeira_falsa.enviados == [ambiente.antigo]

    outro = ambiente.downloads / "outro.txt"
    outro.write_text("x")
    t = time.time() - 30 * 24 * 3600
    os.utime(outro, (t, t))
    executar_limpar(ambiente, "--permanente")

    assert not outro.exists()
    assert lixeira_falsa.enviados == [ambiente.antigo]  # o permanente não passou pela Lixeira


def test_ajuda_do_limpar_mostra_a_opcao_permanente(capsys):
    with pytest.raises(SystemExit):
        cli.main(["limpar", "--help"])

    assert "--permanente" in capsys.readouterr().out


def test_chamada_sem_comando_equivale_a_limpar_com_lixeira(ambiente, lixeira_falsa):
    codigo = cli.main(["--pasta", str(ambiente.downloads), "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 0
    assert lixeira_falsa.enviados == [ambiente.antigo]  # a compatibilidade herda o novo padrão


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


def test_limpar_recusa_pasta_protegida_sem_listar_nem_apagar(ambiente, monkeypatch, caplog):
    """Mesmo com --dry-run (para nada ser apagado se a proteção falhar): raiz do disco é
    recusada antes de qualquer varredura."""
    raiz = os.path.splitdrive(os.path.realpath(ambiente.downloads))[0] + os.sep

    def nao_pode_executar(*args, **kwargs):
        raise AssertionError("a regra não deveria ter sido executada")

    monkeypatch.setattr(limpeza, "executar_regra", nao_pode_executar)

    codigo = cli.main(["limpar", "--dry-run", "--pasta", raiz,
                       "--log-dir", str(ambiente.pasta_logs)])

    assert codigo == 2
    assert "raiz de um disco" in caplog.text


def test_sem_teclado_no_console_nao_quebra(ambiente, monkeypatch):
    def sem_teclado(texto=""):
        raise EOFError

    monkeypatch.setattr(cli, "_console_proprio", lambda: True)
    monkeypatch.setattr("builtins.input", sem_teclado)

    assert cli.main([]) == 0
