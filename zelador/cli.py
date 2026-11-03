"""Linha de comando: escolhe o subcomando e cuida do que é comum a todos (logs)."""

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import limpeza, logs

# Módulos que oferecem `registrar(subparsers, comum)`; cada um define um subcomando.
SUBCOMANDOS = (limpeza,)

SUBCOMANDO_PADRAO = "limpar"


def _opcoes_comuns() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--dry-run", action="store_true",
                       help="apenas mostra o que seria feito, sem alterar nada")
    comum.add_argument("--log-dir", type=Path, default=None, help="pasta dos logs")
    return comum


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zelador",
        description="Automação de arquivos. Use COMANDO --help para as opções de cada comando.",
    )
    subparsers = parser.add_subparsers(dest="comando", required=True, metavar="COMANDO")
    comum = _opcoes_comuns()
    for modulo in SUBCOMANDOS:
        modulo.registrar(subparsers, comum)
    return parser


def _com_subcomando(argv: list) -> list:
    """Retrocompatibilidade: chamadas sem subcomando (nada, ou só opções) equivalem a `limpar`."""
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        return [SUBCOMANDO_PADRAO, *argv]
    return argv


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    args = construir_parser().parse_args(_com_subcomando(argv))

    pasta_log = args.log_dir or logs.pasta_logs_padrao()
    logs.configurar_logging(pasta_log)

    codigo = args.executar(args)

    logs.deletar_logs_antigos(pasta_log)
    return codigo
