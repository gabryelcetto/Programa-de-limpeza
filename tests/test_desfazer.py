import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from zelador import desfazer
from zelador.desfazer import Estado
from zelador.historico import Historico
from zelador.regras import (
    Apagar,
    EnviarParaLixeira,
    ExtensaoEm,
    Mover,
    Registro,
    Regra,
    executar_regra,
)

SISTEMA = Path(os.environ.get("SystemRoot", "/etc"))


@pytest.fixture
def historico(tmp_path):
    return Historico(tmp_path / "dados" / "historico.jsonl")


def criar(pasta, nome, conteudo="x"):
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / nome
    arquivo.write_text(conteudo)
    return arquivo


def rodar(historico, pasta, acao, ext="pdf", comando="rodar"):
    """Executa uma regra de verdade, gravando no histórico. Devolve o último lote gravado."""
    gravador = historico.gravador(comando)
    executar_regra(Regra("r", pasta, [ExtensaoEm((ext,))], acao), registrar=gravador)
    return historico.ler().lotes[-1]


def registrar_a_mao(historico, registro):
    historico.gravador("rodar")(registro)
    return historico.ler().lotes[-1]


def test_desfaz_o_que_foi_movido(tmp_path, historico):
    origem = tmp_path / "entrada"
    a = criar(origem, "a.pdf", "um")
    b = criar(origem, "b.pdf", "dois")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    assert not a.exists() and not b.exists()

    resultados = desfazer.desfazer_lote(historico, lote)

    assert {r.estado for r in resultados} == {Estado.DESFEITO}
    assert a.read_text() == "um" and b.read_text() == "dois"
    assert list((tmp_path / "saida").iterdir()) == []


def test_o_nome_numerado_volta_ao_nome_original(tmp_path, historico):
    origem = tmp_path / "entrada"
    saida = tmp_path / "saida"
    criar(saida, "a.pdf", "ja estava la")
    criar(origem, "a.pdf", "novo")
    lote = rodar(historico, origem, Mover(saida))
    assert (saida / "a (1).pdf").exists()

    desfazer.desfazer_lote(historico, lote)

    assert (origem / "a.pdf").read_text() == "novo"
    assert (saida / "a.pdf").read_text() == "ja estava la"  # o que era do destino não é tocado
    assert not (saida / "a (1).pdf").exists()


def test_pasta_movida_volta_inteira(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem / "proj", "dado.txt", "oi")
    destino = tmp_path / "saida" / "proj"
    destino.parent.mkdir()
    (origem / "proj").rename(destino)
    lote = registrar_a_mao(historico, Registro("r", "mover", origem / "proj", destino, 2, True))

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.DESFEITO
    assert (origem / "proj" / "dado.txt").read_text() == "oi"


