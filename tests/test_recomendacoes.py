import json
import os
import time
from pathlib import Path

import psutil
import pytest

from zelador import arquivos, discos
from zelador.painel import recomendacoes as rec
from zelador.painel.recomendacoes import (
    ATENCAO,
    IMPORTANTE,
    INFO,
    Contexto,
    Recomendacao,
)
from zelador.regras import Apagar, Mover, Resumo

GB = 1024**3
MB = 1024**2
DIA = 24 * 3600
ORDEM = {IMPORTANTE: 0, ATENCAO: 1, INFO: 2}


def raiz_de(pasta):
    return str(pasta.anchor) if os.name == "nt" else "/"


def disco(ponto="C:\\", total=200 * GB, percentual=50.0):
    return discos.UsoDisco(ponto, ponto, "NTFS", total, int(total * percentual / 100), percentual)


class Simulador:
    """Substitui a simulação do motor: devolve resultados prontos e registra o que foi pedido."""

    def __init__(self, parados=None, pdfs=None):
        self.parados = parados or Resumo()
        self.pdfs = pdfs or Resumo()
        self.regras = []

    def __call__(self, regra):
        self.regras.append(regra)
        return self.pdfs if isinstance(regra.acao, Mover) else self.parados


def contexto(tmp_path=None, **kw):
    base = dict(discos=[disco()],
                pasta_downloads=(tmp_path / "Downloads") if tmp_path else None,
                pasta_pdfs=(tmp_path / "Docs" / "PDFs") if tmp_path else None,
                simular=Simulador())
    base.update(kw)
    return Contexto(**base)


@pytest.fixture
def pasta(tmp_path):
    (tmp_path / "Downloads").mkdir()
    return tmp_path


# ------------------------------------------------------------ disco cheio


@pytest.mark.parametrize("percentual, esperado", [
    (84.9, None), (85.0, ATENCAO), (90.0, ATENCAO), (94.9, ATENCAO), (95.0, IMPORTANTE),
    (100.0, IMPORTANTE),
])
def test_disco_cheio_gravidade_por_faixa(percentual, esperado):
    ctx = contexto(discos=[disco(percentual=percentual)])

    assert [r.gravidade for r in rec.disco_cheio(ctx)] == ([esperado] if esperado else [])


def test_disco_cheio_traz_a_evidencia_com_os_numeros():
    ctx = contexto(discos=[disco("D:\\", total=100 * GB, percentual=91.0)])

    (r,) = rec.disco_cheio(ctx)

    assert r.id == "disco-cheio:D:\\"
    assert "D:\\" in r.titulo
    assert r.evidencias == ("D:\\ está a 91% (91.0 GB de 100.0 GB; sobram 9.0 GB)",)
    assert r.acao is None


def test_particao_pequena_de_recuperacao_nao_gera_alarme():
    minimo = rec.DISCO_TAMANHO_MINIMO
    ctx = contexto(discos=[disco("R:\\", total=1 * GB, percentual=99.0),
                           disco("C:\\", total=minimo, percentual=90.0)])

    assert [r.id for r in rec.disco_cheio(ctx)] == ["disco-cheio:C:\\"]
    assert minimo == 10 * GB  # a de exatamente 10 GB já conta


def test_varios_discos_cheios_geram_uma_recomendacao_para_cada():
    ctx = contexto(discos=[disco("C:\\", percentual=88.0), disco("D:\\", percentual=96.0)])

    assert [(r.id, r.gravidade) for r in rec.disco_cheio(ctx)] == [
        ("disco-cheio:C:\\", ATENCAO), ("disco-cheio:D:\\", IMPORTANTE)]


def test_disco_cheio_oferece_limpar_downloads_se_a_pasta_esta_nele(pasta):
    ctx = contexto(pasta, discos=[disco(raiz_de(pasta), percentual=92.0)],
                   simular=Simulador(parados=Resumo(aplicados=40, bytes=3 * GB)))

    (r,) = rec.disco_cheio(ctx)

    assert r.acao.id == rec.LIMPAR_DOWNLOADS
    assert (r.acao.itens, r.acao.bytes) == (40, 3 * GB)


