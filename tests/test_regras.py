import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from limpeza_downloads import arquivos
from limpeza_downloads.regras import (
    Apagar,
    ExtensaoEm,
    IdadeMinima,
    Item,
    Mover,
    NomeCorresponde,
    Regra,
    TamanhoEntre,
    executar_regra,
)

DIA = 24 * 3600
AGORA = datetime(2026, 1, 20)


def item(nome="a.txt", eh_pasta=False, tamanho=0, dias=0):
    atividade = AGORA.timestamp() - dias * DIA
    return Item(Path(nome), eh_pasta, atividade, tamanho)


# ------------------------------------------------------------- condições


def test_idade_minima_exige_mais_que_o_limite():
    cond = IdadeMinima(7)

    assert cond.corresponde(item(dias=8), AGORA)
    assert not cond.corresponde(item(dias=7), AGORA)  # exatamente 7 dias: ainda não é "mais de 7"
    assert not cond.corresponde(item(dias=1), AGORA)


@pytest.mark.parametrize("dias", [0, -1])
def test_idade_minima_rejeita_zero_e_negativo(dias):
    with pytest.raises(ValueError):
        IdadeMinima(dias)


def test_extensao_ignora_maiusculas_e_aceita_com_ou_sem_ponto():
    cond = ExtensaoEm((".PDF", "docx"))

    assert cond.corresponde(item("a.pdf"), AGORA)
    assert cond.corresponde(item("B.Docx"), AGORA)
    assert not cond.corresponde(item("c.txt"), AGORA)


def test_extensao_pastas_e_arquivos_sem_extensao_nao_correspondem():
    cond = ExtensaoEm(("pdf",))

    assert not cond.corresponde(item("relatorio.pdf", eh_pasta=True), AGORA)
    assert not cond.corresponde(item("LEIAME"), AGORA)
    assert not cond.corresponde(item(".pdf"), AGORA)  # arquivo oculto, sem extensão


def test_extensao_composta_vale_so_a_ultima():
    assert ExtensaoEm(("gz",)).corresponde(item("backup.tar.gz"), AGORA)
    assert not ExtensaoEm(("tar",)).corresponde(item("backup.tar.gz"), AGORA)


@pytest.mark.parametrize("extensoes", [(), ("",), (".",), ("pdf", "")])
def test_extensao_rejeita_lista_vazia_ou_com_item_vazio(extensoes):
    with pytest.raises(ValueError):
        ExtensaoEm(extensoes)


def test_tamanho_limites_sao_inclusivos():
    cond = TamanhoEntre(minimo=10, maximo=20)

    assert cond.corresponde(item(tamanho=10), AGORA)
    assert cond.corresponde(item(tamanho=20), AGORA)
    assert not cond.corresponde(item(tamanho=9), AGORA)
    assert not cond.corresponde(item(tamanho=21), AGORA)


def test_tamanho_so_minimo_ou_so_maximo():
    assert TamanhoEntre(minimo=100).corresponde(item(tamanho=10**9), AGORA)
    assert not TamanhoEntre(minimo=100).corresponde(item(tamanho=99), AGORA)
    assert TamanhoEntre(maximo=100).corresponde(item(tamanho=0), AGORA)
    assert not TamanhoEntre(maximo=100).corresponde(item(tamanho=101), AGORA)


def test_tamanho_zero_e_um_limite_valido():
    assert TamanhoEntre(minimo=0).corresponde(item(tamanho=0), AGORA)
    assert TamanhoEntre(maximo=0).corresponde(item(tamanho=0), AGORA)


@pytest.mark.parametrize("kwargs", [{}, {"minimo": 5, "maximo": 4}])
def test_tamanho_rejeita_limites_invalidos(kwargs):
    with pytest.raises(ValueError):
        TamanhoEntre(**kwargs)


def test_nome_usa_regex_sem_diferenciar_maiusculas():
    assert NomeCorresponde(r"relat[óo]rio").corresponde(item("Relatório_2026.pdf"), AGORA)
    assert NomeCorresponde(r"\d{4}").corresponde(item("foto_2026.jpg"), AGORA)
    assert not NomeCorresponde(r"^foto").corresponde(item("minha_foto.jpg"), AGORA)


