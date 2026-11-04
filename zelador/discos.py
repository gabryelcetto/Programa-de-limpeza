"""Leitura dos discos (volumes de dados) do computador, via psutil.

Só lê; nada aqui altera o computador. O resultado são dados simples (dataclasses), sem
formatação, para servirem tanto ao terminal quanto às recomendações e, depois, a uma janela.
"""

from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class UsoDisco:
    dispositivo: str
    ponto_de_montagem: str
    sistema_de_arquivos: str
    total: int  # bytes
    usado: int
    percentual: float


def _percentual(parte: int, total: int) -> float:
    return round(100.0 * parte / total, 1) if total else 0.0


def coletar_discos() -> tuple:
    """Os volumes de dados da máquina, na ordem em que o sistema os informa."""
    discos = []
    vistos = set()
    for particao in psutil.disk_partitions(all=False):
        if particao.mountpoint in vistos or not particao.fstype or "cdrom" in particao.opts:
            continue  # leitor sem mídia, ponto repetido ou algo que não é um volume de dados
        try:
            uso = psutil.disk_usage(particao.mountpoint)
        except OSError:  # PermissionError, volume sem mídia, unidade de rede fora do ar...
            continue
        if uso.total <= 0:
            continue
        vistos.add(particao.mountpoint)
        discos.append(UsoDisco(particao.device, particao.mountpoint, particao.fstype,
                               uso.total, uso.used, _percentual(uso.used, uso.total)))
    return tuple(discos)
