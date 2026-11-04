"""Recomendações: sugestões explicáveis a partir de como o PC está. Nunca agem sozinhas.

Cada recomendação traz a EVIDÊNCIA (os números que a motivaram), uma explicação sem alarme e,
quando existe, uma ação que o painel pode oferecer (sempre começando por "ver o que seria feito").
Para somar uma recomendação nova basta escrever uma função `(Contexto) -> list[Recomendacao]`
e acrescentá-la a RECOMENDADORES.

O ganho estimado vem de uma SIMULAÇÃO do motor de regras (dry-run) sobre a pasta de verdade:
não é um palpite.
"""

import dataclasses
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import psutil

from .. import discos as _discos
from ..arquivos import formatar_tamanho
from ..caminhos import obter_pasta_downloads
from ..regras import Apagar, ExtensaoEm, IdadeMinima, Mover, Regra, Resumo, executar_regra

logger = logging.getLogger("zelador")

# Ações que o painel sabe oferecer (a camada de ações as implementa com estes mesmos ids)
LIMPAR_DOWNLOADS = "limpar_downloads"
ORGANIZAR_PDFS = "organizar_pdfs"

INFO, ATENCAO, IMPORTANTE = "info", "atencao", "importante"
_ORDEM = {IMPORTANTE: 0, ATENCAO: 1, INFO: 2}

GB = 1024**3
MB = 1024**2
DISCO_ATENCAO = 85.0  # % usado
DISCO_IMPORTANTE = 95.0
DISCO_TAMANHO_MINIMO = 10 * GB  # partições de recuperação são pequenas e vivem quase cheias
DOWNLOADS_DIAS = 30
DOWNLOADS_BYTES_MINIMOS = 50 * MB  # abaixo disso não vale incomodar
PDFS_MINIMO = 5
PDFS_DIAS = 1


# ----------------------------------------------------------------- tipos


@dataclass(frozen=True)
class AcaoSugerida:
    id: str  # um dos ids acima
    rotulo: str
    parametros: dict
    itens: Optional[int] = None  # estimativa, vinda de uma simulação
    bytes: Optional[int] = None


@dataclass(frozen=True)
class Recomendacao:
    id: str  # estável entre atualizações, para a tela não "piscar"
    titulo: str
    gravidade: str
    explicacao: str
    evidencias: tuple
    acao: Optional[AcaoSugerida] = None


def _simular(regra: Regra) -> Resumo:
    return executar_regra(regra, dry_run=True, detalhar=False)


@dataclass
class Contexto:
    """Tudo que os recomendadores precisam saber, lido uma vez."""

    discos: Sequence
    pasta_downloads: Optional[Path] = None
    pasta_pdfs: Optional[Path] = None  # destino sugerido para organizar PDFs
    simular: Callable[[Regra], Resumo] = _simular
    _estimativas: dict = field(default_factory=dict, repr=False)

    def _estimar(self, chave, montar_regra) -> Optional[Resumo]:
        """Simula uma vez só por chave (várias recomendações podem precisar da mesma conta)."""
        if self.pasta_downloads is None:
            return None
        if chave not in self._estimativas:
            try:
                self._estimativas[chave] = self.simular(montar_regra())
            except ValueError:  # pasta protegida: não há o que sugerir
                self._estimativas[chave] = None
        return self._estimativas[chave]

    def downloads_parados(self, dias: int = DOWNLOADS_DIAS) -> Optional[Resumo]:
        return self._estimar(("parados", dias), lambda: Regra(
            "estimativa: downloads parados", self.pasta_downloads, [IdadeMinima(dias)], Apagar()))

    def pdfs_soltos(self) -> Optional[Resumo]:
        if self.pasta_pdfs is None:
            return None
        return self._estimar(("pdfs", str(self.pasta_pdfs)), lambda: Regra(
            "estimativa: pdfs soltos", self.pasta_downloads,
            [ExtensaoEm(("pdf",)), IdadeMinima(PDFS_DIAS)], Mover(self.pasta_pdfs)))


# --------------------------------------------------------------- auxiliares


def _disco_de(caminho: Path, discos: Sequence):
    """O disco (ponto de montagem mais específico) em que o caminho está."""
    alvo = os.path.normcase(os.path.abspath(caminho))
    melhor = None
    for disco in discos:
        ponto = os.path.normcase(os.path.abspath(disco.ponto_de_montagem))
        base = ponto.rstrip(os.sep) + os.sep
        if (alvo == ponto or alvo.startswith(base)) and (
                melhor is None or len(ponto) > len(melhor[0])):
            melhor = (ponto, disco)
    return melhor[1] if melhor else None




# ------------------------------------------------------------ recomendadores


