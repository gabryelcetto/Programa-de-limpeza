import os
import time
from types import SimpleNamespace

import psutil
import pytest

from zelador import sistema

GB = 1024**3


def particao(dispositivo, ponto, fstype="NTFS", opts="rw,fixed"):
    return SimpleNamespace(device=dispositivo, mountpoint=ponto, fstype=fstype, opts=opts)


class FalsoProcesso:
    """Imita psutil.Process: `.info` (já lido) e cpu_percent() em duas leituras."""

    def __init__(self, pid, nome, rss, cpu=0.0, erro_na_cpu=None, erro_no_info=None):
        self._info = {"pid": pid, "name": nome,
                      "memory_info": SimpleNamespace(rss=rss) if rss is not None else None}
        self._cpu = cpu
        self._erro_na_cpu = erro_na_cpu
        self._erro_no_info = erro_no_info
        self.leituras_de_cpu = 0

    @property
    def info(self):
        if self._erro_no_info:
            raise self._erro_no_info
        return self._info

    def cpu_percent(self, intervalo=None):
        self.leituras_de_cpu += 1
        if self._erro_na_cpu:
            raise self._erro_na_cpu
        return self._cpu


@pytest.fixture
def falso_psutil(monkeypatch):
    """psutil com valores conhecidos; cada teste ajusta o que precisa."""
    estado = SimpleNamespace(cpu=37.5, intervalos=[], logicos=8, fisicos=4,
                             ram=SimpleNamespace(total=16 * GB, used=6 * GB, percent=37.5),
                             swap=SimpleNamespace(total=4 * GB, used=1 * GB),
                             particoes=[], usos={}, boot=time.time() - 3600, processos=[])

    def cpu_percent(interval=None):
        estado.intervalos.append(interval)
        return estado.cpu

    def cpu_count(logical=True):
        return estado.logicos if logical else estado.fisicos

    def disk_usage(ponto):
        uso = estado.usos[ponto]
        if isinstance(uso, Exception):
            raise uso
        return uso

    monkeypatch.setattr(sistema.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(sistema.psutil, "cpu_count", cpu_count)
    monkeypatch.setattr(sistema.psutil, "virtual_memory", lambda: estado.ram)
    monkeypatch.setattr(sistema.psutil, "swap_memory", lambda: estado.swap)
    monkeypatch.setattr(sistema.psutil, "disk_partitions", lambda all=False: estado.particoes)
    monkeypatch.setattr(sistema.psutil, "disk_usage", disk_usage)
    monkeypatch.setattr(sistema.psutil, "boot_time", lambda: estado.boot)
    monkeypatch.setattr(sistema.psutil, "process_iter", lambda attrs=None: iter(estado.processos))
    return estado


@pytest.fixture
def sem_espera(monkeypatch):
    esperas = []
    monkeypatch.setattr(sistema.time, "sleep", esperas.append)
    return esperas


# ---------------------------------------------------------------- status


def test_status_traduz_os_valores_do_psutil(falso_psutil):
    status = sistema.coletar_status(intervalo_cpu=0.25)

    assert status.cpu == sistema.UsoCpu(37.5, 8, 4)
    assert status.memoria == sistema.UsoMemoria(16 * GB, 6 * GB, 37.5, 4 * GB, 1 * GB)
    assert falso_psutil.intervalos == [0.25]  # a CPU é medida durante o intervalo pedido
    assert 3599 <= status.segundos_ligado <= 3700


def test_status_sem_informacao_de_nucleos_fisicos(falso_psutil):
    falso_psutil.fisicos = None

    assert sistema.coletar_status(0).cpu.nucleos_fisicos is None


def test_status_sem_contagem_de_nucleos_assume_um(falso_psutil):
    falso_psutil.logicos = None

    assert sistema.coletar_status(0).cpu.nucleos_logicos == 1


def test_status_sem_swap(falso_psutil):
    falso_psutil.swap = SimpleNamespace(total=0, used=0)

    memoria = sistema.coletar_status(0).memoria

    assert (memoria.swap_total, memoria.swap_usada) == (0, 0)


def test_boot_no_futuro_nao_gera_tempo_negativo(falso_psutil):
    falso_psutil.boot = time.time() + 1000  # relógio ajustado depois do boot

    assert sistema.coletar_status(0).segundos_ligado == 0.0


def test_discos_calculam_o_percentual(falso_psutil):
    falso_psutil.particoes = [particao("C:\\", "C:\\")]
    falso_psutil.usos = {"C:\\": SimpleNamespace(total=200 * GB, used=50 * GB)}

    (disco,) = sistema.coletar_status(0).discos

    assert disco == sistema.UsoDisco("C:\\", "C:\\", "NTFS", 200 * GB, 50 * GB, 25.0)


def test_discos_ignoram_o_que_nao_e_volume_de_dados(falso_psutil):
    uso = SimpleNamespace(total=100 * GB, used=10 * GB)
    falso_psutil.particoes = [
        particao("D:\\", "D:\\", fstype="", opts="cdrom"),          # leitor sem mídia
        particao("E:\\", "E:\\", fstype="CDFS", opts="ro,cdrom"),   # CD com mídia
        particao("F:\\", "F:\\", fstype=""),                        # sem sistema de arquivos
        particao("G:\\", "G:\\"),                                   # acesso negado
        particao("H:\\", "H:\\"),                                   # volume vazio (total 0)
        particao("C:\\", "C:\\"),                                   # o único bom
        particao("C:\\", "C:\\"),                                   # repetido
    ]
    falso_psutil.usos = {
        "E:\\": uso, "F:\\": uso, "D:\\": uso,
        "G:\\": PermissionError("negado"),
        "H:\\": SimpleNamespace(total=0, used=0),
        "C:\\": uso,
    }

    discos = sistema.coletar_status(0).discos

    assert [d.ponto_de_montagem for d in discos] == ["C:\\"]


def test_discos_mantem_a_ordem_do_sistema(falso_psutil):
    uso = SimpleNamespace(total=GB, used=GB // 2)
    falso_psutil.particoes = [particao("C:\\", "C:\\"), particao("D:\\", "D:\\")]
    falso_psutil.usos = {"C:\\": uso, "D:\\": uso}

    assert [d.dispositivo for d in sistema.coletar_status(0).discos] == ["C:\\", "D:\\"]


def test_percentual_com_total_zero_nao_divide_por_zero():
    assert sistema._percentual(0, 0) == 0.0
    assert sistema._percentual(1, 3) == 33.3


# ------------------------------------------------------------ processos


def test_processos_por_memoria_do_maior_para_o_menor(falso_psutil, sem_espera):
    falso_psutil.processos = [FalsoProcesso(1, "pequeno", 10), FalsoProcesso(2, "grande", 900),
                              FalsoProcesso(3, "medio", 500)]

    processos = sistema.listar_processos("memoria", limite=10)

    assert [p.nome for p in processos] == ["grande", "medio", "pequeno"]
    assert all(p.cpu is None for p in processos)
    assert sem_espera == []  # só ordenar por CPU exige esperar uma segunda leitura


def test_processos_respeitam_o_limite(falso_psutil, sem_espera):
    falso_psutil.processos = [FalsoProcesso(i, f"p{i}", i * 10) for i in range(1, 8)]

    processos = sistema.listar_processos("memoria", limite=3)

    assert [p.pid for p in processos] == [7, 6, 5]


def test_limite_maior_que_o_numero_de_processos(falso_psutil, sem_espera):
    falso_psutil.processos = [FalsoProcesso(1, "a", 1), FalsoProcesso(2, "b", 2)]

    assert len(sistema.listar_processos("memoria", limite=50)) == 2


def test_system_idle_process_e_excluido(falso_psutil, sem_espera):
    falso_psutil.processos = [FalsoProcesso(0, "System Idle Process", 10**9, cpu=380.0),
                              FalsoProcesso(4, "System", 100)]

    assert [p.pid for p in sistema.listar_processos("memoria")] == [4]


def test_acesso_negado_na_memoria_vai_para_o_fim(falso_psutil, sem_espera):
    falso_psutil.processos = [FalsoProcesso(1, "protegido", None), FalsoProcesso(2, "comum", 5),
                              FalsoProcesso(3, "outro", 50)]

    processos = sistema.listar_processos("memoria")

    assert [p.nome for p in processos] == ["outro", "comum", "protegido"]
    assert processos[-1].memoria is None


def test_nome_ausente_vira_interrogacao(falso_psutil, sem_espera):
    falso_psutil.processos = [FalsoProcesso(1, None, 5)]

    assert sistema.listar_processos("memoria")[0].nome == "?"


@pytest.mark.parametrize("erro", [psutil.NoSuchProcess(1), psutil.AccessDenied(1),
                                  psutil.ZombieProcess(1)])
def test_processo_que_some_ou_e_negado_nao_derruba_a_lista(falso_psutil, sem_espera, erro):
    falso_psutil.processos = [FalsoProcesso(1, "some", 100, erro_no_info=erro),
                              FalsoProcesso(2, "fica", 50)]

    assert [p.nome for p in sistema.listar_processos("memoria")] == ["fica"]


def test_processos_por_cpu_normalizam_pelo_numero_de_nucleos(falso_psutil, sem_espera):
    falso_psutil.logicos = 4
    falso_psutil.processos = [FalsoProcesso(1, "um_nucleo", 10, cpu=100.0),
                              FalsoProcesso(2, "meio", 20, cpu=50.0),
                              FalsoProcesso(3, "ocioso", 30, cpu=0.0)]

    processos = sistema.listar_processos("cpu", intervalo_cpu=0.7)

    assert [(p.nome, p.cpu) for p in processos] == [("um_nucleo", 25.0), ("meio", 12.5),
                                                    ("ocioso", 0.0)]
    assert sem_espera == [0.7]  # esperou o intervalo entre as duas leituras
    assert all(p.memoria is not None for p in processos)


def test_cpu_le_cada_processo_duas_vezes(falso_psutil, sem_espera):
    proc = FalsoProcesso(1, "a", 10, cpu=10.0)
    falso_psutil.processos = [proc]

    sistema.listar_processos("cpu")

    assert proc.leituras_de_cpu == 2  # a 1ª só marca o ponto de partida


def test_processo_que_some_durante_a_medicao_de_cpu_e_ignorado(falso_psutil, sem_espera):
    falso_psutil.processos = [
        FalsoProcesso(1, "some_na_cpu", 10, erro_na_cpu=psutil.NoSuchProcess(1)),
        FalsoProcesso(2, "negado_na_cpu", 20, erro_na_cpu=psutil.AccessDenied(2)),
        FalsoProcesso(3, "normal", 30, cpu=40.0),
    ]

    assert [p.nome for p in sistema.listar_processos("cpu")] == ["normal"]


def test_ordenar_por_memoria_nao_mede_cpu(falso_psutil, sem_espera):
    proc = FalsoProcesso(1, "a", 10)
    falso_psutil.processos = [proc]

    sistema.listar_processos("memoria")

    assert proc.leituras_de_cpu == 0


@pytest.mark.parametrize("ordenar", ["", "nome", "MEMORIA", None])
def test_ordenacao_invalida(falso_psutil, ordenar):
    with pytest.raises(ValueError, match="ordenação inválida"):
        sistema.listar_processos(ordenar)


@pytest.mark.parametrize("limite", [0, -1])
def test_limite_invalido(falso_psutil, limite):
    with pytest.raises(ValueError, match="pelo menos 1"):
        sistema.listar_processos("memoria", limite=limite)


# --------------------------------------- psutil de verdade (sem simulação)


def test_status_real_tem_valores_plausiveis():
    status = sistema.coletar_status(intervalo_cpu=0.1)

    assert 0.0 <= status.cpu.percentual <= 100.0
    assert status.cpu.nucleos_logicos >= 1
    assert status.memoria.total > 0
    assert 0 < status.memoria.usada <= status.memoria.total
    assert 0.0 <= status.memoria.percentual <= 100.0
    assert status.segundos_ligado > 0
    assert len(status.discos) >= 1
    for disco in status.discos:
        assert 0 < disco.usado <= disco.total
        assert 0.0 <= disco.percentual <= 100.0


def test_processos_reais_incluem_este_proprio_processo():
    processos = sistema.listar_processos("memoria", limite=10**6)

    este = [p for p in processos if p.pid == os.getpid()]
    assert len(este) == 1
    assert este[0].memoria and este[0].memoria > 1024 * 1024  # um Python usa mais de 1 MB
    assert all(p.pid != 0 for p in processos)


def test_processos_reais_por_cpu():
    processos = sistema.listar_processos("cpu", limite=5, intervalo_cpu=0.1)

    assert 1 <= len(processos) <= 5
    assert all(p.cpu is not None and p.cpu >= 0 for p in processos)
    assert [p.cpu for p in processos] == sorted((p.cpu for p in processos), reverse=True)
