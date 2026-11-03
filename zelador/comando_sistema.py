"""Comandos `status` e `processos`: informações do sistema, só para leitura.

Códigos de saída: 0 tudo certo; 1 o sistema não deixou ler as informações; 2 argumento inválido.
"""

import argparse
import sys

import psutil

from . import sistema
from .arquivos import formatar_tamanho
from .logs import logger

LARGURA_BARRA = 20
LARGURA_NOME = 30
ROTULO = 9  # largura da coluna de rótulos ("Memória", "CPU"...)


# ------------------------------------------------------------ formatação


def barra(percentual: float, largura: int = LARGURA_BARRA) -> str:
    """Barra de texto, como [#####---------------]. Só ASCII, para qualquer console."""
    percentual = min(100.0, max(0.0, percentual))
    cheios = round(percentual / 100 * largura)
    return "[" + "#" * cheios + "-" * (largura - cheios) + "]"


def formatar_duracao(segundos: float) -> str:
    minutos_totais = int(segundos // 60)
    dias, resto = divmod(minutos_totais, 24 * 60)
    horas, minutos = divmod(resto, 60)
    if dias:
        return f"{dias} {'dia' if dias == 1 else 'dias'} {horas}h {minutos:02d}min"
    if horas:
        return f"{horas}h {minutos:02d}min"
    if minutos:
        return f"{minutos}min"
    return "menos de 1 min"


def _linha_de_uso(rotulo: str, percentual: float, detalhe: str) -> str:
    return f"{rotulo:<{ROTULO}} {barra(percentual)} {percentual:5.1f}%  {detalhe}"


def formatar_status(status: sistema.Status) -> str:
    cpu, memoria = status.cpu, status.memoria
    nucleos = f"{cpu.nucleos_logicos} lógicos"
    if cpu.nucleos_fisicos:
        nucleos += f" / {cpu.nucleos_fisicos} físicos"

    linhas = [
        _linha_de_uso("CPU", cpu.percentual, f"núcleos: {nucleos}"),
        _linha_de_uso("Memória", memoria.percentual,
                      f"{formatar_tamanho(memoria.usada)} de {formatar_tamanho(memoria.total)} "
                      "em uso"),
    ]
    if memoria.swap_total:
        percentual_swap = round(100.0 * memoria.swap_usada / memoria.swap_total, 1)
        linhas.append(_linha_de_uso(
            "Swap", percentual_swap,
            f"{formatar_tamanho(memoria.swap_usada)} de {formatar_tamanho(memoria.swap_total)} "
            "em uso"))
    linhas.append(f"{'Ligado':<{ROTULO}} há {formatar_duracao(status.segundos_ligado)}")

    if status.discos:
        largura = max(len(d.ponto_de_montagem) for d in status.discos)
        linhas += ["", "Discos"]
        for disco in status.discos:
            detalhe = (f"{formatar_tamanho(disco.usado)} de {formatar_tamanho(disco.total)} "
                       "em uso")
            linhas.append(f"  {disco.ponto_de_montagem:<{largura}} {disco.sistema_de_arquivos:<6} "
                          f"{barra(disco.percentual)} {disco.percentual:5.1f}%  {detalhe}")
    return "\n".join(linhas)


def _nome_curto(nome: str) -> str:
    return nome if len(nome) <= LARGURA_NOME else nome[:LARGURA_NOME - 3] + "..."


def formatar_processos(processos: list, ordenar: str) -> str:
    titulo = ("memória" if ordenar == "memoria" else "CPU (% do total da máquina)")
    linhas = [f"Top {len(processos)} processos por {titulo}",
              f"{'PID':>7}  {'Nome':<{LARGURA_NOME}} {'Memória':>10} {'CPU':>6}"]
    for p in processos:
        memoria = formatar_tamanho(p.memoria) if p.memoria is not None else "n/d"
        cpu = f"{p.cpu:.1f}%" if p.cpu is not None else "-"
        linhas.append(f"{p.pid:>7}  {_nome_curto(p.nome):<{LARGURA_NOME}} {memoria:>10} {cpu:>6}")
    return "\n".join(linhas)


# --------------------------------------------------------------- comandos


def _inteiro_positivo(texto: str) -> int:
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' não é um número inteiro") from None
    if valor < 1:
        raise argparse.ArgumentTypeError("o mínimo é 1")
    return valor


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    status = subparsers.add_parser(
        "status", parents=[comum],
        help="mostra o uso de CPU, memória e discos e há quanto tempo o PC está ligado",
        description="Mostra o uso de CPU, memória e discos. Só lê informações do sistema; "
                    "a medição da CPU leva cerca de meio segundo.",
    )
    status.set_defaults(executar=executar_status)

    processos = subparsers.add_parser(
        "processos", parents=[comum],
        help="lista os processos que mais usam memória ou CPU",
        description="Lista os processos que mais usam memória ou CPU. Só lê informações do "
                    "sistema; ordenar por CPU leva cerca de meio segundo (precisa de duas "
                    "leituras). Processos que o Windows não deixa ler aparecem como n/d.",
    )
    processos.add_argument("--top", type=_inteiro_positivo, default=10, metavar="N",
                           help="quantos processos mostrar (padrão: %(default)s)")
    processos.add_argument("--ordenar", choices=sistema.ORDENACOES, default="memoria",
                           help="critério (padrão: %(default)s)")
    processos.set_defaults(executar=executar_processos)


def _preparar_saida() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # consoles que não têm algum caractere


def executar_status(args: argparse.Namespace) -> int:
    try:
        status = sistema.coletar_status()
    except (psutil.Error, OSError) as erro:
        logger.error("Não foi possível ler o estado do sistema: %s", erro)
        return 1
    _preparar_saida()
    print(formatar_status(status))
    return 0


def executar_processos(args: argparse.Namespace) -> int:
    try:
        processos = sistema.listar_processos(args.ordenar, args.top)
    except (psutil.Error, OSError) as erro:
        logger.error("Não foi possível listar os processos: %s", erro)
        return 1
    _preparar_saida()
    print(formatar_processos(processos, args.ordenar))
    return 0
