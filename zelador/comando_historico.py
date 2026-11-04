"""Comandos `historico` e `desfazer`.

Códigos de saída: 0 tudo certo (ou nada a fazer); 1 algum item não pôde ser desfeito ou o
histórico não pôde ser lido; 2 pedido inválido (lote que não existe).
"""

import argparse
import sys
from collections import Counter

from . import caminhos, desfazer
from .arquivos import formatar_tamanho
from .desfazer import Estado
from .historico import Historico, Lote
from .logs import logger
from .opcoes import adicionar_dry_run, adicionar_historico

_VERBOS = {"mover": "movido(s)", "lixeira": "enviado(s) para a Lixeira", "apagar": "apagado(s)",
           "outra": "tratado(s)"}


def abrir_historico(args: argparse.Namespace) -> Historico:
    return Historico(args.historico or caminhos.arquivo_de_historico_padrao())


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    historico = subparsers.add_parser(
        "historico", parents=[comum],
        help="mostra o que o Zelador fez nas últimas execuções",
        description="Lista as execuções registradas (a mais recente primeiro). Não altera nada.",
    )
    adicionar_historico(historico)
    historico.add_argument("--ultimos", type=int, default=10, metavar="N",
                           help="quantas execuções mostrar (padrão: %(default)s)")
    historico.add_argument("--lote", default=None, metavar="ID",
                           help="mostra, item por item, o que a execução ID fez")
    historico.set_defaults(executar=executar_historico)

    desfazer_ = subparsers.add_parser(
        "desfazer", parents=[comum],
        help="devolve aos lugares de origem os arquivos que uma execução moveu",
        description="Desfaz a última execução que moveu arquivos (ou a indicada em --lote). "
                    "Nunca sobrescreve: se o lugar original estiver ocupado, o arquivo volta com "
                    "outro nome. O que foi para a Lixeira se restaura pelo Explorer, e o que foi "
                    "apagado de vez não volta.",
    )
    adicionar_historico(desfazer_)
    adicionar_dry_run(desfazer_)
    desfazer_.add_argument("--lote", default=None, metavar="ID",
                           help="execução a desfazer (veja os IDs com o comando historico)")
    desfazer_.set_defaults(executar=executar_desfazer)


def _ler(args: argparse.Namespace):
    """(historico, leitura), ou (historico, None) depois de registrar o erro."""
    historico = abrir_historico(args)
    try:
        leitura = historico.ler()
    except OSError as erro:
        logger.error("Não foi possível ler o histórico (%s): %s", historico.arquivo, erro)
        return historico, None
    if leitura.linhas_ignoradas:
        logger.warning("%d linha(s) do histórico estavam ilegíveis e foram ignoradas.",
                       leitura.linhas_ignoradas)
    return historico, leitura


def _resumo_do_lote(lote: Lote) -> str:
    contagem = Counter(a.tipo for a in lote.acoes)
    partes = [f"{n} {_VERBOS.get(tipo, tipo)}" for tipo, n in contagem.items()]
    texto = ", ".join(partes)
    desfeitas = sum(1 for a in lote.acoes if a.desfeita)
    if desfeitas:
        texto += f" [{desfeitas} desfeito(s)]"
    return texto


# -------------------------------------------------------------- historico


def executar_historico(args: argparse.Namespace) -> int:
    historico, leitura = _ler(args)
    if leitura is None:
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # nomes com caracteres que o console não tem

    if args.lote:
        lote = desfazer.achar_lote(leitura, args.lote)
        if lote is None:
            logger.error("Não há execução com o ID '%s' no histórico.", args.lote)
            return 2
        _imprimir_lote(lote)
        return 0

    if not leitura.lotes:
        print(f"Nada registrado ainda ({historico.arquivo}).")
        return 0
    for lote in reversed(leitura.lotes[-max(args.ultimos, 1):]):
        print(f"{lote.id}  {lote.instante:%d/%m/%Y %H:%M}  {lote.comando}: "
              f"{_resumo_do_lote(lote)}")
    print()
    print("Para ver os itens de uma execução: zelador historico --lote ID")
    return 0


def _imprimir_lote(lote: Lote) -> None:
    print(f"Execução {lote.id} ({lote.comando}, {lote.instante:%d/%m/%Y %H:%M})")
    for acao in lote.acoes:
        marca = " [desfeito]" if acao.desfeita else ""
        destino = f" -> {acao.destino}" if acao.destino else ""
        print(f"  {_VERBOS.get(acao.tipo, acao.tipo)}: {acao.origem}{destino} "
              f"({formatar_tamanho(acao.tamanho)}, regra '{acao.regra}'){marca}")


# --------------------------------------------------------------- desfazer


def executar_desfazer(args: argparse.Namespace) -> int:
    historico, leitura = _ler(args)
    if leitura is None:
        return 1

    if args.lote:
        lote = desfazer.achar_lote(leitura, args.lote)
        if lote is None:
            logger.error("Não há execução com o ID '%s' no histórico.", args.lote)
            return 2
    else:
        lote = desfazer.ultimo_lote_desfazivel(leitura)
        if lote is None:
            logger.info("Nada para desfazer: nenhuma execução registrada moveu arquivos "
                        "que ainda não tenham voltado.")
            return 0

    logger.info("Desfazendo a execução %s (%s, %s)%s.", lote.id, lote.comando,
                f"{lote.instante:%d/%m/%Y %H:%M}", " (SIMULAÇÃO)" if args.dry_run else "")
    resultados = desfazer.desfazer_lote(historico, lote, args.dry_run)

    for r in resultados:
        _relatar(r)

    contagem = Counter(r.estado for r in resultados)
    voltaram = contagem[Estado.DESFEITO]
    verbo = "voltariam" if args.dry_run else "voltaram"
    logger.info("Concluído: %d item(ns) %s ao lugar de origem.", voltaram, verbo)
    problemas = (contagem[Estado.ERRO] + contagem[Estado.NAO_ENCONTRADO]
                 + contagem[Estado.RECUSADO])
    if problemas:
        logger.warning("%d item(ns) não puderam ser desfeitos (veja acima).", problemas)
    return 1 if problemas else 0


def _relatar(r) -> None:
    a = r.acao
    if r.estado is Estado.DESFEITO:
        destino = r.restaurado_em or a.origem
        extra = f" ({r.detalhe})" if r.detalhe else ""
        logger.info("Voltou: %s%s", destino, extra)
    elif r.estado in (Estado.JA_DESFEITO, Estado.NAO_DESFAZIVEL):
        logger.info("%s: %s (%s)", r.estado.value.capitalize(), a.origem, r.detalhe)
    else:
        logger.error("%s: %s (%s)", r.estado.value.capitalize(), a.origem, r.detalhe)

