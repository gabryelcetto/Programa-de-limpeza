"""Opções de linha de comando compartilhadas entre comandos."""

import argparse
from pathlib import Path


def adicionar_dry_run(parser: argparse.ArgumentParser) -> None:
    """--dry-run, para os comandos que alteram arquivos (apagar, mover)."""
    parser.add_argument("--dry-run", action="store_true",
                        help="apenas mostra o que seria feito, sem alterar nada")


def adicionar_historico(parser: argparse.ArgumentParser) -> None:
    """--historico, o arquivo onde o Zelador anota o que fez (e de onde o `desfazer` lê)."""
    parser.add_argument("--historico", type=Path, default=None, metavar="ARQUIVO",
                        help="arquivo do histórico (padrão: a pasta Zelador dentro de "
                             "LOCALAPPDATA)")
