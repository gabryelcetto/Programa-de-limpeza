"""Os dados que a tela recebe: dicionários simples, prontos para virar JSON.

Cada seção é uma função independente, para a tela atualizar cada uma no seu ritmo (o desempenho
a cada segundo; a lista de processos, que custa mais, só de vez em quando). Nenhum valor é NaN ou
infinito, então o resultado sempre passa por `json.dumps(..., allow_nan=False)`.

Esquema (versão 1):
  desempenho:    cpu, memoria, historico (colunas alinhadas; tempo em segundos atrás, <= 0)
  armazenamento: lista de discos
  processos:     lista dos que mais usam memória
"""

import dataclasses
import time
from typing import Optional

import psutil

from .. import sistema
from . import amostrador as _amostrador

VERSAO = 1
SEGUNDOS_DE_HISTORICO = 300.0
PROCESSOS_PADRAO = 8

_COLUNAS = ("cpu", "memoria", "disco_leitura", "disco_escrita", "rede_envio", "rede_recebimento")


def _arredondar(valor: Optional[float], casas: int = 1) -> Optional[float]:
    return None if valor is None else round(valor, casas)


def desempenho(amostras: _amostrador.Amostrador,
               segundos: float = SEGUNDOS_DE_HISTORICO) -> dict:
    """CPU e memória de agora, mais o histórico recente em colunas (bom para gráficos)."""
    agora = time.time()
    historico = amostras.historico(segundos)
    ultima = amostras.ultima()
    ram = sistema.coletar_memoria()

    colunas = {nome: [] for nome in _COLUNAS}
    for a in historico:
        colunas["cpu"].append(_arredondar(a.cpu))
        colunas["memoria"].append(_arredondar(a.memoria))
        for nome in _COLUNAS[2:]:  # taxas em bytes/s; None = o sistema não informa
            colunas[nome].append(_arredondar(getattr(a, nome), 0))

    return {
        "instante": agora,
        "cpu": {
            "percentual": _arredondar(ultima.cpu) if ultima else None,  # None até a 1ª medição
            "nucleos_logicos": psutil.cpu_count(logical=True) or 1,
            "nucleos_fisicos": psutil.cpu_count(logical=False),
        },
        "memoria": dataclasses.asdict(ram),
        "segundos_ligado": sistema.segundos_ligado(),
        "historico": {"segundos_atras": [round(a.instante - agora, 2) for a in historico],
                      **colunas},
    }


def armazenamento() -> list:
    return [dataclasses.asdict(d) for d in sistema.coletar_discos()]


def processos(limite: int = PROCESSOS_PADRAO) -> list:
    return [dataclasses.asdict(p) for p in sistema.listar_processos("memoria", limite)]


def retrato(amostras: _amostrador.Amostrador, limite_processos: int = PROCESSOS_PADRAO,
            segundos: float = SEGUNDOS_DE_HISTORICO) -> dict:
    """Tudo junto. Uma seção que o sistema não deixa ler vira None e entra em `erros`, sem
    derrubar as outras."""
    resultado = {"versao": VERSAO, "erros": []}
    secoes = (("desempenho", lambda: desempenho(amostras, segundos)),
              ("armazenamento", armazenamento),
              ("processos", lambda: processos(limite_processos)))
    for nome, ler in secoes:
        try:
            resultado[nome] = ler()
        except (psutil.Error, OSError):
            resultado[nome] = None
            resultado["erros"].append(nome)
    return resultado
