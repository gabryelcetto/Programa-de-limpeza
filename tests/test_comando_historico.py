"""Os comandos `historico` e `desfazer` e a gravação feita por `rodar` e `limpar`, de ponta a ponta
(pelo cli.main, com pastas de verdade em tmp_path)."""

import os
import time
from pathlib import Path
from textwrap import dedent

import pytest

from zelador import arquivos, caminhos, cli, limpeza, logs
from zelador.historico import Historico

DIA = 24 * 3600


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


@pytest.fixture(autouse=True)
def fechar_logs():
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


class Ambiente:
    def __init__(self, tmp_path, monkeypatch, historico_isolado):
        self.tmp = tmp_path
        self.downloads = tmp_path / "Downloads"
        self.downloads.mkdir()
        self.destino = tmp_path / "Docs" / "PDFs"
        self.pasta_logs = tmp_path / "logs"
        self.arquivo_historico = historico_isolado
        monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: self.downloads)
        # o `limpar` importou a função pelo nome: sem isto ele olharia a pasta Downloads DE VERDADE
        monkeypatch.setattr(limpeza, "obter_pasta_downloads", lambda: self.downloads)

    def arquivo(self, nome, dias=0, conteudo="x"):
        caminho = self.downloads / nome
        caminho.write_text(conteudo)
        t = time.time() - dias * DIA
        os.utime(caminho, (t, t))
        return caminho

    def regras(self, acao):
        caminho = self.tmp / "regras.yml"
        caminho.write_text(dedent(f"""
            regras:
              - nome: Organizar PDFs
                pasta: "{{downloads}}"
                condicoes: {{extensoes: [pdf]}}
                acao: {acao}
            """), encoding="utf-8")
        return caminho

    def cli(self, *args):
        return cli.main([*args, "--log-dir", str(self.pasta_logs)])

    @property
    def lotes(self):
        return Historico(self.arquivo_historico).ler().lotes


@pytest.fixture
def amb(tmp_path, monkeypatch, historico_isolado):
    return Ambiente(tmp_path, monkeypatch, historico_isolado)


def mover(amb):
    return amb.regras(f"{{mover: '{amb.destino}'}}")


# ------------------------------------------------------------- gravação


def test_rodar_grava_o_que_moveu_no_historico_padrao(amb):
    amb.arquivo("a.pdf")

    assert amb.cli("rodar", "--config", str(mover(amb))) == 0

    [lote] = amb.lotes
    assert lote.comando == "rodar"
    [acao] = lote.acoes
    assert acao.tipo == "mover"
    assert acao.origem == amb.downloads / "a.pdf"
    assert acao.destino == amb.destino / "a.pdf"


def test_rodar_com_historico_em_outro_arquivo(amb):
    amb.arquivo("a.pdf")
    outro = amb.tmp / "outro" / "h.jsonl"

    amb.cli("rodar", "--config", str(mover(amb)), "--historico", str(outro))

    assert len(Historico(outro).ler().lotes) == 1
    assert not amb.arquivo_historico.exists()


def test_simulacao_nao_grava_nada(amb):
    amb.arquivo("a.pdf")

    amb.cli("rodar", "--dry-run", "--config", str(mover(amb)))

    assert not amb.arquivo_historico.exists()


def test_execucao_que_nao_fez_nada_nao_deixa_rastro(amb):
    amb.cli("rodar", "--config", str(mover(amb)))

    assert not amb.arquivo_historico.exists()


def test_limpar_grava_a_ida_para_a_lixeira(amb):
    amb.arquivo("velho.txt", dias=30)

    assert amb.cli("limpar") == 0

    [lote] = amb.lotes
    assert lote.comando == "limpar"
    assert [a.tipo for a in lote.acoes] == ["lixeira"]


def test_disco_do_historico_com_problema_nao_impede_a_limpeza(amb):
    amb.arquivo("a.pdf")
    bloqueio = amb.tmp / "bloqueio"
    bloqueio.write_text("sou um arquivo, não uma pasta")  # o histórico não pode ser criado aqui

    codigo = amb.cli("rodar", "--config", str(mover(amb)),
                     "--historico", str(bloqueio / "h.jsonl"))

    assert codigo == 0
    assert (amb.destino / "a.pdf").exists()


# --------------------------------------------------------------- desfazer


def test_desfazer_devolve_o_que_o_rodar_moveu(amb):
    a = amb.arquivo("a.pdf", conteudo="oi")
    amb.cli("rodar", "--config", str(mover(amb)))
    assert not a.exists()

    assert amb.cli("desfazer") == 0

    assert a.read_text() == "oi"
    assert not (amb.destino / "a.pdf").exists()


