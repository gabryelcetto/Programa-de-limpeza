"""Operações sobre arquivos e pastas: medir idade e tamanho, apagar, mover e formatar."""

import os
import shutil
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count
from pathlib import Path


@dataclass
class Medicao:
    ultima_atividade: float  # timestamp mais recente do item (e de tudo dentro dele)
    tamanho: int  # bytes


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


def _caminho_livre(destino: Path, eh_pasta: bool) -> Path:
    """Se o nome já existe, usa 'nome (1).ext', 'nome (2).ext'... Nunca sobrescreve."""
    if not os.path.lexists(destino):
        return destino
    base, sufixo = (destino.name, "") if eh_pasta else (destino.stem, destino.suffix)
    for n in count(1):
        candidato = destino.with_name(f"{base} ({n}){sufixo}")
        if not os.path.lexists(candidato):
            return candidato


def mover_item(item: Path, pasta_destino: Path) -> Path:
    """Move o item para a pasta (criada se preciso) e devolve o novo caminho.

    Não sobrescreve nada. Se a movimentação falhar no meio (ex.: entre volumes), a cópia
    parcial é desfeita, para não deixar duplicatas. Levanta OSError em caso de falha."""
    info = item.lstat()
    eh_pasta = _eh_pasta(info)
    if eh_pasta and _eh_link(info):
        raise OSError(f"'{item.name}' é um atalho de pasta; atalhos não são movidos")

    pasta_destino.mkdir(parents=True, exist_ok=True)
    destino = _caminho_livre(pasta_destino / item.name, eh_pasta)
    try:
        shutil.move(item, destino)
    except OSError:
        if os.path.lexists(item) and os.path.lexists(destino):
            try:
                # o nome estava livre: o que existe aqui é uma cópia parcial nossa
                deletar_item(destino)
            except OSError:
                pass
        raise
    return destino


def formatar_tamanho(bytes_: int) -> str:
    valor = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024:
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"