def test_lugar_original_ocupado_volta_com_outro_nome_sem_sobrescrever(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf", "do historico")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    criar(origem, "a.pdf", "novo ocupante")

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.DESFEITO
    assert r.restaurado_em == origem / "a (1).pdf"
    assert "outro nome" in r.detalhe
    assert (origem / "a.pdf").read_text() == "novo ocupante"
    assert (origem / "a (1).pdf").read_text() == "do historico"


def test_desfazer_duas_vezes_nao_faz_nada_na_segunda(tmp_path, historico):
    origem = tmp_path / "entrada"
    a = criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    desfazer.desfazer_lote(historico, lote)

    lote = historico.ler().lotes[-1]
    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.JA_DESFEITO
    assert a.exists()
    assert desfazer.ultimo_lote_desfazivel(historico.ler()) is None


def test_simulacao_nao_move_nem_grava(tmp_path, historico):
    origem = tmp_path / "entrada"
    a = criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    antes = historico.arquivo.read_bytes()

    [r] = desfazer.desfazer_lote(historico, lote, dry_run=True)

    assert r.estado is Estado.DESFEITO and r.restaurado_em == a
    assert not a.exists()
    assert historico.arquivo.read_bytes() == antes


@pytest.mark.parametrize("acao, trecho", [(Apagar(), "apagado"), (EnviarParaLixeira(), "Lixeira")])
def test_lixeira_e_apagar_nao_sao_desfeitos(tmp_path, historico, acao, trecho):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf")
    lote = rodar(historico, origem, acao)

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.NAO_DESFAZIVEL
    assert trecho in r.detalhe
    assert not desfazer.lote_desfazivel(lote)


def test_item_que_sumiu_do_destino_e_informado_sem_erro(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    (tmp_path / "saida" / "a.pdf").unlink()

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.NAO_ENCONTRADO
    assert not historico.ler().lotes[-1].acoes[0].desfeita


def test_um_item_com_problema_nao_impede_os_outros(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf")
    criar(origem, "b.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    (tmp_path / "saida" / "a.pdf").unlink()

    resultados = desfazer.desfazer_lote(historico, lote)

    assert [r.estado for r in resultados] == [Estado.DESFEITO, Estado.NAO_ENCONTRADO]
    assert (origem / "b.pdf").exists()


def test_processa_da_ultima_acao_para_a_primeira(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf")
    criar(origem, "b.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))

    resultados = desfazer.desfazer_lote(historico, lote)

    assert [r.acao.origem.name for r in resultados] == ["b.pdf", "a.pdf"]


def test_historico_adulterado_com_item_em_pasta_do_sistema_e_recusado(
        tmp_path, historico, monkeypatch):
    existente = next((f for f in SISTEMA.iterdir() if f.is_file()), None)
    if existente is None:
        pytest.skip("não há arquivo na pasta do sistema para usar de exemplo")

    def nao_deveria_mover(atual, original):
        raise AssertionError("tentou mover um arquivo do sistema")

    monkeypatch.setattr(desfazer, "restaurar_item", nao_deveria_mover)
    lote = registrar_a_mao(
        historico, Registro("r", "mover", tmp_path / "volta" / existente.name, existente, 1, False))

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.RECUSADO
    assert "sistema" in r.detalhe


def test_historico_adulterado_com_lugar_de_volta_protegido_e_recusado(tmp_path, historico):
    guardado = criar(tmp_path / "saida", "x.txt")
    lote = registrar_a_mao(
        historico, Registro("r", "mover", SISTEMA / "x.txt", guardado, 1, False))

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.RECUSADO
    assert guardado.exists()
    assert not (SISTEMA / "x.txt").exists()


def test_item_que_mudou_de_tipo_e_recusado(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    (tmp_path / "saida" / "a.pdf").unlink()
    (tmp_path / "saida" / "a.pdf").mkdir()

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.RECUSADO
    assert (tmp_path / "saida" / "a.pdf").is_dir()


def test_falha_do_sistema_ao_mover_vira_erro_do_item(tmp_path, historico, monkeypatch):
    origem = tmp_path / "entrada"
    a = criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))

    def quebrar(atual, original):
        raise OSError("em uso")

    monkeypatch.setattr(desfazer, "restaurar_item", quebrar)

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.ERRO and "em uso" in r.detalhe
    assert not a.exists() and (tmp_path / "saida" / "a.pdf").exists()
    assert not historico.ler().lotes[-1].acoes[0].desfeita


def test_falha_ao_gravar_o_desfeito_nao_desfaz_o_que_voltou(tmp_path, historico, monkeypatch):
    origem = tmp_path / "entrada"
    a = criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))

    def quebrar(acao, restaurada_em):
        raise OSError("disco cheio")

    monkeypatch.setattr(historico, "marcar_desfeito", quebrar)

    [r] = desfazer.desfazer_lote(historico, lote)

    assert r.estado is Estado.DESFEITO
    assert a.exists()


def test_escolha_do_ultimo_lote_pula_os_que_nao_tem_o_que_desfazer(tmp_path, historico):
    pasta1, pasta2 = tmp_path / "um", tmp_path / "dois"
    criar(pasta1, "a.pdf")
    criar(pasta2, "b.pdf")
    rodar(historico, pasta1, Mover(tmp_path / "saida"), comando="rodar")
    rodar(historico, pasta2, Apagar(), comando="limpar")
    leitura = historico.ler()

    assert len(leitura.lotes) == 2
    assert desfazer.ultimo_lote_desfazivel(leitura).comando == "rodar"
    assert desfazer.achar_lote(leitura, leitura.lotes[1].id).comando == "limpar"
    assert desfazer.achar_lote(leitura, "nao-existe") is None


def test_o_registro_de_desfeito_fica_no_arquivo(tmp_path, historico):
    origem = tmp_path / "entrada"
    criar(origem, "a.pdf")
    lote = rodar(historico, origem, Mover(tmp_path / "saida"))
    desfazer.desfazer_lote(historico, lote)

    linhas = [json.loads(x) for x in historico.arquivo.read_text(encoding="utf-8").splitlines()]

    assert [x["tipo"] for x in linhas] == ["lote", "acao", "desfeito"]
    assert datetime.fromisoformat(linhas[-1]["instante"])
