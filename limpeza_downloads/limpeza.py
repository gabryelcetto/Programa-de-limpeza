import argparse
import ctypes
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .logs import logger

DIAS_LIMITE = 7  # dias sem atividade para considerar um item como antigo

# GUID do Known Folder "Downloads" (respeita redirecionamento por OneDrive/GPO)
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


@dataclass
class Medicao:
    ultima_atividade: float  # timestamp mais recente do item (e de tudo dentro dele)
    tamanho: int  # bytes


@dataclass
class Resumo:
    apagados: int = 0
    ignorados: int = 0
    erros: int = 0
    bytes_liberados: int = 0


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


# ------------------------------------------------------- idade dos itens


def _data_referencia(info: os.stat_result) -> float:
    """Maior entre modificação e criação.

    Arquivos baixados/extraídos/copiados costumam manter o mtime original;
    a data de criação reflete quando o arquivo chegou na máquina.
    """
    criacao = getattr(info, "st_birthtime", info.st_ctime)
    return max(info.st_mtime, criacao)


def _eh_link(info: os.stat_result) -> bool:
    """Symlink ou ponto de reanálise (junction) do Windows."""
    atributos = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(atributos & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _eh_pasta(info: os.stat_result) -> bool:
    atributos = getattr(info, "st_file_attributes", 0)
    return stat.S_ISDIR(info.st_mode) or bool(atributos & stat.FILE_ATTRIBUTE_DIRECTORY)


def _percorrer(pasta: Path) -> Iterator[os.stat_result]:
    """Percorre a pasta sem seguir symlinks nem junctions."""
    pilha = [str(pasta)]
    while pilha:
        atual = pilha.pop()
        try:
            entradas = os.scandir(atual)
        except OSError:
            continue
        with entradas:
            for entrada in entradas:
                try:
                    info = entrada.stat(follow_symlinks=False)
                except OSError:
                    continue
                yield info
                if _eh_pasta(info) and not _eh_link(info):
                    pilha.append(entrada.path)


def medir_item(item: Path) -> Medicao:
    """Última atividade do item. Para pastas, considera tudo que há dentro:
    o mtime de uma pasta não muda quando um arquivo aninhado é alterado."""
    info = item.lstat()
    ultima = _data_referencia(info)
    tamanho = 0

    if _eh_pasta(info) and not _eh_link(info):
        for sub in _percorrer(item):
            ultima = max(ultima, _data_referencia(sub))
            if not _eh_pasta(sub):
                tamanho += sub.st_size
    else:
        tamanho = info.st_size

    return Medicao(ultima, tamanho)


def item_antigo(medicao: Medicao, agora: datetime, dias: int) -> bool:
    return agora - datetime.fromtimestamp(medicao.ultima_atividade) > timedelta(days=dias)


# --------------------------------------------------------------- remoção


def _liberar_e_repetir(funcao, caminho, *_):
    """Handler do rmtree: remove o atributo somente leitura e tenta de novo."""
    os.chmod(caminho, stat.S_IWRITE)
    funcao(caminho)


def deletar_item(item: Path) -> None:
    """Apaga o item. Levanta OSError em caso de falha."""
    info = item.lstat()

    if _eh_pasta(info) and _eh_link(info):
        # symlink/junction de pasta: remove só o atalho, nunca o destino
        os.rmdir(item)
    elif _eh_pasta(info):
        if sys.version_info >= (3, 12):
            shutil.rmtree(item, onexc=_liberar_e_repetir)
        else:
            shutil.rmtree(item, onerror=_liberar_e_repetir)
    else:
        try:
            item.unlink()
        except PermissionError:
            os.chmod(item, stat.S_IWRITE)
            item.unlink()


def _formatar_tamanho(bytes_: int) -> str:
    valor = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024:
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"


def limpar(pasta: Path, dias: int, dry_run: bool) -> Resumo:
    resumo = Resumo()
    agora = datetime.now()
    prefixo = "[SIMULAÇÃO] " if dry_run else ""

    for item in sorted(pasta.iterdir()):
        tipo = "Pasta" if item.is_dir() and not item.is_symlink() else "Arquivo"
        try:
            medicao = medir_item(item)
            if not item_antigo(medicao, agora, dias):
                resumo.ignorados += 1
                logger.debug("Mantido (recente): %s", item.name)
                continue

            if not dry_run:
                deletar_item(item)
        except OSError as erro:
            resumo.erros += 1
            logger.error("Erro em %s: %s", item.name, erro)
            continue

        resumo.apagados += 1
        resumo.bytes_liberados += medicao.tamanho
        logger.info("%s%s deletado(a): %s (%s)", prefixo, tipo, item.name,
                    _formatar_tamanho(medicao.tamanho))

    return resumo


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
                resumo.apagados, verbo, _formatar_tamanho(resumo.bytes_liberados),
                resumo.ignorados, resumo.erros)
    return 1 if resumo.erros else 0
