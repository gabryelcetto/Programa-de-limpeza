"""Leitura e validação do arquivo de regras (YAML).

O arquivo decide o que será apagado ou movido, então é tratado como entrada não confiável:
- só YAML "seguro" (nunca executa código) e sem chaves repetidas;
- chaves desconhecidas são erro (um erro de digitação em `idade_dias` deixaria a regra mais
  ampla do que o autor quis);
- cada regra é validada sozinha: uma regra inválida é descartada com um aviso e as demais
  continuam valendo.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import caminhos
from .regras import Apagar, ExtensaoEm, IdadeMinima, Mover, NomeCorresponde, Regra, TamanhoEntre

VERSAO = 1
_CHAVES_DO_ARQUIVO = ("versao", "regras")
_CHAVES_DA_REGRA = ("nome", "pasta", "condicoes", "acao")
_CONDICOES = ("idade_dias", "extensoes", "tamanho_min", "tamanho_max", "nome")
_UNIDADES = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_TAMANHO = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB)?$", re.IGNORECASE)
_VARIAVEL_NAO_EXPANDIDA = re.compile(r"%\w[^%]*%|\$\{?\w")


class ErroConfiguracao(Exception):
    """O arquivo não pôde ser lido ou não tem o formato esperado: nenhuma regra foi carregada."""


class _ErroValidacao(Exception):
    """Problema em uma regra específica."""


@dataclass(frozen=True)
class ErroRegra:
    posicao: int  # a partir de 1, na ordem do arquivo
    nome: str
    mensagem: str

    def __str__(self) -> str:
        return f"regra {self.posicao} ('{self.nome}'): {self.mensagem}"


@dataclass
class Carga:
    regras: list = field(default_factory=list)
    erros: list = field(default_factory=list)


# ------------------------------------------------------------------ yaml


class _LeitorEstrito(yaml.SafeLoader):
    """SafeLoader que recusa chaves repetidas (o PyYAML ficaria com a última, em silêncio)."""


def _mapa_sem_chaves_repetidas(leitor, no, deep=False):
    vistas = set()
    for no_chave, _ in no.value:
        chave = leitor.construct_object(no_chave, deep=deep)
        try:
            repetida = chave in vistas
            vistas.add(chave)
        except TypeError:
            continue  # chave que não é hashable: o próprio construct_mapping reclama
        if repetida:
            raise yaml.constructor.ConstructorError(
                None, None, f"a chave '{chave}' aparece mais de uma vez", no_chave.start_mark)
    return yaml.SafeLoader.construct_mapping(leitor, no, deep)


_LeitorEstrito.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                               _mapa_sem_chaves_repetidas)


def _descrever_erro_yaml(erro: yaml.YAMLError) -> str:
    problema = getattr(erro, "problem", None) or str(erro)
    marca = getattr(erro, "problem_mark", None)
    inicio = getattr(erro, "context_mark", None)
    onde = ""
    if marca:
        onde = f" (linha {marca.line + 1}, coluna {marca.column + 1}"
        if inicio and inicio.line != marca.line:
            onde += f"; o trecho começa na linha {inicio.line + 1}"
        onde += ")"
    dica = ""
    if "escape" in problema:
        dica = (" Dica: caminhos do Windows com barra invertida ficam melhores sem aspas "
                "ou entre aspas simples.")
    return f"YAML inválido{onde}: {problema}.{dica}"


# --------------------------------------------------------------- arquivo


def carregar_regras(caminho) -> Carga:
    """Lê o arquivo. Levanta ErroConfiguracao se ele não puder ser usado."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ErroConfiguracao(f"arquivo não encontrado: {caminho}") from None
    except (OSError, UnicodeDecodeError) as erro:
        raise ErroConfiguracao(f"não foi possível ler {caminho}: {erro}") from None
    return carregar_regras_de_texto(texto)


def carregar_regras_de_texto(texto: str) -> Carga:
    try:
        dados = yaml.load(texto, Loader=_LeitorEstrito)  # noqa: S506 - subclasse do SafeLoader
    except yaml.YAMLError as erro:
        raise ErroConfiguracao(_descrever_erro_yaml(erro)) from None

    if dados is None:  # arquivo vazio ou só com comentários
        dados = {}
    if not isinstance(dados, dict):
        raise ErroConfiguracao("o arquivo deve ter as chaves 'versao' e 'regras'")

    desconhecidas = [str(c) for c in dados if c not in _CHAVES_DO_ARQUIVO]
    if desconhecidas:
        raise ErroConfiguracao(f"chave desconhecida no arquivo: {', '.join(desconhecidas)} "
                               f"(use: {', '.join(_CHAVES_DO_ARQUIVO)})")

    versao = dados.get("versao", VERSAO)
    if isinstance(versao, bool) or versao != VERSAO:
        raise ErroConfiguracao(f"versão do arquivo não suportada: {versao!r} (esta é a {VERSAO})")

    itens = dados.get("regras")
    if itens is None:
        itens = []
    if not isinstance(itens, list):
        raise ErroConfiguracao("'regras' deve ser uma lista (cada regra começa com '- nome:')")

    carga = Carga()
    nomes = set()
    for posicao, item in enumerate(itens, start=1):
        nome = item.get("nome") if isinstance(item, dict) else None
        rotulo = nome if isinstance(nome, str) and nome.strip() else "?"
        try:
            regra = _converter_regra(item)
            if regra.nome.casefold() in nomes:
                raise _ErroValidacao(f"já existe outra regra chamada '{regra.nome}'")
            nomes.add(regra.nome.casefold())
        except (_ErroValidacao, ValueError) as erro:
            carga.erros.append(ErroRegra(posicao, rotulo, str(erro)))
        else:
            carga.regras.append(regra)
    return carga


