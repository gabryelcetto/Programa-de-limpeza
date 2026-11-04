"""Comandos `modelos` e `iniciar`: criam o arquivo de regras a partir de modelos prontos.

Códigos de saída: 0 tudo certo; 1 não foi possível gravar (o arquivo já existe, o destino é uma
pasta, erro de disco); 2 argumento inválido (por exemplo, um modelo que não existe).
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import caminhos, modelos
from .configuracao import carregar_regras_de_texto
from .logs import logger


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    listar = subparsers.add_parser(
        "modelos", parents=[comum],
        help="lista os modelos prontos de regras",
        description="Mostra os modelos que o `iniciar` pode juntar num arquivo de regras.",
    )
    listar.set_defaults(executar=executar_modelos)

    iniciar = subparsers.add_parser(
        "iniciar", parents=[comum],
        help="cria o arquivo de regras a partir de modelos prontos",
        description="Cria um arquivo de regras (YAML) a partir de modelos prontos, para você "
                    "editar. Nunca sobrescreve um arquivo existente sem --forcar.",
    )
    iniciar.add_argument("--modelo", action="append", choices=modelos.ids(), metavar="ID",
                         help="modelo a incluir; repita para vários (padrão: os recomendados). "
                              f"Ids: {', '.join(modelos.ids())}")
    iniciar.add_argument("--destino", type=Path, default=None,
                         help="onde gravar (padrão: o arquivo de regras padrão do Zelador)")
    iniciar.add_argument("--forcar", action="store_true",
                         help="substitui o arquivo existente, guardando antes uma cópia datada")
    iniciar.add_argument("--imprimir", action="store_true",
                         help="mostra o arquivo na tela, sem gravar nada")
    iniciar.set_defaults(executar=executar_iniciar)


def _preparar_saida() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # consoles que não têm algum caractere


def executar_modelos(args: argparse.Namespace) -> int:
    _preparar_saida()
    for modelo in modelos.MODELOS:
        recomendado = "  (recomendado)" if modelo.padrao else ""
        print(f"{modelo.id}{recomendado}")
        print(f"    {modelo.titulo}: {modelo.descricao}")
    print()
    print("Para criar o arquivo de regras: zelador iniciar [--modelo ID ...]")
    return 0


def _copia_de_seguranca(destino: Path) -> Path:
    """Copia o arquivo para `<nome>.<data-hora>.bak`, sem nunca sobrescrever uma cópia antiga."""
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    copia = destino.with_name(f"{destino.name}.{carimbo}.bak")
    contador = 1
    while copia.exists():
        copia = destino.with_name(f"{destino.name}.{carimbo}-{contador}.bak")
        contador += 1
    shutil.copy2(destino, copia)
    return copia


def _gravar_atomicamente(destino: Path, texto: str) -> None:
    """Grava num arquivo temporário e troca: se falhar no meio, o original continua intacto."""
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        # bytes, e não write_text(newline=...): esse parâmetro só existe a partir do Python 3.10,
        # e assim as quebras de linha saem iguais em qualquer sistema
        temporario.write_bytes(texto.encode("utf-8"))
        os.replace(temporario, destino)
    except OSError:
        temporario.unlink(missing_ok=True)
        raise


def executar_iniciar(args: argparse.Namespace) -> int:
    texto = modelos.gerar_yaml(args.modelo)

    if args.imprimir:
        _preparar_saida()
        print(texto, end="")
        return 0

    destino = args.destino or caminhos.arquivo_de_regras_padrao()
    if destino.is_dir():
        logger.error("O destino %s é uma pasta; informe o caminho de um arquivo.", destino)
        return 1
    existia = destino.exists()
    if existia and not args.forcar:
        logger.error("Já existe um arquivo de regras em %s. Use --forcar para substituí-lo "
                     "(uma cópia datada é guardada) ou --destino para gravar em outro lugar.",
                     destino)
        return 1

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if existia:
            logger.info("Cópia do arquivo anterior guardada em %s", _copia_de_seguranca(destino))
        _gravar_atomicamente(destino, texto)
    except OSError as erro:
        logger.error("Não foi possível gravar %s: %s", destino, erro)
        return 1

    carga = carregar_regras_de_texto(texto)
    logger.info("Arquivo de regras criado em %s (%d regra(s)).", destino, len(carga.regras))
    for erro in carga.erros:  # só acontece se o computador impedir uma regra (pasta protegida...)
        logger.warning("Atenção, não vale neste computador: %s", erro)
    logger.info("Próximos passos: 1) zelador listar --config \"%s\"  2) zelador rodar "
                "--config \"%s\" --dry-run  3) tire o --dry-run quando estiver satisfeito.",
                destino, destino)
    return 0
