import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from zelador.painel import amostrador
from zelador.painel.amostrador import Amostra, Amostrador, Medidor, _taxa


def amostra(instante=0.0, cpu=10.0, memoria=50.0):
    return Amostra(instante, cpu, memoria, 1.0, 2.0, 3.0, 4.0)


class Relogio:
    def __init__(self, inicio=1000.0):
        self.agora = inicio

    def __call__(self):
        return self.agora

    def avancar(self, segundos):
        self.agora += segundos


@pytest.fixture
def falso_psutil(monkeypatch):
    estado = SimpleNamespace(
        cpu=40.0, memoria=55.0, chamadas_cpu=[],
        disco=SimpleNamespace(read_bytes=0, write_bytes=0),
        rede=SimpleNamespace(bytes_sent=0, bytes_recv=0),
    )

    def cpu_percent(interval=None):
        estado.chamadas_cpu.append(interval)
        return estado.cpu

    def disk_io_counters():
        if isinstance(estado.disco, Exception):
            raise estado.disco
        return estado.disco

    def net_io_counters():
        if isinstance(estado.rede, Exception):
            raise estado.rede
        return estado.rede

    monkeypatch.setattr(amostrador.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(amostrador.psutil, "virtual_memory",
                        lambda: SimpleNamespace(percent=estado.memoria))
    monkeypatch.setattr(amostrador.psutil, "disk_io_counters", disk_io_counters)
    monkeypatch.setattr(amostrador.psutil, "net_io_counters", net_io_counters)
    return estado


# ---------------------------------------------------------------- taxa


@pytest.mark.parametrize("atual, anterior, segundos, esperado", [
    (3000, 1000, 2.0, 1000.0),
    (1000, 1000, 1.0, 0.0),
    (500, 1000, 1.0, 0.0),        # o contador reiniciou: nunca devolve taxa negativa
    (1000, 0, 0.5, 2000.0),
    (None, 1000, 1.0, None),
    (1000, None, 1.0, None),
    (2000, 1000, 0, None),        # sem tempo decorrido: não divide por zero
    (2000, 1000, -3.0, None),     # relógio voltou
])
def test_taxa(atual, anterior, segundos, esperado):
    assert _taxa(atual, anterior, segundos) == esperado


# -------------------------------------------------------------- medidor


def test_medidor_calcula_taxas_entre_duas_medicoes(falso_psutil):
    relogio = Relogio()
    medidor = Medidor(relogio)
    falso_psutil.disco = SimpleNamespace(read_bytes=4000, write_bytes=10000)
    falso_psutil.rede = SimpleNamespace(bytes_sent=500, bytes_recv=3000)
    relogio.avancar(2.0)

    a = medidor.medir()

    assert (a.cpu, a.memoria) == (40.0, 55.0)
    assert (a.disco_leitura, a.disco_escrita) == (2000.0, 5000.0)
    assert (a.rede_envio, a.rede_recebimento) == (250.0, 1500.0)
    assert a.instante == relogio.agora


def test_medidor_usa_sempre_a_diferenca_da_medicao_anterior(falso_psutil):
    relogio = Relogio()
    medidor = Medidor(relogio)
    falso_psutil.disco = SimpleNamespace(read_bytes=1000, write_bytes=0)
    relogio.avancar(1.0)
    medidor.medir()
    falso_psutil.disco = SimpleNamespace(read_bytes=1500, write_bytes=0)
    relogio.avancar(1.0)

    assert medidor.medir().disco_leitura == 500.0  # e não 1500 (desde o início)


def test_medidor_marca_o_inicio_da_cpu_sem_esperar(falso_psutil):
    Medidor(Relogio()).medir()

    assert falso_psutil.chamadas_cpu == [None, None]  # nunca bloqueia: só o amostrador mede


def test_medidor_sem_contadores_de_disco_e_rede(falso_psutil):
    falso_psutil.disco = None
    falso_psutil.rede = None
    relogio = Relogio()
    medidor = Medidor(relogio)
    relogio.avancar(1.0)

    a = medidor.medir()

    assert (a.disco_leitura, a.disco_escrita, a.rede_envio, a.rede_recebimento) == (None,) * 4


def test_medidor_contadores_que_falham_viram_indisponiveis(falso_psutil):
    falso_psutil.disco = PermissionError("negado")
    falso_psutil.rede = OSError("sem interface")
    relogio = Relogio()
    medidor = Medidor(relogio)
    relogio.avancar(1.0)

    a = medidor.medir()

    assert a.disco_leitura is None and a.rede_envio is None
    assert a.cpu == 40.0  # o resto continua funcionando


@pytest.mark.parametrize("bruto, esperado", [(-5.0, 0.0), (250.0, 100.0), (37.5, 37.5)])
def test_medidor_limita_cpu_e_memoria_entre_0_e_100(falso_psutil, bruto, esperado):
    falso_psutil.cpu = bruto
    falso_psutil.memoria = bruto
    relogio = Relogio()
    medidor = Medidor(relogio)
    relogio.avancar(1.0)

    a = medidor.medir()

    assert (a.cpu, a.memoria) == (esperado, esperado)


# ------------------------------------------------------------ amostrador


class MedidorFalso:
    def __init__(self, relogio, falhar_em=()):
        self.relogio = relogio
        self.n = 0
        self.falhar_em = set(falhar_em)

    def medir(self):
        self.n += 1
        self.relogio.avancar(1.0)
        if self.n in self.falhar_em:
            raise psutil.AccessDenied()
        return amostra(self.relogio(), cpu=float(self.n))


def amostrador_de_teste(**kw):
    relogio = Relogio()
    medidor = MedidorFalso(relogio, kw.pop("falhar_em", ()))
    return Amostrador(medidor=medidor, relogio=relogio, **kw), relogio


def test_registrar_guarda_em_ordem():
    a, _ = amostrador_de_teste(intervalo=1.0, janela=10.0)

    for _ in range(3):
        a.registrar()

    assert [x.cpu for x in a.historico()] == [1.0, 2.0, 3.0]
    assert a.ultima().cpu == 3.0


def test_historico_e_um_anel_de_tamanho_fixo():
    a, _ = amostrador_de_teste(intervalo=1.0, janela=5.0)

    for _ in range(12):
        a.registrar()

    assert [x.cpu for x in a.historico()] == [8.0, 9.0, 10.0, 11.0, 12.0]  # só os 5 mais novos


def test_tamanho_do_anel_acompanha_intervalo_e_janela():
    a, _ = amostrador_de_teste(intervalo=2.0, janela=300.0)

    for _ in range(500):
        a.registrar()

    assert len(a.historico()) == 150


def test_historico_pode_ser_limitado_aos_ultimos_segundos():
    a, _ = amostrador_de_teste(intervalo=1.0, janela=100.0)
    for _ in range(10):
        a.registrar()  # instantes 1001..1010; agora = 1010

    ultimos = a.historico(segundos=3)

    assert [x.cpu for x in ultimos] == [7.0, 8.0, 9.0, 10.0]


def test_sem_medicoes_nao_ha_ultima_nem_historico():
    a, _ = amostrador_de_teste()

    assert a.historico() == [] and a.ultima() is None


def test_falha_na_medicao_e_contada_e_nao_derruba():
    a, _ = amostrador_de_teste(intervalo=1.0, janela=10.0, falhar_em={2})

    resultados = [a.registrar() for _ in range(3)]

    assert resultados[1] is None
    assert a.falhas == 1
    assert [x.cpu for x in a.historico()] == [1.0, 3.0]  # a falha não deixa buraco nem lixo


def test_historico_devolve_copia_que_nao_muda_depois():
    a, _ = amostrador_de_teste(intervalo=1.0, janela=10.0)
    a.registrar()
    copia = a.historico()

    a.registrar()

    assert len(copia) == 1


@pytest.mark.parametrize("intervalo, janela", [(0, 10), (-1, 10), (5, 4)])
def test_parametros_invalidos(intervalo, janela):
    with pytest.raises(ValueError):
        Amostrador(intervalo=intervalo, janela=janela, medidor=object())


# --- com a thread de verdade (intervalos curtos)


class MedidorRapido:
    def __init__(self):
        self.n = 0

    def medir(self):
        self.n += 1
        return amostra(time.time(), cpu=float(self.n % 100))


def test_thread_mede_sozinha_e_para_quando_pedido():
    a = Amostrador(intervalo=0.02, janela=2.0, medidor=MedidorRapido())

    with a:
        assert a.em_execucao
        time.sleep(0.3)
    medidas = len(a.historico())

    assert not a.em_execucao
    assert 5 <= medidas <= 20
    time.sleep(0.1)
    assert len(a.historico()) == medidas  # parou de verdade


def test_iniciar_duas_vezes_nao_cria_duas_threads():
    a = Amostrador(intervalo=0.02, janela=1.0, medidor=MedidorRapido())
    a.iniciar()
    try:
        primeira = a._thread
        a.iniciar()

        assert a._thread is primeira
        assert sum(t.name == "zelador-amostrador" for t in threading.enumerate()) == 1
    finally:
        a.parar()


def test_parar_sem_ter_iniciado_e_parar_duas_vezes_nao_quebra():
    a = Amostrador(intervalo=0.02, janela=1.0, medidor=MedidorRapido())

    a.parar()
    a.iniciar()
    a.parar()
    a.parar()

    assert not a.em_execucao


def test_pode_reiniciar_depois_de_parar():
    a = Amostrador(intervalo=0.02, janela=1.0, medidor=MedidorRapido())
    a.iniciar()
    time.sleep(0.1)
    a.parar()
    antes = len(a.historico())

    a.iniciar()
    time.sleep(0.15)
    a.parar()

    assert len(a.historico()) > antes


def test_thread_e_daemon_para_nao_travar_o_encerramento_do_programa():
    a = Amostrador(intervalo=0.02, janela=1.0, medidor=MedidorRapido())
    a.iniciar()
    try:
        assert a._thread.daemon
    finally:
        a.parar()


def test_ler_enquanto_a_thread_escreve_nao_quebra():
    a = Amostrador(intervalo=0.001, janela=0.05, medidor=MedidorRapido())
    erros = []

    def leitor():
        try:
            for _ in range(300):
                a.historico()
                a.ultima()
        except Exception as e:  # noqa: BLE001 - qualquer erro aqui é falha do teste
            erros.append(e)

    with a:
        threads = [threading.Thread(target=leitor) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]

    assert erros == []


# --------------------------------------- psutil de verdade (sem simulação)


def test_medidor_real_devolve_valores_plausiveis():
    medidor = Medidor()
    time.sleep(0.2)

    a = medidor.medir()

    assert 0.0 <= a.cpu <= 100.0
    assert 0.0 < a.memoria <= 100.0
    for taxa in (a.disco_leitura, a.disco_escrita, a.rede_envio, a.rede_recebimento):
        assert taxa is None or taxa >= 0.0
    assert abs(a.instante - time.time()) < 5


def test_amostrador_real_acumula_amostras():
    with Amostrador(intervalo=0.1, janela=5.0) as a:
        time.sleep(0.55)

    assert len(a.historico()) >= 3
    assert a.falhas == 0


class MedidorQueQuebra:
    """Um erro que NÃO é do sistema (um defeito qualquer): a thread tem que sobreviver."""

    def __init__(self):
        self.n = 0

    def medir(self):
        self.n += 1
        if self.n <= 2:
            raise KeyError("defeito inesperado")
        return amostra(time.time(), cpu=1.0)


def test_erro_inesperado_na_thread_nao_mata_o_amostrador(caplog):
    a = Amostrador(intervalo=0.02, janela=2.0, medidor=MedidorQueQuebra())

    with a:
        time.sleep(0.3)

    assert a.falhas == 2
    assert len(a.historico()) >= 3  # voltou a medir depois dos dois erros
    assert "Erro inesperado no amostrador" in caplog.text


def test_erro_inesperado_chamando_registrar_direto_aparece_para_quem_chamou():
    a = Amostrador(intervalo=1.0, janela=10.0, medidor=MedidorQueQuebra())

    with pytest.raises(KeyError):
        a.registrar()  # só a thread engole: quem chama à mão vê o defeito


def test_parar_espera_a_thread_realmente_terminar():
    a = Amostrador(intervalo=5.0, janela=10.0, medidor=MedidorRapido())  # dormindo no intervalo
    a.iniciar()
    thread = a._thread

    a.parar()

    assert not thread.is_alive()  # e não só "esquecida": parar() acorda e espera ela sair


class MedidorLento:
    """Uma medição que leva tempo (disco ocupado, CPU saturada...)."""

    def __init__(self, demora):
        self.demora = demora

    def medir(self):
        time.sleep(self.demora)
        return amostra(time.time())


def test_o_periodo_nao_acumula_atraso_do_tempo_de_medir():
    # intervalo 0,1 s e cada medição leva 0,04 s: com "esperar depois de medir" o período seria
    # 0,14 s (~7 medições em 1 s); agendando contra o relógio, é ~0,10 s (~10 medições).
    a = Amostrador(intervalo=0.1, janela=10.0, medidor=MedidorLento(0.04))

    with a:
        time.sleep(1.05)

    assert 9 <= len(a.historico()) <= 11


class MedidorComUmaPausa:
    """Rápido, exceto a 2ª medição (um travamento do disco, da CPU...)."""

    def __init__(self):
        self.n = 0

    def medir(self):
        self.n += 1
        if self.n == 2:
            time.sleep(0.4)
        return amostra(time.time())


def test_depois_de_um_travamento_nao_ha_rajada_para_recuperar_o_atraso():
    a = Amostrador(intervalo=0.05, janela=10.0, medidor=MedidorComUmaPausa())

    with a:
        time.sleep(1.0)

    instantes = [m.instante for m in a.historico()]
    folgas = [b - c for c, b in zip(instantes, instantes[1:])]
    assert len(instantes) >= 8
    # sem o reajuste, ao destravar viriam ~8 medições coladas (folgas de milissegundos)
    assert min(folgas) >= 0.03
