"""Informações do sistema: CPU, memória, discos e processos (via psutil).

Só lê; nada aqui altera o computador. Os resultados são dados simples (dataclasses), sem
formatação, para servirem tanto ao terminal quanto, depois, a uma janela.
"""

import time
from dataclasses import dataclass
from typing import Optional

import psutil

ORDENACOES = ("memoria", "cpu")


@dataclass(frozen=True)
class UsoCpu:
    percentual: float  # uso total de todos os núcleos, de 0 a 100
    nucleos_logicos: int
    nucleos_fisicos: Optional[int]  # None quando o sistema não informa


@dataclass(frozen=True)
class UsoMemoria:
    total: int  # bytes
    usada: int
    percentual: float
    swap_total: int  # arquivo de paginação; 0 se não houver
    swap_usada: int


@dataclass(frozen=True)
class UsoDisco:
    dispositivo: str
    ponto_de_montagem: str
    sistema_de_arquivos: str
    total: int
    usado: int
    percentual: float


@dataclass(frozen=True)
class Status:
    cpu: UsoCpu
    memoria: UsoMemoria
    discos: tuple
    segundos_ligado: float


@dataclass(frozen=True)
class Processo:
    pid: int
    nome: str
    memoria: Optional[int]  # bytes em uso (working set); None se o acesso foi negado
    cpu: Optional[float]  # % da CPU total da máquina; só é medido ao ordenar por CPU


def _percentual(parte: int, total: int) -> float:
    return round(100.0 * parte / total, 1) if total else 0.0


# ---------------------------------------------------------------- status


def _discos() -> tuple:
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


def coletar_memoria() -> UsoMemoria:
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return UsoMemoria(ram.total, ram.used, ram.percent, swap.total, swap.used)


def coletar_discos() -> tuple:
    return _discos()


def segundos_ligado() -> float:
    return max(0.0, time.time() - psutil.boot_time())


def coletar_status(intervalo_cpu: float = 0.5) -> Status:
    """Fotografia do sistema. O uso de CPU é medido durante `intervalo_cpu` segundos."""
    cpu = UsoCpu(psutil.cpu_percent(interval=intervalo_cpu),
                 psutil.cpu_count(logical=True) or 1,
                 psutil.cpu_count(logical=False))
    return Status(cpu, coletar_memoria(), coletar_discos(), segundos_ligado())


# ------------------------------------------------------------- processos


def listar_processos(ordenar: str = "memoria", limite: int = 10,
                     intervalo_cpu: float = 0.5) -> list:
    """Os `limite` processos que mais usam memória ou CPU, do maior para o menor.

    Processos que somem durante a leitura, ou cujos dados o Windows nega, não derrubam nada:
    os dados negados ficam como None (e, na ordenação, vão para o fim)."""
    if ordenar not in ORDENACOES:
        raise ValueError(f"ordenação inválida: {ordenar!r} (use: {', '.join(ORDENACOES)})")
    if limite < 1:
        raise ValueError("o limite deve ser de pelo menos 1 processo")

    nucleos = psutil.cpu_count(logical=True) or 1
    processos = list(psutil.process_iter(["pid", "name", "memory_info"]))

    if ordenar == "cpu":
        for processo in processos:  # a primeira leitura só marca o ponto de partida
            try:
                processo.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(intervalo_cpu)

    resultado = []
    for processo in processos:
        try:
            info = processo.info
            if info["pid"] == 0:
                continue  # "System Idle Process" no Windows: o % dele é o tempo ocioso
            memoria = info["memory_info"].rss if info["memory_info"] else None
            cpu = None
            if ordenar == "cpu":
                cpu = round(processo.cpu_percent(None) / nucleos, 1)  # % do total, como o Windows
        except psutil.Error:  # NoSuchProcess, AccessDenied, ZombieProcess
            continue
        resultado.append(Processo(info["pid"], info["name"] or "?", memoria, cpu))

    def chave(p: Processo):
        valor = p.memoria if ordenar == "memoria" else p.cpu
        return (valor is not None, valor or 0)

    resultado.sort(key=chave, reverse=True)
    return resultado[:limite]