def test_nome_rejeita_regex_invalida():
    with pytest.raises(ValueError, match="inválida"):
        NomeCorresponde("(abc")


# ----------------------------------------------------------------- regra


def test_regra_sem_condicao_e_rejeitada(tmp_path):
    with pytest.raises(ValueError, match="ao menos uma condição"):
        Regra("tudo", tmp_path, [], Apagar())


# --------------------------------------------------------- execução real


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    """No Windows o os.utime não muda a data de criação; os testes usam só o mtime."""
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


def criar(pasta, nome, dias, conteudo="x"):
    arquivo = pasta / nome
    arquivo.write_text(conteudo)
    t = time.time() - dias * DIA
    os.utime(arquivo, (t, t))
    return arquivo


def envelhecer(caminho, dias):
    t = time.time() - dias * DIA
    os.utime(caminho, (t, t))


def test_apagar_por_idade(tmp_path):
    antigo = criar(tmp_path, "antigo.txt", 10)
    recente = criar(tmp_path, "recente.txt", 1)

    resumo = executar_regra(Regra("limpar", tmp_path, [IdadeMinima(7)], Apagar()))

    assert not antigo.exists() and recente.exists()
    assert (resumo.aplicados, resumo.ignorados, resumo.erros) == (1, 1, 0)


def test_todas_as_condicoes_precisam_ser_verdadeiras(tmp_path):
    pdf_antigo = criar(tmp_path, "a.pdf", 10)
    txt_antigo = criar(tmp_path, "b.txt", 10)
    pdf_novo = criar(tmp_path, "c.pdf", 1)
    regra = Regra("pdfs velhos", tmp_path, [IdadeMinima(7), ExtensaoEm(("pdf",))], Apagar())

    resumo = executar_regra(regra)

    assert not pdf_antigo.exists()
    assert txt_antigo.exists() and pdf_novo.exists()
    assert (resumo.aplicados, resumo.ignorados) == (1, 2)


def test_mover_cria_o_destino_e_soma_os_bytes(tmp_path):
    pasta = tmp_path / "downloads"
    pasta.mkdir()
    criar(pasta, "a.pdf", 1, "12345")
    criar(pasta, "b.pdf", 1, "123")
    criar(pasta, "c.txt", 1)
    destino = tmp_path / "docs" / "pdfs"

    resumo = executar_regra(Regra("organizar", pasta, [ExtensaoEm(("pdf",))], Mover(destino)))

    assert sorted(p.name for p in destino.iterdir()) == ["a.pdf", "b.pdf"]
    assert (pasta / "c.txt").exists()
    assert (resumo.aplicados, resumo.bytes) == (2, 8)


def test_mover_nome_repetido_preserva_os_dois(tmp_path):
    pasta = tmp_path / "downloads"
    pasta.mkdir()
    destino = tmp_path / "docs"
    destino.mkdir()
    (destino / "a.pdf").write_text("antigo")
    criar(pasta, "a.pdf", 1, "novo")

    resumo = executar_regra(Regra("organizar", pasta, [ExtensaoEm(("pdf",))], Mover(destino)))

    assert resumo.erros == 0
    assert (destino / "a.pdf").read_text() == "antigo"
    assert (destino / "a (1).pdf").read_text() == "novo"


def test_mover_pasta_antiga_inteira(tmp_path):
    pasta = tmp_path / "downloads"
    (pasta / "projeto" / "sub").mkdir(parents=True)
    criar(pasta / "projeto" / "sub", "dado.txt", 30, "oi")
    envelhecer(pasta / "projeto" / "sub", 30)
    envelhecer(pasta / "projeto", 30)
    destino = tmp_path / "arquivo"

    resumo = executar_regra(Regra("arquivar", pasta, [IdadeMinima(7)], Mover(destino)))

    assert (destino / "projeto" / "sub" / "dado.txt").read_text() == "oi"
    assert not (pasta / "projeto").exists()
    assert resumo.aplicados == 1


