from pathlib import Path
from textwrap import dedent

import pytest

from zelador import caminhos
from zelador.configuracao import (
    ErroConfiguracao,
    ErroRegra,
    carregar_regras,
    carregar_regras_de_texto,
)
from zelador.regras import (
    Apagar,
    EnviarParaLixeira,
    ExtensaoEm,
    IdadeMinima,
    Mover,
    NomeCorresponde,
    TamanhoEntre,
)

MB = 1024**2


@pytest.fixture(autouse=True)
def downloads_falso(tmp_path, monkeypatch):
    """{downloads} aponta para uma pasta de teste, nunca para a do usuário."""
    pasta = tmp_path / "Downloads"
    monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: pasta)
    return pasta


def carregar(texto):
    return carregar_regras_de_texto(dedent(texto))


def regra_com(campos: str) -> str:
    """Uma regra válida em que `campos` substitui/acrescenta campos (já indentados)."""
    return f"""
    regras:
      - nome: Teste
        pasta: "{{downloads}}"
        condicoes:
          idade_dias: 7
        acao: apagar
{campos}
    """


# ---------------------------------------------------------- caso feliz


def test_regra_completa_vira_objetos_do_motor(downloads_falso):
    carga = carregar("""
        versao: 1
        regras:
          - nome: Organizar PDFs
            pasta: "{downloads}"
            condicoes:
              idade_dias: 3
              extensoes: [pdf, DOCX]
              tamanho_min: 1KB
              tamanho_max: 50MB
              nome: "^relat"
            acao:
              mover: "{downloads}/PDFs"
    """)

    assert carga.erros == []
    (regra,) = carga.regras
    assert regra.nome == "Organizar PDFs"
    assert regra.pasta == downloads_falso
    assert regra.condicoes == (
        IdadeMinima(3),
        ExtensaoEm(("pdf", "docx")),
        TamanhoEntre(minimo=1024, maximo=50 * MB),
        NomeCorresponde("^relat"),
    )
    assert regra.acao == Mover(downloads_falso / "PDFs")


def test_apagar_e_varias_regras_na_ordem_do_arquivo():
    carga = carregar("""
        regras:
          - nome: B
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
          - nome: A
            pasta: "{downloads}"
            condicoes: {idade_dias: 30}
            acao: apagar
    """)

    assert [r.nome for r in carga.regras] == ["B", "A"]
    assert carga.regras[0].acao == Apagar()


def test_acao_lixeira_e_acao_apagar_sao_coisas_diferentes():
    lixeira = carregar(regra_com("").replace("acao: apagar", "acao: lixeira"))
    apagar = carregar(regra_com(""))

    assert lixeira.erros == [] and lixeira.regras[0].acao == EnviarParaLixeira()
    assert apagar.regras[0].acao == Apagar()  # `apagar` continua apagando de vez, como antes


@pytest.mark.parametrize("valor", [
    "Lixeira", "LIXEIRA", "lixo", "trash", "'lixeira '", "[lixeira]",
])
def test_acao_lixeira_com_outra_grafia_e_recusada(valor):
    mensagem = invalida(regra_yaml(acao=f"acao: {valor}"))

    assert "'acao' inválida" in mensagem and "'lixeira'" in mensagem  # a mensagem ensina o certo


def test_versao_pode_ser_omitida():
    assert carregar(regra_com("")).erros == []


@pytest.mark.parametrize("texto", [
    "", "# só comentário\n", "regras:\n", "regras: []\n", "versao: 1\n",
])
def test_arquivo_sem_regras_e_valido(texto):
    carga = carregar_regras_de_texto(texto)

    assert carga.regras == [] and carga.erros == []


# --------------------------------------------- erros do arquivo inteiro


@pytest.mark.parametrize("texto, trecho", [
    ("- uma\n- lista\n", "'versao' e 'regras'"),
    ("apenas texto\n", "'versao' e 'regras'"),
    ("regra: []\n", "chave desconhecida no arquivo: regra"),
    ("versao: 2\nregras: []\n", "versão do arquivo não suportada"),
    ("versao: true\nregras: []\n", "versão do arquivo não suportada"),
    ("regras: texto\n", "'regras' deve ser uma lista"),
    ("regras:\n  nome: x\n", "'regras' deve ser uma lista"),
])
def test_arquivo_invalido_nao_carrega_nada(texto, trecho):
    with pytest.raises(ErroConfiguracao, match=trecho):
        carregar_regras_de_texto(texto)


