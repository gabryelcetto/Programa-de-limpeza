"""Comando `painel`: abre o painel no navegador, com os dados reais do PC.

O painel só mostra e simula; nada muda antes de a pessoa confirmar na tela. Fica aberto enquanto
esta janela do terminal estiver aberta (Ctrl+C fecha).
"""

import argparse
from pathlib import Path

from . import caminhos
from .opcoes import adicionar_historico
from .painel import servidor


def _porta(texto: str) -> int:
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' não é um número de porta") from None
    if not 0 <= valor <= 65535:
        raise argparse.ArgumentTypeError("a porta vai de 0 a 65535 (0 = qualquer uma livre)")
    return valor


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "painel", parents=[comum],
        help="abre o painel no navegador (discos, recomendações, regras, histórico, agenda)",
        description="Abre o painel no navegador. Ele mostra o que seria feito e só age depois "
                    "que você confirma na tela. Feche com Ctrl+C neste terminal.",
    )
    adicionar_historico(parser)
    parser.add_argument("--config", type=Path, default=None,
                        help="arquivo de regras (padrão: a pasta Zelador dentro de APPDATA)")
    parser.add_argument("--porta", type=_porta, default=0,
                        help="porta local (padrão: uma livre, escolhida pelo sistema)")
    parser.add_argument("--sem-navegador", action="store_true",
                        help="não abre o navegador sozinho (só mostra o endereço)")
    parser.set_defaults(executar=executar)


def executar(args: argparse.Namespace) -> int:
    cfg = servidor.Configuracao(
        regras=args.config or caminhos.arquivo_de_regras_padrao(),
        historico=args.historico or caminhos.arquivo_de_historico_padrao(),
        downloads=caminhos.obter_pasta_downloads(),
        pdfs=caminhos.obter_pasta_documentos() / "PDFs",
    )
    return servidor.servir(cfg, args.porta, abrir_navegador=not args.sem_navegador)
