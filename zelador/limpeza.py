import argparse
import ctypes
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

from .arquivos import formatar_tamanho
from .logs import logger
from .regras import Apagar, IdadeMinima, Regra, Resumo, executar_regra

DIAS_LIMITE = 7  # dias sem atividade para considerar um item como antigo

# GUID do Known Folder "Downloads" (respeita redirecionamento por OneDrive/GPO)
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


# ---------------------------------------------------------------- caminhos


def obter_pasta_downloads() -> Path:
    """Resolve a pasta de Downloads real do usuário (Known Folders no Windows)."""
    if sys.platform == "win32":
        try:

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            guid = GUID.from_buffer_copy(uuid.UUID(FOLDERID_DOWNLOADS).bytes_le)
            caminho = ctypes.c_wchar_p()
            resultado = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(caminho)
            )
            if resultado == 0:
                try:
                    return Path(caminho.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(caminho)
        except Exception:
            pass  # cai no caminho padrão abaixo

    return Path.home() / "Downloads"


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

    resumo = limpar(pasta, args.dias, args.dry_run)

    verbo = "seriam apagados" if args.dry_run else "apagados"
    logger.info("Concluído: %d %s (%s), %d mantidos, %d erros.",
                resumo.aplicados, verbo, formatar_tamanho(resumo.bytes),
                resumo.ignorados, resumo.erros)
    return 1 if resumo.erros else 0
