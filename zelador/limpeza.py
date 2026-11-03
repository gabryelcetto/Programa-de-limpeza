import argparse
from pathlib import Path

from .arquivos import formatar_tamanho
from .caminhos import obter_pasta_downloads
from .logs import logger
from .opcoes import adicionar_dry_run
from .regras import Apagar, IdadeMinima, Regra, Resumo, executar_regra

DIAS_LIMITE = 7  # dias sem atividade para considerar um item como antigo


def limpar(pasta: Path, dias: int, dry_run: bool) -> Resumo:
    """O `limpar` é uma regra do motor: apagar o que está sem atividade há mais de `dias`."""
    regra = Regra("limpar", pasta, [IdadeMinima(dias)], Apagar())
    return executar_regra(regra, dry_run)


# ------------------------------------------------------------ subcomando


def _dias_validos(texto: str) -> int:
    """Aceita só inteiros >= 1: com 0 (ou negativo) tudo seria considerado antigo."""
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' não é um número inteiro") from None
    if valor < 1:
        raise argparse.ArgumentTypeError("o mínimo é 1 dia")
    return valor


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    """Registra o subcomando `limpar` no parser principal."""
    parser = subparsers.add_parser(
        "limpar",
        parents=[comum],
        help="remove itens antigos de uma pasta (padrão: Downloads)",
        description="Remove itens antigos da pasta de Downloads.",
    )
    adicionar_dry_run(parser)
    parser.add_argument("--dias", type=_dias_validos, default=DIAS_LIMITE,
                        help="dias sem atividade (mínimo 1; padrão: %(default)s)")
    parser.add_argument("--pasta", type=Path, default=None,
                        help="pasta a limpar (padrão: Downloads do usuário)")
    parser.set_defaults(executar=executar)


def executar(args: argparse.Namespace) -> int:
    pasta = args.pasta or obter_pasta_downloads()
    logger.info("Limpeza iniciada%s. Pasta: %s | limite: %d dias",
                " (SIMULAÇÃO)" if args.dry_run else "", pasta, args.dias)

    if not pasta.is_dir():
        logger.error("A pasta %s não foi encontrada.", pasta)
        return 1

    try:
        resumo = limpar(pasta, args.dias, args.dry_run)
    except ValueError as erro:  # pasta protegida (raiz do disco, pasta do sistema...)
        logger.error("%s", erro)
        return 2

    verbo = "seriam apagados" if args.dry_run else "apagados"
    logger.info("Concluído: %d %s (%s), %d mantidos, %d erros.",
                resumo.aplicados, verbo, formatar_tamanho(resumo.bytes),
                resumo.ignorados, resumo.erros)
    return 1 if resumo.erros else 0