def test_disco_cheio_nao_oferece_a_acao_se_downloads_esta_em_outro_disco(pasta):
    outro = "Z:\\" if os.name == "nt" else "/outro"
    ctx = contexto(pasta, discos=[disco(outro, percentual=92.0)],
                   simular=Simulador(parados=Resumo(aplicados=40, bytes=3 * GB)))

    assert rec.disco_cheio(ctx)[0].acao is None


def test_disco_cheio_nao_oferece_a_acao_se_o_ganho_e_desprezivel(pasta):
    ctx = contexto(pasta, discos=[disco(raiz_de(pasta), percentual=92.0)],
                   simular=Simulador(parados=Resumo(aplicados=2, bytes=1 * MB)))

    assert rec.disco_cheio(ctx)[0].acao is None


def test_disco_mais_especifico_vence_na_escolha_do_disco_da_pasta(tmp_path):
    raiz = raiz_de(tmp_path)
    montado = tmp_path / "montado"
    montado.mkdir()
    discos = [disco(raiz, percentual=10.0), disco(str(montado), percentual=10.0)]

    assert rec._disco_de(montado / "Downloads", discos).ponto_de_montagem == str(montado)
    assert rec._disco_de(tmp_path / "outra", discos).ponto_de_montagem == raiz


def test_pasta_com_nome_parecido_nao_pertence_ao_disco_montado_ao_lado(tmp_path):
    raiz = raiz_de(tmp_path)
    dados = tmp_path / "dados"
    dados2 = tmp_path / "dados2"
    dados.mkdir()
    dados2.mkdir()
    discos = [disco(raiz, percentual=10.0), disco(str(dados), percentual=10.0)]

    # "dados2" só COMEÇA com "dados": não está dentro do disco montado em "dados"
    assert rec._disco_de(dados2 / "Downloads", discos).ponto_de_montagem == raiz
    assert rec._disco_de(dados / "Downloads", discos).ponto_de_montagem == str(dados)
    assert rec._disco_de(dados, discos).ponto_de_montagem == str(dados)


# ------------------------------------------------------ downloads parados


def test_downloads_parados_com_ganho_relevante(pasta):
    ctx = contexto(pasta, simular=Simulador(parados=Resumo(aplicados=60, bytes=2 * GB)))

    (r,) = rec.downloads_parados(ctx)

    assert r.id == "downloads-parados" and r.gravidade == INFO
    assert "60 itens (2.0 GB)" in r.evidencias[0] and "30 dias" in r.evidencias[0]
    assert str(pasta / "Downloads") in r.evidencias[0]
    assert (r.acao.id, r.acao.parametros, r.acao.itens, r.acao.bytes) == (
        rec.LIMPAR_DOWNLOADS, {"dias": 30}, 60, 2 * GB)
    assert "confirmar" in r.explicacao  # deixa claro que nada acontece sem o usuário


@pytest.mark.parametrize("bytes_, aparece", [(50 * MB - 1, False), (50 * MB, True)])
def test_downloads_parados_limite_minimo_de_ganho(pasta, bytes_, aparece):
    ctx = contexto(pasta, simular=Simulador(parados=Resumo(aplicados=3, bytes=bytes_)))

    assert bool(rec.downloads_parados(ctx)) is aparece


def test_downloads_parados_sem_pasta_nao_recomenda():
    assert rec.downloads_parados(contexto()) == []


def test_simulacao_de_downloads_usa_a_regra_certa(pasta):
    simulador = Simulador(parados=Resumo(aplicados=1, bytes=100 * MB))

    rec.downloads_parados(contexto(pasta, simular=simulador))

    (regra,) = simulador.regras
    assert regra.pasta == pasta / "Downloads"
    assert isinstance(regra.acao, Apagar)  # é só uma estimativa: a simulação nunca altera nada
    assert regra.condicoes[0].dias == rec.DOWNLOADS_DIAS