def test_yaml_com_erro_de_sintaxe_informa_a_linha():
    with pytest.raises(ErroConfiguracao, match=r"linha 4.*começa na linha 3"):
        carregar_regras_de_texto("regras:\n  - nome: x\n    pasta: [aberta\n")


def test_chave_repetida_e_recusada_e_informa_a_linha():
    texto = dedent("""
        regras:
          - nome: Teste
            pasta: "{downloads}"
            condicoes:
              idade_dias: 30
              idade_dias: 1
            acao: apagar
    """)

    with pytest.raises(ErroConfiguracao, match=r"linha 7.*'idade_dias' aparece mais de uma vez"):
        carregar_regras_de_texto(texto)


def test_aspas_duplas_com_barra_invertida_dao_dica():
    with pytest.raises(ErroConfiguracao, match="aspas simples"):
        carregar_regras_de_texto('regras:\n  - pasta: "C:\\Users\\ana"\n')


def test_yaml_nao_executa_codigo():
    malicioso = "regras: !!python/object/apply:os.system ['echo executou > nunca.txt']\n"

    with pytest.raises(ErroConfiguracao):
        carregar_regras_de_texto(malicioso)

    assert not Path("nunca.txt").exists()


# -------------------------------------------------- regra a regra


def test_regra_invalida_e_descartada_e_as_outras_continuam():
    carga = carregar("""
        regras:
          - nome: Boa 1
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
          - nome: Ruim
            pasta: "{downloads}"
            condicoes: {idade_dia: 7}
            acao: apagar
          - nome: Boa 2
            pasta: "{downloads}"
            condicoes: {idade_dias: 9}
            acao: apagar
    """)

    assert [r.nome for r in carga.regras] == ["Boa 1", "Boa 2"]
    (erro,) = carga.erros
    assert (erro.posicao, erro.nome) == (2, "Ruim")
    assert "idade_dia" in erro.mensagem


def test_item_que_nao_e_regra_e_um_erro_com_posicao():
    carga = carregar("""
        regras:
          - nome: Boa
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
          - so um texto
          - 42
    """)

    assert len(carga.regras) == 1
    assert [(e.posicao, e.nome) for e in carga.erros] == [(2, "?"), (3, "?")]


def test_nomes_repetidos_sem_diferenciar_maiusculas():
    carga = carregar("""
        regras:
          - nome: Limpar
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
          - nome: LIMPAR
            pasta: "{downloads}"
            condicoes: {idade_dias: 9}
            acao: apagar
    """)

    assert [r.nome for r in carga.regras] == ["Limpar"]
    assert "já existe outra regra" in carga.erros[0].mensagem


def test_erro_regra_formata_posicao_e_nome():
    assert str(ErroRegra(2, "Ruim", "algo errado")) == "regra 2 ('Ruim'): algo errado"


def invalida(campos_da_regra: str):
    """Carrega uma única regra e devolve a mensagem do (único) erro."""
    linhas = campos_da_regra.splitlines()
    texto = "\n".join(["regras:", "  - " + linhas[0]] + ["    " + linha for linha in linhas[1:]])
    carga = carregar_regras_de_texto(texto)
    assert carga.regras == [], "a regra inválida foi aceita!"
    assert len(carga.erros) == 1
    return carga.erros[0].mensagem


def regra_yaml(**partes):
    base = {"nome": "nome: Teste",
            "pasta": 'pasta: "{downloads}"',
            "condicoes": "condicoes: {idade_dias: 7}",
            "acao": "acao: apagar"}
    base.update(partes)
    return "\n".join(v for v in base.values() if v is not None)


