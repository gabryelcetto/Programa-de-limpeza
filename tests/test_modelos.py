import pytest
import yaml

from zelador import caminhos, modelos
from zelador.configuracao import carregar_regras_de_texto
from zelador.modelos import MODELOS, gerar_yaml
from zelador.regras import Apagar, EnviarParaLixeira, ExtensaoEm, Mover


@pytest.fixture(autouse=True)
def pastas_falsas(tmp_path, monkeypatch):
    """Os atalhos {downloads}, {documentos} e {imagens} apontam para pastas de teste."""
    monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(caminhos, "obter_pasta_documentos", lambda: tmp_path / "Documentos")
    monkeypatch.setattr(caminhos, "obter_pasta_imagens", lambda: tmp_path / "Imagens")


# ---------------------------------------------------- o catálogo de modelos


def test_ids_e_nomes_de_regra_sao_unicos():
    ids = [m.id for m in MODELOS]
    nomes = [yaml.safe_load("regras:\n" + m.regra)["regras"][0]["nome"] for m in MODELOS]

    assert len(set(ids)) == len(ids)
    assert len(set(nomes)) == len(nomes)  # senão duas regras com o mesmo nome se recusariam


def test_cada_modelo_sozinho_gera_exatamente_uma_regra_valida():
    for modelo in MODELOS:
        carga = carregar_regras_de_texto(gerar_yaml([modelo.id]))

        assert carga.erros == [], modelo.id
        assert len(carga.regras) == 1, modelo.id


def test_todos_os_modelos_juntos_sao_validos():
    carga = carregar_regras_de_texto(gerar_yaml([m.id for m in MODELOS]))

    assert carga.erros == []
    assert len(carga.regras) == len(MODELOS)


def test_nenhum_modelo_apaga_de_vez():
    """Invariante de segurança: um modelo só move ou manda para a Lixeira (as duas têm volta)."""
    carga = carregar_regras_de_texto(gerar_yaml([m.id for m in MODELOS]))

    for regra in carga.regras:
        assert isinstance(regra.acao, (Mover, EnviarParaLixeira)), regra.nome
        assert not isinstance(regra.acao, Apagar), regra.nome


def test_todos_os_modelos_atuam_so_na_pasta_downloads(tmp_path):
    carga = carregar_regras_de_texto(gerar_yaml([m.id for m in MODELOS]))

    assert {r.pasta for r in carga.regras} == {tmp_path / "Downloads"}


def test_destinos_usam_as_pastas_reais_e_nunca_o_til():
    for modelo in MODELOS:
        assert "~" not in modelo.regra, modelo.id  # ~/Documents pode ser outra pasta
        if "mover:" in modelo.regra:
            assert "{documentos}" in modelo.regra or "{imagens}" in modelo.regra, modelo.id


def test_destino_dos_modelos_que_movem_fica_na_pasta_certa(tmp_path):
    carga = carregar_regras_de_texto(gerar_yaml(["organizar-pdfs", "organizar-imagens",
                                                 "organizar-documentos"]))
    destinos = {r.nome: r.acao.destino for r in carga.regras}

    assert destinos["Organizar PDFs"] == tmp_path / "Documentos" / "PDFs"
    assert destinos["Organizar imagens"] == tmp_path / "Imagens" / "Downloads"
    assert destinos["Organizar documentos do Office"] == tmp_path / "Documentos" / "Baixados"


def test_as_extensoes_de_cada_modelo_nao_se_sobrepoem():
    """Duas regras que movem o mesmo tipo brigariam: a segunda nunca veria o arquivo."""
    carga = carregar_regras_de_texto(gerar_yaml(["organizar-pdfs", "organizar-imagens",
                                                 "organizar-documentos"]))
    conjuntos = [set(c.extensoes) for r in carga.regras for c in r.condicoes
                 if isinstance(c, ExtensaoEm)]
    assert len(conjuntos) == 3

    for i, a in enumerate(conjuntos):
        for b in conjuntos[i + 1:]:
            assert a.isdisjoint(b)


# ------------------------------------------------------------ gerar_yaml


def test_sem_escolha_usa_os_modelos_recomendados():
    carga = carregar_regras_de_texto(gerar_yaml())
    esperados = {m.titulo for m in MODELOS if m.padrao}

    assert {r.nome for r in carga.regras} == {
        "Organizar PDFs", "Limpar instaladores antigos", "Limpar downloads parados"}
    assert esperados == {"Organizar PDFs", "Limpar instaladores antigos",
                         "Limpar downloads parados"}


def test_a_ordem_do_arquivo_nao_depende_da_ordem_pedida():
    a = carregar_regras_de_texto(gerar_yaml(["limpar-downloads-parados", "organizar-pdfs"]))
    b = carregar_regras_de_texto(gerar_yaml(["organizar-pdfs", "limpar-downloads-parados"]))

    assert [r.nome for r in a.regras] == [r.nome for r in b.regras]
    assert [r.nome for r in a.regras] == ["Organizar PDFs", "Limpar downloads parados"]


def test_quem_organiza_roda_antes_de_quem_limpa():
    carga = carregar_regras_de_texto(gerar_yaml([m.id for m in MODELOS]))
    tipos = [isinstance(r.acao, Mover) for r in carga.regras]

    assert tipos == sorted(tipos, reverse=True)  # todos os "mover" (True) antes dos "lixeira"
    assert carga.regras[-1].nome == "Limpar downloads parados"  # a rede de segurança é a última


def test_modelo_repetido_entra_uma_vez_so():
    texto = gerar_yaml(["organizar-pdfs", "organizar-pdfs"])
    carga = carregar_regras_de_texto(texto)

    # olha o TEXTO, e não só o resultado do carregador (que descartaria a duplicata sozinho)
    assert texto.count("- nome: Organizar PDFs") == 1
    assert carga.erros == [] and [r.nome for r in carga.regras] == ["Organizar PDFs"]


def test_modelo_desconhecido_lista_os_validos():
    with pytest.raises(ValueError) as erro:
        gerar_yaml(["organizar-pdfs", "nao-existe"])

    mensagem = str(erro.value)
    assert "nao-existe" in mensagem and "organizar-pdfs" in mensagem  # diz o errado e os certos


def test_lista_vazia_de_modelos_e_recusada():
    with pytest.raises(ValueError, match="ao menos um"):
        gerar_yaml([])


def test_o_arquivo_gerado_e_yaml_valido_com_cabecalho_e_comentarios():
    texto = gerar_yaml()

    dados = yaml.safe_load(texto)
    assert dados["versao"] == 1 and len(dados["regras"]) == 3
    assert texto.startswith("# Regras do Zelador")
    assert "zelador rodar --dry-run" in texto  # ensina o passo seguro
    assert "\t" not in texto  # tabulação quebraria o YAML
    assert texto.endswith("\n")


def test_cada_regra_vem_com_um_comentario_explicando():
    for modelo in MODELOS:
        primeira = modelo.regra.lstrip().splitlines()[0]
        assert primeira.startswith("#"), f"{modelo.id} não começa com um comentário"


def test_ids_devolve_todos_na_ordem_do_catalogo():
    assert modelos.ids() == tuple(m.id for m in MODELOS)
    assert "organizar-pdfs" in modelos.ids()


def test_a_regra_que_limpa_de_vez_nao_e_oferecida_como_modelo():
    assert not any("acao: apagar" in m.regra for m in MODELOS)
