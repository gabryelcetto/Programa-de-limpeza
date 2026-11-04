import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from zelador import historico

# importado por nome: a fixture do conftest troca o do módulo `caminhos` nos testes
from zelador.caminhos import arquivo_de_historico_padrao as padrao_real
from zelador.historico import Historico
from zelador.regras import Registro


class Relogio:
    def __init__(self, inicio=datetime(2026, 11, 4, 15, 30, 12)):
        self.agora = inicio

    def __call__(self):
        return self.agora

    def avancar(self, **tempo):
        self.agora += timedelta(**tempo)


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def hist(tmp_path, relogio):
    return Historico(tmp_path / "pasta" / "historico.jsonl", agora=relogio)


def registro(nome="a.pdf", tipo="mover", destino="docs", tamanho=10, eh_pasta=False,
             regra="Organizar"):
    origem = Path("C:\\downloads") / nome if False else Path("/downloads") / nome
    novo = Path(f"/{destino}") / nome if destino else None
    return Registro(regra, tipo, origem, novo, tamanho, eh_pasta)


def linhas(h):
    return [json.loads(x) for x in h.arquivo.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------ gravar e ler


def test_ida_e_volta_de_um_lote_com_varias_acoes(hist):
    gravar = hist.gravador("rodar")
    gravar(registro("a.pdf", tamanho=111))
    gravar(registro("b.txt", tipo="lixeira", destino=None, tamanho=22, regra="Limpar"))

    leitura = hist.ler()

    assert leitura.linhas_ignoradas == 0
    (lote,) = leitura.lotes
    assert lote.comando == "rodar" and lote.instante == datetime(2026, 11, 4, 15, 30, 12)
    a, b = lote.acoes
    assert (a.regra, a.tipo, a.origem, a.destino, a.tamanho, a.eh_pasta) == (
        "Organizar", "mover", Path("/downloads/a.pdf"), Path("/docs/a.pdf"), 111, False)
    assert (b.regra, b.tipo, b.origem, b.destino, b.tamanho) == (
        "Limpar", "lixeira", Path("/downloads/b.txt"), None, 22)
    assert not a.desfeita and a.restaurada_em is None


def test_ids_das_acoes_sao_unicos_e_ligados_ao_lote(hist):
    gravar = hist.gravador("limpar")
    for nome in ("a", "b", "c"):
        gravar(registro(nome))

    (lote,) = hist.ler().lotes

    assert len({a.id for a in lote.acoes}) == 3
    assert all(a.id.startswith(lote.id + "-") and a.lote == lote.id for a in lote.acoes)


def test_lote_so_aparece_quando_algo_foi_feito(hist):
    hist.gravador("rodar")  # criou o gravador, mas nenhum item foi tratado

    assert not hist.arquivo.exists()  # execução que não fez nada não deixa rastro
    assert hist.ler().lotes == []


def test_cada_execucao_e_um_lote_proprio_em_ordem(hist, relogio):
    hist.gravador("rodar")(registro("a"))
    relogio.avancar(minutes=5)
    hist.gravador("limpar")(registro("b"))

    lotes = hist.ler().lotes

    assert [l.comando for l in lotes] == ["rodar", "limpar"]  # noqa: E741
    assert lotes[0].id != lotes[1].id and lotes[0].instante < lotes[1].instante


def test_o_arquivo_tem_uma_linha_json_por_registro(hist):
    gravar = hist.gravador("rodar")
    gravar(registro("a"))
    gravar(registro("b"))

    dados = linhas(hist)

    assert [d["tipo"] for d in dados] == ["lote", "acao", "acao"]
    assert hist.arquivo.read_bytes().count(b"\r\n") == 0  # "\n" em qualquer sistema
    assert hist.arquivo.read_bytes().endswith(b"\n")


def test_acentos_e_nomes_exoticos_sobrevivem_em_utf8(hist):
    hist.gravador("rodar")(Registro("Relatórios do mês", "mover", Path("/d/ação é çã.pdf"),
                                    Path("/docs/ação é çã.pdf"), 1, False))

    (acao,) = hist.ler().lotes[0].acoes

    assert acao.regra == "Relatórios do mês" and acao.origem.name == "ação é çã.pdf"
    assert "ação" in hist.arquivo.read_text(encoding="utf-8")  # legível, sem \\u00e7


def test_cria_a_pasta_do_arquivo_sozinho(hist):
    assert not hist.arquivo.parent.exists()

    hist.gravador("rodar")(registro())

    assert hist.arquivo.exists()


def test_arquivo_inexistente_le_como_vazio(hist):
    leitura = hist.ler()

    assert leitura.lotes == [] and leitura.linhas_ignoradas == 0


# --------------------------------------------------------------- desfeito


def test_marcar_desfeito_aparece_na_proxima_leitura(hist, relogio):
    hist.gravador("rodar")(registro("a"))
    (acao,) = hist.ler().lotes[0].acoes
    relogio.avancar(hours=2)

    hist.marcar_desfeito(acao, Path("/downloads/a (1).pdf"))

    (relida,) = hist.ler().lotes[0].acoes
    assert relida.desfeita and relida.restaurada_em == Path("/downloads/a (1).pdf")
    assert linhas(hist)[-1]["instante"] == "2026-11-04T17:30:12"


def test_so_a_acao_marcada_fica_desfeita(hist):
    gravar = hist.gravador("rodar")
    gravar(registro("a"))
    gravar(registro("b"))
    primeira, segunda = hist.ler().lotes[0].acoes

    hist.marcar_desfeito(primeira, Path("/x"))

    a, b = hist.ler().lotes[0].acoes
    assert a.desfeita and not b.desfeita


def test_o_historico_nunca_reescreve_linhas_antigas(hist):
    hist.gravador("rodar")(registro("a"))
    antes = hist.arquivo.read_bytes()
    (acao,) = hist.ler().lotes[0].acoes

    hist.marcar_desfeito(acao, Path("/x"))

    assert hist.arquivo.read_bytes().startswith(antes)  # só acrescentou


# ------------------------------------------------- arquivo estragado


def test_linhas_ilegiveis_sao_ignoradas_e_contadas(hist):
    hist.gravador("rodar")(registro("a"))
    with open(hist.arquivo, "a", encoding="utf-8") as f:
        f.write("isto não é json\n")
        f.write("\n")  # linha em branco não conta como problema
        f.write('{"tipo":"acao","id":"x"}\n')  # faltam campos
        f.write('{"tipo":"coisa-nova","x":1}\n')  # tipo desconhecido
        f.write('[1, 2, 3]\n')  # JSON válido, mas não é um objeto
        f.write('{"tipo":"desfeito","acao":"nao-existe","instante":"x","restaurada_em":"y"}\n')

    leitura = hist.ler()

    assert len(leitura.lotes[0].acoes) == 1  # o que era bom continua lá
    assert leitura.linhas_ignoradas == 5


def test_ultima_linha_cortada_no_meio_nao_estraga_o_resto(hist):
    gravar = hist.gravador("rodar")
    gravar(registro("a"))
    gravar(registro("b"))
    conteudo = hist.arquivo.read_bytes()
    hist.arquivo.write_bytes(conteudo[:-40])  # o PC desligou no meio da última gravação

    leitura = hist.ler()

    assert [a.origem.name for a in leitura.lotes[0].acoes] == ["a"]  # a "b" foi cortada
    assert leitura.linhas_ignoradas == 1


def test_acao_cujo_lote_nao_existe_e_ignorada(hist):
    hist._anexar({"tipo": "acao", "id": "z-1", "lote": "z", "instante": "2026-11-04T10:00:00",
                  "regra": "r", "acao": "mover", "origem": "/a", "destino": "/b", "tamanho": 1,
                  "eh_pasta": False})

    leitura = hist.ler()

    assert leitura.lotes == [] and leitura.linhas_ignoradas == 1


def test_arquivo_com_bom_ou_bytes_invalidos_ainda_le(hist):
    hist.gravador("rodar")(registro("a"))
    bom = hist.arquivo.read_bytes()
    hist.arquivo.write_bytes(b"\xef\xbb\xbf" + bom + b"\xff\xfe lixo\n")  # BOM + bytes inválidos

    leitura = hist.ler()

    assert len(leitura.lotes[0].acoes) == 1 and leitura.linhas_ignoradas == 1


def test_arquivo_que_nao_pode_ser_lido_levanta_oserror(tmp_path, relogio):
    pasta_no_lugar = tmp_path / "historico.jsonl"
    pasta_no_lugar.mkdir()

    with pytest.raises(OSError):
        Historico(pasta_no_lugar, agora=relogio).ler()


# ------------------------------------------- falha ao gravar não derruba nada


def test_falha_de_disco_vira_aviso_unico_e_nao_levanta(tmp_path, relogio, caplog):
    bloqueio = tmp_path / "arquivo_no_lugar_da_pasta"
    bloqueio.write_text("x")
    h = Historico(bloqueio / "sub" / "historico.jsonl", agora=relogio)
    gravar = h.gravador("rodar")

    with caplog.at_level("WARNING", logger="zelador"):
        gravar(registro("a"))
        gravar(registro("b"))
        gravar(registro("c"))

    assert gravar.falhas == 3
    assert caplog.text.count("Não foi possível gravar no histórico") == 1  # avisa uma vez só
    assert "não poderá ser desfeito" in caplog.text


def test_depois_de_uma_falha_o_gravador_volta_a_gravar(tmp_path, relogio, monkeypatch):
    h = Historico(tmp_path / "h.jsonl", agora=relogio)
    gravar = h.gravador("rodar")
    original = h._anexar
    falhar = {"vez": True}

    def instavel(dados):
        if falhar["vez"]:
            falhar["vez"] = False
            raise PermissionError("arquivo em uso")
        original(dados)

    monkeypatch.setattr(h, "_anexar", instavel)

    gravar(registro("a"))  # falha (nem o cabeçalho do lote foi gravado)
    gravar(registro("b"))  # funciona

    (lote,) = h.ler().lotes
    assert [a.origem.name for a in lote.acoes] == ["b"] and gravar.falhas == 1


def test_lote_sem_acoes_gravadas_nao_aparece(hist):
    hist._anexar({"tipo": "lote", "id": "vazio", "instante": "2026-11-04T10:00:00",
                  "comando": "rodar"})  # o cabeçalho foi, as ações não

    assert hist.ler().lotes == []


def test_defeito_de_programacao_no_gravador_nao_e_escondido(hist, monkeypatch):
    def quebrado(dados):
        raise KeyError("bug nosso")

    monkeypatch.setattr(hist, "_anexar", quebrado)

    with pytest.raises(KeyError):  # só OSError (disco) é tratado
        hist.gravador("rodar")(registro())


# ----------------------------------------------------------------- poda


def _lote_em(hist, relogio, dias_atras, nome):
    relogio.agora = datetime(2026, 11, 4, 12, 0, 0) - timedelta(days=dias_atras)
    hist.gravador("rodar")(registro(nome))


def test_podar_descarta_so_os_lotes_antigos(hist, relogio):
    _lote_em(hist, relogio, 400, "velho")
    _lote_em(hist, relogio, 200, "medio")
    _lote_em(hist, relogio, 10, "novo")
    relogio.agora = datetime(2026, 11, 4, 12, 0, 0)

    descartados = hist.podar(dias=180)

    assert descartados == 2
    (lote,) = hist.ler().lotes
    assert lote.acoes[0].origem.name == "novo"


def test_podar_sem_nada_antigo_nao_toca_no_arquivo(hist, relogio):
    _lote_em(hist, relogio, 5, "novo")
    relogio.agora = datetime(2026, 11, 4, 12, 0, 0)
    antes = hist.arquivo.read_bytes()

    assert hist.podar(dias=180) == 0
    assert hist.arquivo.read_bytes() == antes


def test_podar_preserva_o_que_ja_foi_desfeito_nos_lotes_que_ficam(hist, relogio):
    _lote_em(hist, relogio, 5, "novo")
    (acao,) = hist.ler().lotes[0].acoes
    hist.marcar_desfeito(acao, Path("/restaurado"))
    _lote_em(hist, relogio, 400, "velho")
    relogio.agora = datetime(2026, 11, 4, 12, 0, 0)

    hist.podar(dias=180)

    (lote,) = hist.ler().lotes
    assert lote.acoes[0].desfeita and lote.acoes[0].restaurada_em == Path("/restaurado")


def test_poda_que_falha_deixa_o_original_intacto_e_sem_lixo(hist, relogio, monkeypatch):
    _lote_em(hist, relogio, 400, "velho")
    _lote_em(hist, relogio, 5, "novo")
    relogio.agora = datetime(2026, 11, 4, 12, 0, 0)
    antes = hist.arquivo.read_bytes()

    def falhar(origem, destino):
        raise PermissionError("em uso")

    monkeypatch.setattr(historico.os, "replace", falhar)

    with pytest.raises(OSError):
        hist.podar(dias=180)

    assert hist.arquivo.read_bytes() == antes
    assert not list(hist.arquivo.parent.glob("*.tmp"))


def test_poda_automatica_so_quando_o_arquivo_passa_do_limite(tmp_path, relogio, monkeypatch):
    h = Historico(tmp_path / "h.jsonl", agora=relogio)
    chamadas = []
    monkeypatch.setattr(h, "podar", lambda *a, **k: chamadas.append(1) or 0)
    h.gravador("rodar")(registro("a"))
    assert chamadas == []  # arquivo pequeno: nada de podar

    h.arquivo.write_bytes(b"x" * (historico.LIMITE_PARA_PODAR + 1))
    h.gravador("rodar")(registro("b"))

    assert chamadas == [1]


def test_falha_na_poda_automatica_nao_impede_de_gravar(tmp_path, relogio, monkeypatch):
    h = Historico(tmp_path / "h.jsonl", agora=relogio)
    h.arquivo.write_bytes(b"x" * (historico.LIMITE_PARA_PODAR + 1))

    def falhar(*a, **k):
        raise PermissionError("em uso")

    monkeypatch.setattr(h, "podar", falhar)
    gravar = h.gravador("rodar")

    gravar(registro("a"))

    assert gravar.falhas == 0 and h.arquivo.read_bytes().count(b'"tipo":"acao"') == 1


# ---------------------------------------------------------- caminho padrão


def test_historico_padrao_fica_em_localappdata_ao_lado_dos_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert padrao_real() == tmp_path / "Zelador" / "historico.jsonl"


def test_historico_padrao_sem_localappdata(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    esperado = tmp_path / ".local" / "share" / "zelador" / "historico.jsonl"
    assert padrao_real() == esperado