def test_destino_dentro_da_propria_pasta_nao_e_movido_para_dentro_de_si(tmp_path):
    pasta = tmp_path / "downloads"
    destino = pasta / "Antigos"
    destino.mkdir(parents=True)
    criar(destino, "ja_organizado.txt", 30)
    envelhecer(destino, 30)
    antigo = criar(pasta, "velho.txt", 30)

    resumo = executar_regra(Regra("arquivar", pasta, [IdadeMinima(7)], Mover(destino)))

    assert resumo.erros == 0
    assert (destino / "velho.txt").exists() and not antigo.exists()
    assert (destino / "ja_organizado.txt").exists()
    assert destino.is_dir()
    assert not (destino / "Antigos").exists()


def test_ocupa_detecta_o_destino_e_seus_ancestrais(tmp_path):
    destino = tmp_path / "a" / "b"
    acao = Mover(destino)

    assert acao.ocupa(destino)
    assert acao.ocupa(tmp_path / "a")
    assert not acao.ocupa(tmp_path / "a" / "c")
    assert not acao.ocupa(tmp_path / "ab")
    # nome que apenas começa igual não é ancestral: "a" não contém "ab/c"
    assert not Mover(tmp_path / "ab" / "c").ocupa(tmp_path / "a")
    assert not Apagar().ocupa(tmp_path)


def test_dry_run_nao_altera_nada_mas_conta(tmp_path):
    pasta = tmp_path / "downloads"
    pasta.mkdir()
    antigo = criar(pasta, "antigo.txt", 10, "12345")
    destino = tmp_path / "destino"

    r_apagar = executar_regra(Regra("a", pasta, [IdadeMinima(7)], Apagar()), dry_run=True)
    r_mover = executar_regra(Regra("m", pasta, [IdadeMinima(7)], Mover(destino)), dry_run=True)

    assert antigo.exists()
    assert not destino.exists()
    assert (r_apagar.aplicados, r_apagar.bytes) == (1, 5)
    assert (r_mover.aplicados, r_mover.bytes) == (1, 5)


def test_regra_nao_desce_nas_subpastas_por_conta_propria(tmp_path):
    pasta = tmp_path / "downloads"
    (pasta / "sub").mkdir(parents=True)
    interno = criar(pasta / "sub", "a.pdf", 1)

    resumo = executar_regra(Regra("pdfs", pasta, [ExtensaoEm(("pdf",))], Mover(tmp_path / "d")))

    assert interno.exists()  # a subpasta é um item único, e pastas não têm extensão
    assert resumo.aplicados == 0


def test_erro_em_um_item_nao_interrompe_os_outros(tmp_path):
    ruim = criar(tmp_path, "a_ruim.txt", 10)
    bom = criar(tmp_path, "b_bom.txt", 10)

    class ApagarMenosUm(Apagar):
        def aplicar(self, item):
            if item.nome == "a_ruim.txt":
                raise PermissionError("arquivo em uso")
            super().aplicar(item)

    resumo = executar_regra(Regra("r", tmp_path, [IdadeMinima(7)], ApagarMenosUm()))

    assert (resumo.aplicados, resumo.erros) == (1, 1)
    assert ruim.exists() and not bom.exists()


def test_acao_personalizada_funciona_sem_herdar_de_nada(tmp_path):
    criar(tmp_path, "a.txt", 10)
    vistos = []

    class Registrar:
        def descricao(self):
            return "registrado(a)"

        def aplicar(self, item):
            vistos.append(item.nome)

        def ocupa(self, caminho):
            return False

    resumo = executar_regra(Regra("r", tmp_path, [IdadeMinima(7)], Registrar()))

    assert vistos == ["a.txt"] and resumo.aplicados == 1


def test_pasta_vazia_nao_quebra(tmp_path):
    resumo = executar_regra(Regra("r", tmp_path, [IdadeMinima(7)], Apagar()))

    assert (resumo.aplicados, resumo.ignorados, resumo.erros) == (0, 0, 0)


def test_pasta_inexistente_levanta_oserror(tmp_path):
    with pytest.raises(OSError):
        executar_regra(Regra("r", tmp_path / "nao_existe", [IdadeMinima(7)], Apagar()))