def disco_cheio(ctx: Contexto) -> list:
    resultado = []
    downloads = ctx.downloads_parados()
    disco_dos_downloads = (_disco_de(ctx.pasta_downloads, ctx.discos)
                           if ctx.pasta_downloads else None)
    for disco in ctx.discos:
        if disco.total < DISCO_TAMANHO_MINIMO or disco.percentual < DISCO_ATENCAO:
            continue
        acao = None
        if (disco is disco_dos_downloads and downloads
                and downloads.bytes >= DOWNLOADS_BYTES_MINIMOS):
            acao = _acao_limpar_downloads(downloads)
        resultado.append(Recomendacao(
            id=f"disco-cheio:{disco.ponto_de_montagem}",
            titulo=f"O disco {disco.ponto_de_montagem} está ficando cheio",
            gravidade=IMPORTANTE if disco.percentual >= DISCO_IMPORTANTE else ATENCAO,
            explicacao=("Com pouco espaço livre o Windows pode ficar mais lento e deixar de "
                        "instalar atualizações. Apagar o que você não usa mais ajuda."),
            evidencias=(f"{disco.ponto_de_montagem} está a {disco.percentual:.0f}% "
                        f"({formatar_tamanho(disco.usado)} de {formatar_tamanho(disco.total)}; "
                        f"sobram {formatar_tamanho(disco.total - disco.usado)})",),
            acao=acao,
        ))
    return resultado


def _acao_limpar_downloads(resumo: Resumo) -> AcaoSugerida:
    return AcaoSugerida(LIMPAR_DOWNLOADS, "Ver o que seria apagado", {"dias": DOWNLOADS_DIAS},
                        resumo.aplicados, resumo.bytes)


def downloads_parados(ctx: Contexto) -> list:
    resumo = ctx.downloads_parados()
    if resumo is None or resumo.bytes < DOWNLOADS_BYTES_MINIMOS:
        return []
    return [Recomendacao(
        id="downloads-parados",
        titulo="Há arquivos parados na pasta Downloads",
        gravidade=INFO,
        explicacao=("São itens baixados que ninguém mexe há mais de um mês. Nada é apagado antes "
                    "de você ver a lista e confirmar."),
        evidencias=(f"{resumo.aplicados} itens ({formatar_tamanho(resumo.bytes)}) sem atividade "
                    f"há mais de {DOWNLOADS_DIAS} dias em {ctx.pasta_downloads}",),
        acao=_acao_limpar_downloads(resumo),
    )]


def pdfs_soltos(ctx: Contexto) -> list:
    resumo = ctx.pdfs_soltos()
    if resumo is None or resumo.aplicados < PDFS_MINIMO:
        return []
    return [Recomendacao(
        id="pdfs-soltos",
        titulo="Há PDFs soltos na pasta Downloads",
        gravidade=INFO,
        explicacao=("Eles ficam misturados com o resto dos downloads. Mover para uma pasta "
                    "própria não apaga nada: se o nome já existir lá, o novo vira 'nome (1).pdf'."),
        evidencias=(f"{resumo.aplicados} PDFs ({formatar_tamanho(resumo.bytes)}) com mais de "
                    f"{PDFS_DIAS} dia em {ctx.pasta_downloads}",),
        acao=AcaoSugerida(ORGANIZAR_PDFS, "Ver o que seria movido",
                          {"destino": str(ctx.pasta_pdfs)}, resumo.aplicados, resumo.bytes),
    )]


RECOMENDADORES = (disco_cheio, downloads_parados, pdfs_soltos)


# ------------------------------------------------------------------ público


def gerar(contexto: Contexto, recomendadores: Optional[Sequence] = None) -> list:
    """As recomendações, das mais importantes para as menos (e, em empate, as de maior ganho).
    Um recomendador que o sistema não deixa executar é ignorado; os outros continuam."""
    resultado = []
    for recomendador in recomendadores or RECOMENDADORES:
        try:
            resultado.extend(recomendador(contexto))
        except (OSError, psutil.Error) as erro:
            logger.warning("Recomendação '%s' ignorada: %s", recomendador.__name__, erro)
    resultado.sort(key=lambda r: (_ORDEM[r.gravidade], -((r.acao.bytes or 0) if r.acao else 0)))
    return resultado


def coletar_contexto(pasta_downloads: Optional[Path] = None,
                     pasta_pdfs: Optional[Path] = None) -> Contexto:
    """Lê o estado real da máquina: os discos e a pasta Downloads."""
    downloads = pasta_downloads or obter_pasta_downloads()
    return Contexto(
        discos=_discos.coletar_discos(),
        pasta_downloads=downloads if downloads.is_dir() else None,
        pasta_pdfs=pasta_pdfs or Path.home() / "Documents" / "PDFs",
    )


def para_dicts(recomendacoes: Sequence) -> list:
    return [dataclasses.asdict(r) for r in recomendacoes]
