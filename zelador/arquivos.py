"""Operações sobre arquivos e pastas: medir idade e tamanho, apagar, mover e formatar."""

import errno
import os
import shutil
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count
from pathlib import Path

import send2trash


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


def _codigo(erro: OSError) -> str:
    """O código de erro do sistema, para diagnóstico, no fim da mensagem (ou nada)."""
    codigo = getattr(erro, "winerror", None)
    if codigo is not None:
        return f" (código do sistema: {codigo & 0xFFFFFFFF:#010x})"
    return f" (código do sistema: {erro.errno})" if erro.errno else ""


def enviar_para_lixeira(item: Path) -> None:
    """Manda o item para a Lixeira do sistema, de onde dá para restaurá-lo. Levanta OSError.

    Atalhos (symlink/junction) nunca vão para a Lixeira: só o atalho é removido, como em
    `deletar_item`, porque o que a Lixeira faria com o conteúdo apontado por ele não é garantido.
    Atenção: um item na Lixeira ainda ocupa espaço no disco até ela ser esvaziada, e o Windows
    apaga de vez o que for maior que o limite da Lixeira ou estiver num disco sem Lixeira."""
    if _eh_link(item.lstat()):
        deletar_item(item)
        return
    try:
        send2trash.send2trash(str(item))
    except FileNotFoundError:
        raise
    except OSError as erro:  # a mensagem original é um código do sistema ou só o caminho
        raise OSError(f"o sistema não deixou enviar '{item.name}' para a Lixeira: o arquivo pode "
                      f"estar em uso por outro programa ou sem permissão{_codigo(erro)}") from erro


def _caminho_livre(destino: Path, eh_pasta: bool) -> Path:
    """Se o nome já existe, usa 'nome (1).ext', 'nome (2).ext'... Nunca sobrescreve."""
    if not os.path.lexists(destino):
        return destino
    base, sufixo = (destino.name, "") if eh_pasta else (destino.stem, destino.suffix)
    for n in count(1):
        candidato = destino.with_name(f"{base} ({n}){sufixo}")
        if not os.path.lexists(candidato):
            return candidato


def _mover_para(item: Path, pasta: Path, nome: str) -> Path:
    """Move `item` para `pasta/nome` (ou `nome (1)`, `nome (2)`... se já existir) e devolve o novo
    caminho. Nunca sobrescreve e recusa atalhos de pasta. Levanta OSError em caso de falha.

    No mesmo disco é só uma troca de nome, instantânea e sem meio-termo. Entre discos é preciso
    copiar e depois apagar a origem, e é aí que mora o perigo: uma pasta pode ter sido apagada só
    em parte (um arquivo aberto em outro programa impede o resto). Por isso a cópia completa NUNCA
    é desfeita depois que a origem começou a ser apagada: o que não pôde sair fica na origem, o
    resto fica no destino, e nada se perde."""
    info = item.lstat()
    eh_pasta = _eh_pasta(info)
    # no Linux o lstat de um symlink para pasta não traz o bit de diretório; is_dir() segue o link
    if _eh_link(info) and (eh_pasta or item.is_dir()):
        raise OSError(f"'{item.name}' é um atalho de pasta; atalhos não são movidos")

    pasta.mkdir(parents=True, exist_ok=True)
    destino = _caminho_livre(pasta / nome, eh_pasta)
    try:
        os.rename(item, destino)  # no Windows, falha (em vez de sobrescrever) se o nome existir
        return destino
    except OSError as erro:
        if erro.errno != errno.EXDEV:  # não é "outro disco": nada foi feito, nada a desfazer
            raise

    # outro disco: 1) copia; se a cópia falhar, a origem está intacta e a cópia parcial sai
    try:
        if eh_pasta:
            shutil.copytree(item, destino, symlinks=True)
        else:
            shutil.copy2(item, destino, follow_symlinks=False)
    except OSError:
        if os.path.lexists(destino):
            try:
                deletar_item(destino)
            except OSError:
                pass
        raise

    # 2) apaga a origem
    try:
        deletar_item(item)
    except OSError as erro:
        if not eh_pasta:
            # um arquivo é apagado de uma vez só: ele continua inteiro na origem, então a cópia
            # sai, para não ficar duplicado
            try:
                deletar_item(destino)
            except OSError:
                pass
            raise
        raise OSError(f"'{item.name}' foi copiado inteiro para {destino}, mas parte da pasta "
                      f"original não pôde ser apagada ({erro}). Nada se perdeu: confira as "
                      f"duas e apague a original quando ela for liberada") from erro
    return destino


def mover_item(item: Path, pasta_destino: Path) -> Path:
    """Move o item para a pasta (criada se preciso) e devolve o novo caminho.

    Não sobrescreve nada. Se a movimentação falhar no meio (ex.: entre volumes), a cópia
    parcial é desfeita, para não deixar duplicatas. Levanta OSError em caso de falha."""
    return _mover_para(item, pasta_destino, item.name)


def restaurar_item(atual: Path, original: Path) -> Path:
    """Devolve `atual` ao lugar `original`, com o NOME original (e não o que ele tem agora: um
    "a (1).pdf" volta a ser "a.pdf"). Se o lugar original estiver ocupado, não sobrescreve: o
    item volta como "nome (1)". Devolve o caminho em que ele ficou. Levanta OSError."""
    return _mover_para(atual, original.parent, original.name)


def formatar_tamanho(bytes_: int) -> str:
    valor = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024:
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"
