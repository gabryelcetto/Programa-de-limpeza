"""Motor de regras: onde olhar (pasta), quais itens escolher (condições) e o que fazer (ação).

Uma regra só vale se TODAS as suas condições forem verdadeiras para o item. Os itens são os
que estão diretamente na pasta; uma subpasta é tratada como um item único (sua atividade e
seu tamanho consideram tudo que há dentro dela).
"""

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, ClassVar, Optional, Protocol

from . import arquivos as _arquivos
from .arquivos import deletar_item, formatar_tamanho, medir_item, mover_item
from .logs import logger
from .seguranca import motivo_pasta_protegida, pasta_do_sistema_que_contem

# ------------------------------------------------------------------ item


@dataclass(frozen=True)
class Item:
    caminho: Path
    eh_pasta: bool
    ultima_atividade: float  # timestamp mais recente do item (e de tudo dentro dele)
    tamanho: int  # bytes

    @property
    def nome(self) -> str:
        return self.caminho.name

    @property
    def extensao(self) -> str:
        """Em minúsculas e sem o ponto ('' para pastas e arquivos sem extensão).
        Só vale a última: 'a.tar.gz' tem extensão 'gz'."""
        return "" if self.eh_pasta else self.caminho.suffix.lstrip(".").lower()


# ------------------------------------------------------------- condições


@dataclass(frozen=True)
class IdadeMinima:
    """Sem atividade há MAIS de `dias` dias."""

    dias: int

    def __post_init__(self):
        if self.dias < 1:
            raise ValueError("a idade mínima é de 1 dia (com 0, tudo seria considerado antigo)")

    def corresponde(self, item: Item, agora: datetime) -> bool:
        try:
            momento = datetime.fromtimestamp(item.ultima_atividade)
        except (OverflowError, OSError, ValueError):
            # data que o sistema guarda mas o Python não representa (ex.: ano 10000, que o NTFS
            # aceita): no futuro nunca é "antigo"; no passado distante, é
            return item.ultima_atividade < agora.timestamp()
        return agora - momento > timedelta(days=self.dias)

    def resumo(self) -> str:
        return f"sem atividade há mais de {self.dias} dia(s)"


@dataclass(frozen=True)
class ExtensaoEm:
    """Arquivo cuja extensão está na lista (com ou sem ponto, sem diferenciar maiúsculas).
    Pastas nunca correspondem."""

    extensoes: tuple

    def __post_init__(self):
        normalizadas = tuple(e.lstrip(".").lower() for e in self.extensoes)
        if not normalizadas or not all(normalizadas):
            raise ValueError("informe ao menos uma extensão, e nenhuma vazia")
        object.__setattr__(self, "extensoes", normalizadas)

    def corresponde(self, item: Item, agora: datetime) -> bool:
        return item.extensao in self.extensoes

    def resumo(self) -> str:
        return "extensão: " + ", ".join(self.extensoes)


@dataclass(frozen=True)
class TamanhoEntre:
    """Tamanho em bytes dentro dos limites (inclusivos). Use só um deles para 'no mínimo'
    ou 'no máximo'."""

    minimo: Optional[int] = None
    maximo: Optional[int] = None

    def __post_init__(self):
        if self.minimo is None and self.maximo is None:
            raise ValueError("informe o tamanho mínimo, o máximo, ou os dois")
        if self.minimo is not None and self.maximo is not None and self.minimo > self.maximo:
            raise ValueError("o tamanho mínimo não pode ser maior que o máximo")

    def corresponde(self, item: Item, agora: datetime) -> bool:
        return ((self.minimo is None or item.tamanho >= self.minimo)
                and (self.maximo is None or item.tamanho <= self.maximo))

    def resumo(self) -> str:
        if self.minimo is not None and self.maximo is not None:
            return (f"tamanho entre {formatar_tamanho(self.minimo)} "
                    f"e {formatar_tamanho(self.maximo)}")
        if self.minimo is not None:
            return f"tamanho de pelo menos {formatar_tamanho(self.minimo)}"
        return f"tamanho de no máximo {formatar_tamanho(self.maximo)}"


