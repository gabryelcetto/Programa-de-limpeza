"""Configuração, gravação e rotação dos logs."""

import logging
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DIAS_MANUTENCAO_LOG = 30  # dias para manter os logs antes de deletar
FORMATO_DATA_LOG = "%Y-%m-%d"

logger = logging.getLogger("limpeza_downloads")


def pasta_logs_padrao() -> Path:
    """Pasta de logs fixa (independe do diretório de onde o script é chamado)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "LimpezaDownloads" / "logs"
    return Path(__file__).resolve().parent / "logs"


def configurar_logging(pasta_log: Path) -> Optional[Path]:
    """Configura log no console e em <pasta_log>/<data>/log.txt.
    Se o arquivo não puder ser criado, segue só com o console."""
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formato = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    if sys.stderr is not None:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(errors="replace")  # nomes de arquivo com caracteres exóticos
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(formato)
        logger.addHandler(console)

    try:
        pasta_dia = pasta_log / datetime.now().strftime(FORMATO_DATA_LOG)
        pasta_dia.mkdir(parents=True, exist_ok=True)
        arquivo = pasta_dia / "log.txt"
        handler = logging.FileHandler(arquivo, encoding="utf-8")
        handler.setFormatter(formato)
        logger.addHandler(handler)
        return arquivo
    except OSError as erro:
        logger.warning("Não foi possível criar o log em %s: %s", pasta_log, erro)
        return None


def deletar_logs_antigos(pasta_log: Path) -> None:
    if not pasta_log.exists():
        return

    agora = datetime.now()
    for pasta in pasta_log.iterdir():
        if not pasta.is_dir():
            continue
        try:
            data_pasta = datetime.strptime(pasta.name, FORMATO_DATA_LOG)
        except ValueError:
            continue  # não é uma pasta de log por data; não mexe
        if agora - data_pasta > timedelta(days=DIAS_MANUTENCAO_LOG):
            try:
                shutil.rmtree(pasta)
            except OSError as erro:
                logger.warning("Erro ao apagar log antigo %s: %s", pasta.name, erro)