@pytest.mark.parametrize("partes, trecho", [
    ({"nome": None}, "'nome' é obrigatório"),
    ({"nome": 'nome: ""'}, "'nome' é obrigatório"),
    ({"nome": "nome: no"}, "'nome' é obrigatório"),
    ({"pasta": None}, "'pasta' é obrigatória"),
    ({"pasta": "pasta: 42"}, "'pasta' deve ser um caminho"),
    ({"pasta": "pasta: relativa/sub"}, "caminho absoluto"),
    ({"pasta": "pasta: ${VARIAVEL_QUE_NAO_EXISTE_ZEL}/x"}, "variável de ambiente indefinida"),
    ({"condicoes": None}, "'condicoes' é obrigatória"),
    ({"condicoes": "condicoes: {}"}, "ao menos uma condição"),
    ({"condicoes": "condicoes: [idade_dias]"}, "ao menos uma condição"),
    ({"condicoes": "condicoes: {idade_dia: 7}"}, "campo desconhecido em 'condicoes': idade_dia"),
    ({"acao": None}, "'acao' é obrigatória"),
    ({"acao": "acao: deletar"}, "'acao' inválida"),
    ({"acao": "acao: {mover: }"}, "'acao.mover' deve ser um caminho"),
    ({"acao": "acao: {mover: relativa}"}, "caminho absoluto"),
    ({"acao": "acao: {mover: '{downloads}/x', apagar: sim}"}, "'acao' inválida"),
    ({"acao": "acao: {apagar: sim}"}, "'acao' inválida"),
    ({"acao": "acao: [apagar]"}, "'acao' inválida"),
    ({"extra": "descricao: algo"}, "campo desconhecido na regra: descricao"),
])
def test_campos_da_regra_invalidos(partes, trecho):
    assert trecho in invalida(regra_yaml(**partes))


@pytest.mark.parametrize("valor", ["0", "-1", "'7'", "7.5", "true", "[7]", "null"])
def test_idade_dias_invalida(valor):
    mensagem = invalida(regra_yaml(condicoes=f"condicoes: {{idade_dias: {valor}}}"))

    assert "'idade_dias' deve ser um número inteiro maior ou igual a 1" in mensagem


@pytest.mark.parametrize("valor", ["[]", "[no]", "[1]", "['']", "[pdf, '']", "{}", "42", "''"])
def test_extensoes_invalidas(valor):
    mensagem = invalida(regra_yaml(condicoes=f"condicoes: {{extensoes: {valor}}}"))

    assert "extens" in mensagem


def test_extensao_no_sem_aspas_explica_o_problema_do_yaml():
    mensagem = invalida(regra_yaml(condicoes="condicoes: {extensoes: [no]}"))

    assert "entre aspas" in mensagem


def test_extensao_no_com_aspas_funciona():
    carga = carregar(regra_com("").replace("idade_dias: 7", "extensoes: ['no']"))

    assert carga.regras[0].condicoes == (ExtensaoEm(("no",)),)


def test_extensoes_aceita_um_unico_texto():
    carga = carregar(regra_com("").replace("idade_dias: 7", "extensoes: pdf"))

    assert carga.regras[0].condicoes == (ExtensaoEm(("pdf",)),)


@pytest.mark.parametrize("campo", ["tamanho_min", "tamanho_max"])
@pytest.mark.parametrize("valor", ["abc", "'50 XB'", "-1", "'-5MB'", "1.5", "true", "[1]", "''"])
def test_tamanho_invalido(campo, valor):
    mensagem = invalida(regra_yaml(condicoes=f"condicoes: {{{campo}: {valor}}}"))

    assert campo in mensagem


def test_tamanho_minimo_maior_que_o_maximo():
    mensagem = invalida(regra_yaml(condicoes="condicoes: {tamanho_min: 2MB, tamanho_max: 1MB}"))

    assert "mínimo não pode ser maior" in mensagem