@dataclass(frozen=True)
class NomeCorresponde:
    """O nome (com extensão) contém um trecho que casa com a expressão regular,
    sem diferenciar maiúsculas. Para casar o nome inteiro, use ^ e $."""

    padrao: str
    _regex: "re.Pattern" = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        try:
            object.__setattr__(self, "_regex", re.compile(self.padrao, re.IGNORECASE))
        except re.error as erro:
            raise ValueError(f"expressão regular inválida '{self.padrao}': {erro}") from None

    def corresponde(self, item: Item, agora: datetime) -> bool:
        return self._regex.search(item.nome) is not None

    def resumo(self) -> str:
        return f"nome casa com /{self.padrao}/"


# ----------------------------------------------------------------- ações


class Acao(Protocol):
    def descricao(self) -> str:
        """Texto para o log: 'deletado(a)', 'movido(a) para X'..."""

    def resumo(self) -> str:
        """Texto para listar a regra: 'apagar', 'mover para X'..."""

    def aplicar(self, item: Item) -> Optional[Path]:
        """Executa a ação. Devolve o novo lugar do item, se ele foi para algum (mover), senão
        None. Levanta OSError em caso de falha."""

    def ocupa(self, caminho: Path) -> bool:
        """True se o caminho é (ou contém) o destino da ação: o item deve ser deixado em paz."""


@dataclass(frozen=True)
class Apagar:
    tipo: ClassVar[str] = "apagar"  # como o histórico identifica a ação

    def descricao(self) -> str:
        return "deletado(a)"

    def resumo(self) -> str:
        return "apagar"

    def aplicar(self, item: Item) -> Optional[Path]:
        deletar_item(item.caminho)
        return None

    def ocupa(self, caminho: Path) -> bool:
        return False


@dataclass(frozen=True)
class EnviarParaLixeira:
    """Como `Apagar`, mas o item vai para a Lixeira do sistema e dá para restaurá-lo."""

    tipo: ClassVar[str] = "lixeira"

    def descricao(self) -> str:
        return "enviado(a) para a Lixeira"

    def resumo(self) -> str:
        return "enviar para a Lixeira"

    def aplicar(self, item: Item) -> Optional[Path]:
        # pelo módulo (e não importando o nome): quem testa troca num ponto só
        _arquivos.enviar_para_lixeira(item.caminho)
        return None

    def ocupa(self, caminho: Path) -> bool:
        return False


def _normalizar(caminho: Path) -> str:
    """Caminho absoluto comparável, sem seguir atalhos e sem diferenciar maiúsculas no Windows."""
    return os.path.normcase(os.path.abspath(caminho))


