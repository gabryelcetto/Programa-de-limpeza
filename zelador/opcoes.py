"""Opções de linha de comando compartilhadas entre comandos."""

import argparse


def adicionar_dry_run(parser: argparse.ArgumentParser) -> None:
    """--dry-run, para os comandos que alteram arquivos (apagar, mover)."""
    parser.add_argument("--dry-run", action="store_true",
                        help="apenas mostra o que seria feito, sem alterar nada")