def test_mesma_estimativa_e_calculada_uma_vez_so(pasta):
    simulador = Simulador(parados=Resumo(aplicados=40, bytes=3 * GB))
    ctx = contexto(pasta, discos=[disco(raiz_de(pasta), percentual=92.0)], simular=simulador)

    rec.disco_cheio(ctx)
    rec.downloads_parados(ctx)

    assert len(simulador.regras) == 1  # disco_cheio e downloads_parados dividem a conta


def test_pasta_protegida_nao_gera_sugestao_nem_erro(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # Downloads == pasta pessoal: protegida
    ctx = contexto(pasta_downloads=tmp_path, simular=rec._simular)

    assert rec.downloads_parados(ctx) == []


# ------------------------------------------------------------ pdfs soltos


@pytest.mark.parametrize("n, aparece", [(4, False), (5, True), (30, True)])
def test_pdfs_soltos_precisa_de_um_minimo(pasta, n, aparece):
    ctx = contexto(pasta, simular=Simulador(pdfs=Resumo(aplicados=n, bytes=n * MB)))

    assert bool(rec.pdfs_soltos(ctx)) is aparece


def test_pdfs_soltos_traz_evidencia_e_acao_com_destino(pasta):
    ctx = contexto(pasta, simular=Simulador(pdfs=Resumo(aplicados=12, bytes=3 * MB)))

    (r,) = rec.pdfs_soltos(ctx)

    assert r.id == "pdfs-soltos"
    assert "12 PDFs (3.0 MB)" in r.evidencias[0]
    assert r.acao.id == rec.ORGANIZAR_PDFS
    assert r.acao.parametros == {"destino": str(pasta / "Docs" / "PDFs")}
    assert "não apaga nada" in r.explicacao


def test_pdfs_soltos_simula_um_mover_e_nunca_apaga(pasta):
    simulador = Simulador(pdfs=Resumo(aplicados=9, bytes=MB))

    rec.pdfs_soltos(contexto(pasta, simular=simulador))

    (regra,) = simulador.regras
    assert isinstance(regra.acao, Mover) and regra.acao.destino == pasta / "Docs" / "PDFs"
    assert regra.condicoes[0].extensoes == ("pdf",)


def test_pdfs_soltos_sem_destino_ou_sem_pasta_nao_recomenda(pasta):
    assert rec.pdfs_soltos(contexto(pasta, pasta_pdfs=None)) == []
    assert rec.pdfs_soltos(contexto()) == []


# ------------------------------------------------------------------ gerar


def test_gerar_ordena_por_gravidade_e_depois_por_ganho(pasta):
    raiz = raiz_de(pasta)
    simulador = Simulador(parados=Resumo(aplicados=10, bytes=2 * GB),
                          pdfs=Resumo(aplicados=8, bytes=10 * MB))
    ctx = contexto(pasta, discos=[disco(raiz, percentual=97.0)], simular=simulador)

    resultado = rec.gerar(ctx)

    assert resultado[0].id == f"disco-cheio:{raiz}" and resultado[0].gravidade == IMPORTANTE
    assert [r.gravidade for r in resultado] == sorted((r.gravidade for r in resultado),
                                                      key=ORDEM.get)
    com_acao = [r.id for r in resultado if r.gravidade == INFO and r.acao]
    assert com_acao == ["downloads-parados", "pdfs-soltos"]  # 2 GB vêm antes de 10 MB


def test_em_empate_de_gravidade_o_maior_ganho_vem_primeiro():
    def acao(bytes_):
        return rec.AcaoSugerida("x", "r", {}, 1, bytes_)

    def pequena(ctx):
        return [Recomendacao("pequena", "T", INFO, "e", ("v",), acao(1 * MB))]

    def grande(ctx):
        return [Recomendacao("grande", "T", INFO, "e", ("v",), acao(5 * GB))]

    def sem_acao(ctx):
        return [Recomendacao("sem-acao", "T", INFO, "e", ("v",))]

    # a ordem do registro (pequena, sem_acao, grande) é o contrário da ordem pelo ganho
    resultado = rec.gerar(contexto(), recomendadores=(pequena, sem_acao, grande))

    assert [r.id for r in resultado] == ["grande", "pequena", "sem-acao"]


def test_gravidade_pesa_mais_que_o_ganho():
    def grande_info(ctx):
        return [Recomendacao("info", "T", INFO, "e", ("v",), rec.AcaoSugerida("x", "r", {}, 1, GB))]

    def pequena_atencao(ctx):
        return [Recomendacao("atencao", "T", ATENCAO, "e", ("v",))]

    resultado = rec.gerar(contexto(), recomendadores=(grande_info, pequena_atencao))

    assert [r.id for r in resultado] == ["atencao", "info"]


def test_gerar_sem_nada_a_dizer_devolve_lista_vazia():
    assert rec.gerar(contexto()) == []


def test_um_recomendador_que_falha_no_sistema_e_ignorado_e_os_outros_seguem(caplog):
    def quebrado(ctx):
        raise psutil.AccessDenied()

    def sem_disco(ctx):
        raise OSError("sem disco")

    def bom(ctx):
        return [Recomendacao("x", "T", INFO, "e", ("v",))]

    with caplog.at_level("WARNING", logger="zelador"):
        resultado = rec.gerar(contexto(), recomendadores=(quebrado, sem_disco, bom))

    assert [r.id for r in resultado] == ["x"]
    assert "quebrado" in caplog.text and "sem_disco" in caplog.text


def test_defeito_de_programacao_nao_e_escondido():
    def com_bug(ctx):
        raise KeyError("bug nosso")

    with pytest.raises(KeyError):
        rec.gerar(contexto(), recomendadores=(com_bug,))


def _contexto_cheio(pasta):
    return contexto(pasta, discos=[disco(raiz_de(pasta), percentual=99.0)],
                    simular=Simulador(parados=Resumo(aplicados=5, bytes=GB),
                                      pdfs=Resumo(aplicados=6, bytes=MB)))


def test_todas_as_recomendacoes_viram_json_valido(pasta):
    texto = json.dumps(rec.para_dicts(rec.gerar(_contexto_cheio(pasta))), allow_nan=False)

    dados = json.loads(texto)
    assert len(dados) == 3  # disco, downloads e pdfs
    assert {d["gravidade"] for d in dados} <= {INFO, ATENCAO, IMPORTANTE}
    assert all(set(d) == {"id", "titulo", "gravidade", "explicacao", "evidencias", "acao"}
               for d in dados)
    assert all(isinstance(d["evidencias"], list) and d["evidencias"] for d in dados)


def test_ids_de_acao_sao_um_contrato_com_a_camada_de_acoes():
    """Se mudar, a camada de ações (e o HTML) precisam mudar junto."""
    assert rec.LIMPAR_DOWNLOADS == "limpar_downloads"
    assert rec.ORGANIZAR_PDFS == "organizar_pdfs"


def test_toda_recomendacao_tem_evidencia_e_texto_sem_linguagem_de_alarme(pasta):
    for r in rec.gerar(_contexto_cheio(pasta)):
        assert r.evidencias and r.explicacao
        texto = (r.titulo + r.explicacao).lower()
        for palavra in ("vírus", "perigo", "urgente", "risco", "crítico", "!"):
            assert palavra not in texto, f"{r.id} usa linguagem de alarme: {palavra}"


# --------------------------- com o motor e arquivos de verdade (sem simulador)


def criar(pasta, nome, dias, tamanho=10):
    arquivo = pasta / nome
    arquivo.write_bytes(b"x" * tamanho)
    t = time.time() - dias * DIA
    os.utime(arquivo, (t, t))
    return arquivo


@pytest.fixture
def so_data_de_modificacao(monkeypatch):
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


def _contexto_real(pasta, downloads):
    return Contexto(discos=[],
                    pasta_downloads=downloads, pasta_pdfs=pasta / "Docs" / "PDFs")


def test_estimativas_reais_contam_sem_alterar_nada(pasta, so_data_de_modificacao, caplog):
    downloads = pasta / "Downloads"
    velhos = [criar(downloads, f"velho{i}.bin", 40, tamanho=MB) for i in range(3)]
    pdfs = [criar(downloads, f"doc{i}.pdf", 5, tamanho=2 * MB) for i in range(6)]
    novo = criar(downloads, "novo.txt", 0)
    ctx = _contexto_real(pasta, downloads)

    with caplog.at_level("INFO", logger="zelador"):
        parados = ctx.downloads_parados(30)
        soltos = ctx.pdfs_soltos()

    assert (parados.aplicados, parados.bytes) == (3, 3 * MB)  # só os de 40 dias; PDFs de 5 não
    assert all(a.exists() for a in velhos + pdfs + [novo])  # nada foi tocado
    assert (soltos.aplicados, soltos.bytes) == (6, 12 * MB)
    assert not (pasta / "Docs").exists()  # nem o destino foi criado
    assert "deletado(a)" not in caplog.text and "movido(a)" not in caplog.text


def test_gerar_de_ponta_a_ponta_com_arquivos_reais(pasta, so_data_de_modificacao):
    downloads = pasta / "Downloads"
    for i in range(3):
        criar(downloads, f"instalador{i}.bin", 60, tamanho=30 * MB)
    for i in range(6):
        criar(downloads, f"doc{i}.pdf", 3, tamanho=MB)

    recomendacoes = rec.gerar(_contexto_real(pasta, downloads))

    assert [r.id for r in recomendacoes] == ["downloads-parados", "pdfs-soltos"]
    assert recomendacoes[0].acao.bytes == 90 * MB
    assert recomendacoes[1].acao.itens == 6


# ------------------------------------ máquina de verdade (sem simulação)


def test_coletar_contexto_e_gerar_na_maquina_real():
    ctx = rec.coletar_contexto()

    resultado = rec.gerar(ctx)

    assert len(ctx.discos) >= 1
    json.dumps(rec.para_dicts(resultado), allow_nan=False)
    assert all(isinstance(r, Recomendacao) for r in resultado)


def test_coletar_contexto_sem_pasta_downloads_nao_quebra(tmp_path):
    ctx = rec.coletar_contexto(pasta_downloads=tmp_path / "nao_existe")

    assert ctx.pasta_downloads is None
    assert rec.downloads_parados(ctx) == [] and rec.pdfs_soltos(ctx) == []


def test_destino_padrao_dos_pdfs_e_a_pasta_documentos_de_verdade(tmp_path, monkeypatch):
    documentos = tmp_path / "OneDrive" / "Documentos"
    monkeypatch.setattr(rec, "obter_pasta_documentos", lambda: documentos)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "casa")

    ctx = rec.coletar_contexto(pasta_downloads=tmp_path / "nao_existe")

    assert ctx.pasta_pdfs == documentos / "PDFs"
    assert ctx.pasta_pdfs != tmp_path / "casa" / "Documents" / "PDFs"  # o antigo (errado)


def test_destino_dos_pdfs_informado_tem_prioridade(tmp_path):
    ctx = rec.coletar_contexto(pasta_downloads=tmp_path / "nao_existe",
                               pasta_pdfs=tmp_path / "meus_pdfs")

    assert ctx.pasta_pdfs == tmp_path / "meus_pdfs"