def _mesmo_lugar(a: Path, b: Path) -> bool:
    """Os dois caminhos levam à mesma pasta, mesmo escritos de jeitos diferentes ('x/../y', barra
    no fim, maiúsculas no Windows, atalho de pasta)."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


@dataclass(frozen=True)
class Mover:
    destino: Path

    tipo: ClassVar[str] = "mover"

    def __post_init__(self):
        pasta = pasta_do_sistema_que_contem(self.destino)
        if pasta:
            raise ValueError(f"não é permitido mover arquivos para dentro de uma pasta do "
                             f"sistema ({pasta})")

    def descricao(self) -> str:
        return f"movido(a) para {self.destino}"

    def resumo(self) -> str:
        return f"mover para {self.destino}"

    def aplicar(self, item: Item) -> Optional[Path]:
        return mover_item(item.caminho, self.destino)  # o lugar final: pode ter virado "nome (1)"

    def ocupa(self, caminho: Path) -> bool:
        alvo, destino = _normalizar(caminho), _normalizar(self.destino)
        return destino == alvo or destino.startswith(alvo + os.sep)


# ----------------------------------------------------------------- regra


@dataclass
class Regra:
    nome: str
    pasta: Path
    condicoes: Sequence
    acao: Acao

    def __post_init__(self):
        motivo = motivo_pasta_protegida(self.pasta)
        if motivo:
            raise ValueError(f"a regra '{self.nome}' não pode atuar em {self.pasta}: "
                             f"é {motivo}")
        destino = getattr(self.acao, "destino", None)
        if destino is not None and _mesmo_lugar(destino, self.pasta):
            # cada execução "moveria" o item para onde ele já está, renomeando: a.pdf, a (1).pdf...
            raise ValueError(f"a regra '{self.nome}' move os itens para a própria pasta deles "
                             f"({self.pasta}); escolha outro destino")
        self.condicoes = tuple(self.condicoes)
        if not self.condicoes:
            # sem condição, a regra valeria para tudo da pasta (e Apagar esvaziaria a pasta)
            raise ValueError(f"a regra '{self.nome}' precisa de ao menos uma condição")


@dataclass
class Resumo:
    aplicados: int = 0
    ignorados: int = 0
    erros: int = 0
    bytes: int = 0


@dataclass(frozen=True)
class Registro:
    """O que foi feito com um item: o que o histórico guarda (e o desfazer usa)."""

    regra: str
    tipo: str  # "apagar", "lixeira", "mover" (ou "outra", para uma ação personalizada)
    origem: Path  # sempre absoluta
    destino: Optional[Path]  # onde ficou, se a ação move; já com o "(1)" se houve colisão
    tamanho: int
    eh_pasta: bool


def executar_regra(regra: Regra, dry_run: bool = False, agora: Optional[datetime] = None,
                   detalhar: bool = True,
                   registrar: Optional[Callable[[Registro], None]] = None,
                   observar: Optional[Callable[[Item], None]] = None) -> Resumo:
    """Aplica a regra aos itens da pasta. Um erro em um item não interrompe os demais.

    `detalhar=False` não registra uma linha de log por item (para simulações só de estimativa,
    repetidas, como as do painel); erros continuam sendo registrados.

    `registrar`, se informado, é chamado a cada item aplicado DE VERDADE (nunca na simulação),
    depois de a ação dar certo. Ele não deve levantar por problemas de disco: o que ele levantar
    não é tratado como erro do item e interrompe a regra.

    `observar`, se informado, recebe cada item que casou com a regra, TAMBÉM na simulação (é
    como o painel monta a lista do que seria feito)."""
    resumo = Resumo()
    agora = agora or datetime.now()
    prefixo = "[SIMULAÇÃO] " if dry_run else ""
    logger.debug("Regra '%s' em %s", regra.nome, regra.pasta)

    for caminho in sorted(regra.pasta.iterdir()):
        eh_pasta = caminho.is_dir() and not caminho.is_symlink()
        try:
            if regra.acao.ocupa(caminho):
                resumo.ignorados += 1
                logger.debug("Mantido (é o destino da ação): %s", caminho.name)
                continue

            medicao = medir_item(caminho)
            item = Item(caminho, eh_pasta, medicao.ultima_atividade, medicao.tamanho)
            if not all(c.corresponde(item, agora) for c in regra.condicoes):
                resumo.ignorados += 1
                logger.debug("Mantido (não casa com a regra): %s", caminho.name)
                continue

            novo_lugar = None if dry_run else regra.acao.aplicar(item)
        except OSError as erro:
            resumo.erros += 1
            logger.error("Erro em %s: %s", caminho.name, erro)
            continue

        resumo.aplicados += 1
        resumo.bytes += item.tamanho
        if observar is not None:
            observar(item)
        if registrar is not None and not dry_run:
            registrar(Registro(regra.nome, getattr(regra.acao, "tipo", "outra"),
                               Path(os.path.abspath(caminho)),
                               Path(os.path.abspath(novo_lugar)) if novo_lugar else None,
                               item.tamanho, eh_pasta))
        if detalhar:
            logger.info("%s%s %s: %s (%s)", prefixo, "Pasta" if eh_pasta else "Arquivo",
                        regra.acao.descricao(), caminho.name, formatar_tamanho(item.tamanho))

    return resumo