# ----------------------------------------------------------------- regra


def _converter_regra(item) -> Regra:
    if not isinstance(item, dict):
        raise _ErroValidacao("cada regra deve ser um conjunto de campos (nome, pasta, "
                             "condicoes, acao)")
    _recusar_chaves_desconhecidas(item, _CHAVES_DA_REGRA, "na regra")

    nome = item.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _ErroValidacao("'nome' é obrigatório e deve ser um texto")
    if "pasta" not in item:
        raise _ErroValidacao("'pasta' é obrigatória")
    if "condicoes" not in item:
        raise _ErroValidacao("'condicoes' é obrigatória: sem condição, a regra valeria para "
                             "tudo da pasta")
    if "acao" not in item:
        raise _ErroValidacao("'acao' é obrigatória (apagar ou mover)")

    return Regra(nome.strip(), _caminho(item["pasta"], "pasta"),
                 _condicoes(item["condicoes"]), _acao(item["acao"]))


def _recusar_chaves_desconhecidas(mapa: dict, validas: tuple, onde: str) -> None:
    desconhecidas = [str(c) for c in mapa if c not in validas]
    if desconhecidas:
        raise _ErroValidacao(f"campo desconhecido {onde}: {', '.join(desconhecidas)} "
                             f"(válidos: {', '.join(validas)})")


def _caminho(valor, campo: str) -> Path:
    """Aceita ~, variáveis de ambiente (${VAR} ou %VAR%) e {downloads}. Exige caminho absoluto,
    para a regra nunca depender da pasta de onde o programa foi aberto."""
    if not isinstance(valor, str) or not valor.strip():
        raise _ErroValidacao(f"'{campo}' deve ser um caminho (texto)")
    texto = valor.strip()
    if texto.startswith("{downloads}"):
        texto = str(caminhos.obter_pasta_downloads()) + texto[len("{downloads}"):]
    texto = os.path.expandvars(os.path.expanduser(texto))
    if _VARIAVEL_NAO_EXPANDIDA.search(texto):
        raise _ErroValidacao(f"'{campo}': variável de ambiente indefinida em '{valor}'")
    caminho = Path(texto)
    if not caminho.is_absolute():
        raise _ErroValidacao(f"'{campo}' deve ser um caminho absoluto (use ~, {{downloads}} ou "
                             f"uma variável de ambiente): '{valor}'")
    return caminho


# ------------------------------------------------------------- condições


def _condicoes(dados) -> list:
    if not isinstance(dados, dict) or not dados:
        raise _ErroValidacao("'condicoes' deve ter ao menos uma condição "
                             f"({', '.join(_CONDICOES)})")
    _recusar_chaves_desconhecidas(dados, _CONDICOES, "em 'condicoes'")

    condicoes = []
    if "idade_dias" in dados:
        dias = dados["idade_dias"]
        if isinstance(dias, bool) or not isinstance(dias, int) or dias < 1:
            raise _ErroValidacao("'idade_dias' deve ser um número inteiro maior ou igual a 1")
        condicoes.append(IdadeMinima(dias))
    if "extensoes" in dados:
        condicoes.append(ExtensaoEm(_extensoes(dados["extensoes"])))
    if "tamanho_min" in dados or "tamanho_max" in dados:
        condicoes.append(TamanhoEntre(minimo=_limite(dados, "tamanho_min"),
                                      maximo=_limite(dados, "tamanho_max")))
    if "nome" in dados:
        if not isinstance(dados["nome"], str) or not dados["nome"]:
            raise _ErroValidacao("'nome' (em 'condicoes') deve ser uma expressão regular (texto)")
        condicoes.append(NomeCorresponde(dados["nome"]))
    return condicoes


def _extensoes(valor) -> tuple:
    itens = [valor] if isinstance(valor, str) else valor
    if not isinstance(itens, list) or not itens:
        raise _ErroValidacao("'extensoes' deve ser uma lista de textos, como [pdf, docx]")
    for item in itens:
        if not isinstance(item, str):
            raise _ErroValidacao(
                f"a extensão {item!r} não é texto: em YAML, no/yes/on/off e números sem aspas "
                "viram outro tipo; escreva entre aspas (ex.: \"no\")")
    return tuple(itens)


def _limite(dados: dict, campo: str):
    return _tamanho(dados[campo], campo) if campo in dados else None


def _tamanho(valor, campo: str) -> int:
    """Bytes (número) ou texto com unidade: '500KB', '50MB', '1.5GB' (base 1024)."""
    if isinstance(valor, bool) or isinstance(valor, float):
        raise _ErroValidacao(f"'{campo}' deve ser bytes (inteiro) ou texto com unidade, "
                             "como '50MB' ou '1.5GB'")
    if isinstance(valor, int):
        if valor < 0:
            raise _ErroValidacao(f"'{campo}' não pode ser negativo")
        return valor
    correspondencia = _TAMANHO.match(valor.strip()) if isinstance(valor, str) else None
    if not correspondencia:
        raise _ErroValidacao(f"'{campo}' inválido: {valor!r} (exemplos: 500KB, 50MB, 1.5GB)")
    numero = float(correspondencia.group(1).replace(",", "."))
    return int(round(numero * _UNIDADES[(correspondencia.group(2) or "B").upper()]))


# ------------------------------------------------------------------ ação


def _acao(dados):
    if dados == "apagar":
        return Apagar()
    if isinstance(dados, dict) and set(dados) == {"mover"}:
        return Mover(_caminho(dados["mover"], "acao.mover"))
    raise _ErroValidacao("'acao' inválida: use 'apagar' ou 'mover: <pasta de destino>'")
