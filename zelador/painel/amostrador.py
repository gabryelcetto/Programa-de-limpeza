"""Amostrador: mede o desempenho a cada instante e guarda os últimos minutos para os gráficos.

Só lê; nada aqui altera o computador. O histórico é limitado (anel de tamanho fixo), então o
painel aberto por horas não consome mais memória com o tempo.
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import psutil

logger = logging.getLogger("zelador")

INTERVALO_PADRAO = 1.0  # segundos entre medições
JANELA_PADRAO = 300.0  # segundos de histórico guardados (5 minutos)


@dataclass(frozen=True)
class Amostra:
    instante: float  # segundos desde 1970 (time.time)
    cpu: float  # 0 a 100, todos os núcleos
    memoria: float  # 0 a 100
    # bytes por segundo; None quando o sistema não informa esse contador
    disco_leitura: Optional[float]
    disco_escrita: Optional[float]
    rede_envio: Optional[float]
    rede_recebimento: Optional[float]


def _taxa(atual: Optional[int], anterior: Optional[int], segundos: float) -> Optional[float]:
    """Bytes por segundo entre duas leituras de um contador que só cresce."""
    if atual is None or anterior is None or segundos <= 0:
        return None
    return max(0, atual - anterior) / segundos  # contador que zerou (reinício) vira 0, não negativo


def _limitar(valor: float) -> float:
    return min(100.0, max(0.0, valor))


class Medidor:
    """Uma medição = o uso de agora + as taxas desde a medição anterior."""

    def __init__(self, relogio: Callable[[], float] = time.time):
        self._relogio = relogio
        psutil.cpu_percent(None)  # a 1ª leitura sem espera não significa nada: só marca o início
        self._anterior = self._contadores()

    def _contadores(self) -> tuple:
        try:
            disco = psutil.disk_io_counters()
        except (OSError, RuntimeError):  # sem permissão, ou contadores de disco desligados
            disco = None
        try:
            rede = psutil.net_io_counters()
        except OSError:
            rede = None
        return (
            self._relogio(),
            (disco.read_bytes, disco.write_bytes) if disco else None,
            (rede.bytes_sent, rede.bytes_recv) if rede else None,
        )

    def medir(self) -> Amostra:
        cpu = _limitar(psutil.cpu_percent(None))
        memoria = _limitar(psutil.virtual_memory().percent)
        instante, disco, rede = self._contadores()
        antes_instante, antes_disco, antes_rede = self._anterior
        self._anterior = (instante, disco, rede)
        segundos = instante - antes_instante

        def par(atual, antes, posicao):
            return _taxa(atual[posicao] if atual else None,
                         antes[posicao] if antes else None, segundos)

        return Amostra(instante, cpu, memoria,
                       par(disco, antes_disco, 0), par(disco, antes_disco, 1),
                       par(rede, antes_rede, 0), par(rede, antes_rede, 1))


class Amostrador:
    """Mede de tempos em tempos, numa thread própria, e guarda o histórico recente."""

    def __init__(self, intervalo: float = INTERVALO_PADRAO, janela: float = JANELA_PADRAO,
                 medidor: Optional[Medidor] = None,
                 relogio: Callable[[], float] = time.time):
        if intervalo <= 0:
            raise ValueError("o intervalo deve ser maior que zero")
        if janela < intervalo:
            raise ValueError("a janela de histórico deve ser pelo menos igual ao intervalo")
        self.intervalo = intervalo
        self.janela = janela
        self.falhas = 0  # medições que deram erro (a thread continua mesmo assim)
        self._medidor = medidor
        self._relogio = relogio
        self._amostras = deque(maxlen=max(1, round(janela / intervalo)))
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- ciclo de vida

    @property
    def em_execucao(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self) -> None:
        if self.em_execucao:
            return
        if self._medidor is None:
            self._medidor = Medidor(self._relogio)
        self._parar.clear()
        self._thread = threading.Thread(target=self._laco, name="zelador-amostrador",
                                        daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=self.intervalo + 2)
            self._thread = None

    def __enter__(self) -> "Amostrador":
        self.iniciar()
        return self

    def __exit__(self, *erro) -> None:
        self.parar()

    # ---- medição e leitura

    def registrar(self) -> Optional[Amostra]:
        """Faz uma medição agora e a guarda. Um erro do sistema é contado, não propagado."""
        try:
            amostra = self._medidor.medir()
        except (psutil.Error, OSError) as erro:
            self.falhas += 1
            logger.debug("Falha ao medir o desempenho: %s", erro)
            return None
        with self._trava:
            self._amostras.append(amostra)
        return amostra

    def _laco(self) -> None:
        # Agenda cada medição contra um relógio monotônico: esperar `intervalo` DEPOIS de medir
        # faria o período real ser intervalo + tempo de medir, e o atraso se acumularia.
        proximo = time.monotonic() + self.intervalo
        while not self._parar.wait(max(0.0, proximo - time.monotonic())):
            try:
                self.registrar()
            except Exception:  # noqa: BLE001 - um monitor não pode morrer calado: o gráfico congelaria
                self.falhas += 1
                logger.exception("Erro inesperado no amostrador; continua medindo")
            proximo += self.intervalo
            agora = time.monotonic()
            if proximo < agora:  # demorou mais que o intervalo: sem rajada para "recuperar"
                proximo = agora + self.intervalo

    def historico(self, segundos: Optional[float] = None) -> list:
        """As amostras mais antigas primeiro; `segundos` limita aos últimos N segundos."""
        with self._trava:
            amostras = list(self._amostras)
        if segundos is None:
            return amostras
        corte = self._relogio() - segundos
        return [a for a in amostras if a.instante >= corte]

    def ultima(self) -> Optional[Amostra]:
        with self._trava:
            return self._amostras[-1] if self._amostras else None