@pytest.mark.parametrize("texto, esperado", [
    ("50MB", 50 * MB),
    ("50mb", 50 * MB),
    ("50 MB", 50 * MB),
    ("1.5GB", int(1.5 * 1024**3)),
    ("1,5 GB", int(1.5 * 1024**3)),
    ("10KB", 10 * 1024),
    ("2TB", 2 * 1024**4),
    ("100B", 100),
    ("100", 100),
    ("0", 0),
])
def test_tamanho_com_unidade(texto, esperado):
    carga = carregar(regra_com("").replace("idade_dias: 7", f"tamanho_max: '{texto}'"))

    assert carga.regras[0].condicoes == (TamanhoEntre(maximo=esperado),)


def test_tamanho_em_bytes_como_numero():
    carga = carregar(regra_com("").replace("idade_dias: 7", "tamanho_min: 2048"))

    assert carga.regras[0].condicoes == (TamanhoEntre(minimo=2048),)


def test_nome_com_regex_invalida():
    mensagem = invalida(regra_yaml(condicoes='condicoes: {nome: "(abc"}'))

    assert "expressão regular inválida" in mensagem


@pytest.mark.parametrize("valor", ["42", "''", "[a]"])
def test_nome_que_nao_e_texto(valor):
    mensagem = invalida(regra_yaml(condicoes=f"condicoes: {{nome: {valor}}}"))

    assert "expressão regular" in mensagem


# --------------------------------------------------------- caminhos


def test_downloads_com_subpasta(downloads_falso):
    carga = carregar(regra_com("").replace('"{downloads}"', '"{downloads}/sub/pasta"'))

    assert carga.regras[0].pasta == downloads_falso / "sub" / "pasta"


def test_til_expande_para_a_pasta_pessoal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    carga = carregar(regra_com("").replace('"{downloads}"', "~/Meus Arquivos"))

    assert carga.regras[0].pasta == tmp_path / "Meus Arquivos"


def test_variavel_de_ambiente_expande(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEL_BASE", str(tmp_path))

    carga = carregar(regra_com("").replace('"{downloads}"', '"${ZEL_BASE}/dados"'))

    assert carga.regras[0].pasta == tmp_path / "dados"


def test_caminho_absoluto_literal(tmp_path):
    carga = carregar(regra_com("").replace('"{downloads}"', f"'{tmp_path}'"))

    assert carga.regras[0].pasta == tmp_path


# ---------------------------------------------------------- arquivo


def test_carregar_regras_le_do_disco(tmp_path):
    arquivo = tmp_path / "regras.yml"
    arquivo.write_text(dedent(regra_com("")), encoding="utf-8")

    carga = carregar_regras(arquivo)

    assert [r.nome for r in carga.regras] == ["Teste"]


def test_arquivo_com_bom_do_bloco_de_notas_funciona(tmp_path):
    arquivo = tmp_path / "regras.yml"
    arquivo.write_bytes(b"\xef\xbb\xbf" + dedent(regra_com("")).encode("utf-8"))

    assert len(carregar_regras(arquivo).regras) == 1


def test_arquivo_com_acentos_em_utf8(tmp_path):
    arquivo = tmp_path / "regras.yml"
    texto = dedent(regra_com("")).replace("nome: Teste", "nome: Relatórios antigos")
    arquivo.write_text(texto, encoding="utf-8")

    assert carregar_regras(arquivo).regras[0].nome == "Relatórios antigos"


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(ErroConfiguracao, match="arquivo não encontrado"):
        carregar_regras(tmp_path / "nao_existe.yml")


def test_caminho_que_e_uma_pasta(tmp_path):
    with pytest.raises(ErroConfiguracao, match="não foi possível ler"):
        carregar_regras(tmp_path)


def test_arquivo_que_nao_e_utf8(tmp_path):
    arquivo = tmp_path / "regras.yml"
    arquivo.write_bytes("nome: Relatórios".encode("latin-1"))

    with pytest.raises(ErroConfiguracao, match="não foi possível ler"):
        carregar_regras(arquivo)


def test_pasta_relativa_e_rejeitada_mesmo_que_exista_no_diretorio_atual(tmp_path, monkeypatch):
    (tmp_path / "downloads_rel").mkdir()
    monkeypatch.chdir(tmp_path)

    mensagem = invalida(regra_yaml(pasta="pasta: downloads_rel"))

    assert "caminho absoluto" in mensagem
