"""Desfazer: devolve ao lugar de origem o que uma execução do Zelador moveu.

Só o que foi MOVIDO pode ser desfeito pelo Zelador. O que foi para a Lixeira se restaura pelo
Explorer (o Zelador não tem como pegar de volta de lá), e o que foi apagado de vez não volta.

O histórico é um arquivo de texto que qualquer um pode editar, então nada nele é obedecido às
cegas: antes de mover, confere-se que o item ainda está onde o histórico diz, que é do mesmo tipo
e que nem ele nem o lugar de volta são pastas protegidas do sistema.
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .arquivos import restaurar_item
from .historico import Acao, Historico, Leitura, Lote
from .seguranca import motivo_pasta_protegida, pasta_do_sistema_que_contem

logger = logging.getLogger("zelador")


class Estado(Enum):
    DESFEITO = "desfeito"  # voltou ao lugar (ou voltaria, na simulação)
    JA_DESFEITO = "já desfeito antes"
    NAO_DESFAZIVEL = "não dá para desfazer"  # lixeira / apagar / ação personalizada
    NAO_ENCONTRADO = "não encontrado"  # o item não está mais onde o histórico diz
    RECUSADO = "recusado"  # algo não bate ou é protegido: não foi tocado
    ERRO = "erro"  # o sistema não deixou mover


@dataclass
class ResultadoItem:
    acao: Acao
    estado: Estado
    detalhe: str = ""
    restaurado_em: Optional[Path] = None


def desfazivel(acao: Acao) -> bool:
    return acao.tipo == "mover"


def lote_desfazivel(lote: Lote) -> bool:
    """Tem ao menos uma ação movida que ainda não foi desfeita."""
    return any(desfazivel(a) and not a.desfeita for a in lote.acoes)


def ultimo_lote_desfazivel(leitura: Leitura) -> Optional[Lote]:
    for lote in reversed(leitura.lotes):
        if lote_desfazivel(lote):
            return lote
    return None


def achar_lote(leitura: Leitura, id_: str) -> Optional[Lote]:
    return next((lote for lote in leitura.lotes if lote.id == id_), None)


def _eh_pasta_de_verdade(caminho: Path) -> bool:
    return caminho.is_dir() and not caminho.is_symlink()


def _por_que_recusar(acao: Acao) -> Optional[str]:
    """Confere o que o histórico afirma contra o que existe de fato. None se está tudo certo."""
    destino = acao.destino
    if pasta_do_sistema_que_contem(destino):
        return "o item está numa pasta do sistema"
    if motivo_pasta_protegida(destino):
        return "o item é uma pasta protegida"
    volta = motivo_pasta_protegida(acao.origem.parent)
    if volta:
        return f"o lugar de volta é {volta}"
    if _eh_pasta_de_verdade(destino) != acao.eh_pasta:
        return "o item agora é de outro tipo (arquivo/pasta) do que quando foi movido"
    return None


def _desfazer_acao(historico: Historico, acao: Acao, dry_run: bool) -> ResultadoItem:
    if acao.desfeita:
        return ResultadoItem(acao, Estado.JA_DESFEITO, f"já voltou para {acao.restaurada_em}")
    if not desfazivel(acao):
        motivo = {"lixeira": "está na Lixeira: restaure pelo Explorer",
                  "apagar": "foi apagado de vez"}.get(acao.tipo, "esta ação não é reversível")
        return ResultadoItem(acao, Estado.NAO_DESFAZIVEL, motivo)
    if acao.destino is None or not os.path.lexists(acao.destino):
        return ResultadoItem(acao, Estado.NAO_ENCONTRADO,
                             f"não está mais em {acao.destino}")
    recusa = _por_que_recusar(acao)
    if recusa:
        return ResultadoItem(acao, Estado.RECUSADO, recusa)
    if dry_run:
        return ResultadoItem(acao, Estado.DESFEITO, "simulação", acao.origem)

    try:
        novo = restaurar_item(acao.destino, acao.origem)
    except OSError as erro:
        return ResultadoItem(acao, Estado.ERRO, str(erro))

    detalhe = ""
    if novo != acao.origem:
        detalhe = "o lugar original estava ocupado: voltou com outro nome"
    try:
        historico.marcar_desfeito(acao, novo)
    except OSError as erro:  # o item voltou; só o registro ficou para trás
        logger.warning("Voltou, mas não foi possível registrar no histórico: %s", erro)
    return ResultadoItem(acao, Estado.DESFEITO, detalhe, novo)


def desfazer_lote(historico: Historico, lote: Lote, dry_run: bool = False) -> list:
    """Desfaz as ações do lote, da última para a primeira (como uma pilha). Um problema em um
    item não impede os demais. Devolve um ResultadoItem por ação, nessa mesma ordem."""
    return [_desfazer_acao(historico, acao, dry_run) for acao in reversed(lote.acoes)]