def test_desfazer_simulacao_nao_mexe_em_nada(amb):
    a = amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    antes = amb.arquivo_historico.read_bytes()

    assert amb.cli("desfazer", "--dry-run") == 0

    assert not a.exists() and (amb.destino / "a.pdf").exists()
    assert amb.arquivo_historico.read_bytes() == antes


def test_desfazer_so_a_ultima_execucao(amb):
    a = amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    b = amb.arquivo("b.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))

    amb.cli("desfazer")

    assert b.exists() and not a.exists()  # a segunda voltou; a primeira continua movida


def test_desfazer_por_id_escolhe_uma_execucao_antiga(amb):
    a = amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    primeiro = amb.lotes[0].id
    b = amb.arquivo("b.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))

    assert amb.cli("desfazer", "--lote", primeiro) == 0

    assert a.exists() and not b.exists()


def test_desfazer_id_que_nao_existe_e_erro_de_uso(amb):
    amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))

    assert amb.cli("desfazer", "--lote", "nao-existe") == 2


def test_desfazer_sem_historico_nao_e_erro(amb):
    assert amb.cli("desfazer") == 0


def test_desfazer_pula_execucoes_que_so_apagaram(amb):
    a = amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    amb.arquivo("velho.txt", dias=30)
    amb.cli("limpar")  # a última execução foi uma limpeza: não há o que desfazer nela

    amb.cli("desfazer")

    assert a.exists()


def test_desfazer_com_item_que_sumiu_sai_com_codigo_1_mas_volta_os_outros(amb):
    a = amb.arquivo("a.pdf")
    b = amb.arquivo("b.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    (amb.destino / "a.pdf").unlink()

    assert amb.cli("desfazer") == 1

    assert b.exists() and not a.exists()


def test_historico_ilegivel_nao_derruba_o_desfazer(amb):
    amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    with open(amb.arquivo_historico, "a", encoding="utf-8") as f:
        f.write("isto não é json\n")

    assert amb.cli("desfazer") == 0

    assert (amb.downloads / "a.pdf").exists()


def test_arquivo_de_historico_que_e_uma_pasta_sai_com_codigo_1(amb):
    pasta = amb.tmp / "h"
    pasta.mkdir()

    assert amb.cli("desfazer", "--historico", str(pasta)) == 1
    assert amb.cli("historico", "--historico", str(pasta)) == 1


# -------------------------------------------------------------- historico


def test_historico_vazio(amb, capsys):
    assert amb.cli("historico") == 0
    assert "Nada registrado" in capsys.readouterr().out


def test_historico_lista_do_mais_novo_para_o_mais_antigo(amb, capsys):
    amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    amb.arquivo("velho.txt", dias=30)
    amb.cli("limpar")
    capsys.readouterr()

    assert amb.cli("historico") == 0

    linhas = [x for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert "limpar" in linhas[0] and "Lixeira" in linhas[0]
    assert "rodar" in linhas[1] and "movido(s)" in linhas[1]


def test_historico_ultimos_limita_a_lista(amb, capsys):
    for nome in ("a.pdf", "b.pdf", "c.pdf"):
        amb.arquivo(nome)
        amb.cli("rodar", "--config", str(mover(amb)))
    capsys.readouterr()

    amb.cli("historico", "--ultimos", "2")

    saida = capsys.readouterr().out
    assert saida.count("rodar:") == 2


def test_historico_marca_o_que_foi_desfeito(amb, capsys):
    amb.arquivo("a.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    amb.cli("desfazer")
    capsys.readouterr()

    amb.cli("historico")

    assert "1 desfeito(s)" in capsys.readouterr().out


def test_historico_de_um_lote_mostra_cada_item(amb, capsys):
    amb.arquivo("relatorio final.pdf")
    amb.cli("rodar", "--config", str(mover(amb)))
    [lote] = amb.lotes
    capsys.readouterr()

    assert amb.cli("historico", "--lote", lote.id) == 0

    saida = capsys.readouterr().out
    assert "relatorio final.pdf" in saida
    assert str(amb.destino) in saida
    assert "Organizar PDFs" in saida


def test_historico_de_lote_inexistente(amb):
    assert amb.cli("historico", "--lote", "xyz") == 2


def test_dry_run_so_existe_onde_altera_arquivos(amb):
    with pytest.raises(SystemExit):
        amb.cli("historico", "--dry-run")
    assert Path(amb.tmp).exists()
