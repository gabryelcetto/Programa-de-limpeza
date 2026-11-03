"""Comandos `rodar` e `listar`: trabalham com as regras de um arquivo YAML.

Códigos de saída: 0 tudo certo; 1 alguma regra foi ignorada por ser inválida ou houve erro ao
executar; 2 o arquivo não pôde ser usado (não existe, YAML inválido...).
"""

import argparse
import sys
from pathlib import Path

from . import caminhos
from .arquivos import formatar_tamanho
from .configuracao import Carga, ErroConfiguracao, carregar_regras
from .logs import logger
from .regras import Resumo, executar_regra


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    ajuda_config = "arquivo de regras (padrão: a pasta Zelador dentro de APPDATA)"

    rodar = subparsers.add_parser(
        "rodar", parents=[comum],
        help="executa as regras de um arquivo YAML",
        description="Executa, em ordem, as regras de um arquivo YAML. Regras inválidas são "
                    "ignoradas com um aviso e as demais continuam.",
    )
    rodar.add_argument("--config", type=Path, default=None, help=ajuda_config)
    rodar.set_defaults(executar=executar_rodar)

    listar = subparsers.add_parser(
        "listar", parents=[comum],
        help="mostra as regras de um arquivo YAML e aponta problemas, sem executar nada",
        description="Mostra o que cada regra faz e quais têm problema. Não altera nenhum "
                    "arquivo, então serve para conferir o arquivo antes de rodar.",
    )
    listar.add_argument("--config", type=Path, default=None, help=ajuda_config)
    listar.set_defaults(executar=executar_listar)


def _carregar(args: argparse.Namespace):
    """Devolve (caminho, carga), ou (caminho, None) depois de registrar o erro."""
    caminho = args.config or caminhos.arquivo_de_regras_padrao()
    try:
        return caminho, carregar_regras(caminho)
    except ErroConfiguracao as erro:
        logger.error("Arquivo de regras: %s", erro)
        if args.config is None and not caminho.exists():
            logger.error("Crie o arquivo (há um modelo em exemplos/regras.yml no repositório) "
                         "ou informe outro com --config.")
        return caminho, None


# ----------------------------------------------------------------- rodar


def executar_rodar(args: argparse.Namespace) -> int:
    caminho, carga = _carregar(args)
    if carga is None:
        return 2

    logger.info("Regras lidas de %s: %d válida(s), %d ignorada(s)%s.", caminho,
                len(carga.regras), len(carga.erros), " (SIMULAÇÃO)" if args.dry_run else "")
    for erro in carga.erros:
        logger.error("Regra ignorada: %s", erro)
    if not carga.regras:
        logger.warning("Nenhuma regra válida para executar.")

    total = Resumo()
    houve_falha = bool(carga.erros)
    for regra in carga.regras:
        logger.info("Regra '%s' em %s", regra.nome, regra.pasta)
        try:
            resumo = executar_regra(regra, args.dry_run)
        except OSError as erro:  # ex.: a pasta da regra não existe
            logger.error("Regra '%s': não foi possível ler a pasta %s: %s",
                         regra.nome, regra.pasta, erro)
            houve_falha = True
            continue
        total.aplicados += resumo.aplicados
        total.erros += resumo.erros
        total.bytes += resumo.bytes

    verbo = "seriam aplicados" if args.dry_run else "aplicados"
    logger.info("Concluído: %d itens %s (%s), %d erros.",
                total.aplicados, verbo, formatar_tamanho(total.bytes), total.erros)
    return 1 if houve_falha or total.erros else 0


# ---------------------------------------------------------------- listar


def _imprimir(carga: Carga, caminho: Path) -> None:
    print(f"Arquivo: {caminho}")
    print(f"{len(carga.regras)} regra(s) válida(s), {len(carga.erros)} com problema.")

    for numero, regra in enumerate(carga.regras, start=1):
        print()
        print(f"{numero}. {regra.nome}")
        print(f"   pasta : {regra.pasta}")
        for condicao in regra.condicoes:
            print(f"   quando: {condicao.resumo()}")
        print(f"   acao  : {regra.acao.resumo()}")

    if carga.erros:
        print()
        print("Regras com problema (serão ignoradas):")
        for erro in carga.erros:
            print(f"  - {erro}")


def executar_listar(args: argparse.Namespace) -> int:
    caminho, carga = _carregar(args)
    if carga is None:
        return 2

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # nomes com caracteres que o console não tem
    _imprimir(carga, caminho)
    return 1 if carga.erros else 0
