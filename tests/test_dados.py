import json
import time

import psutil
import pytest

from zelador import sistema
from zelador.painel import dados
from zelador.painel.amostrador import Amostra, Amostrador

GB = 1024**3


class AmostradorFalso:
    """Só o que dados.py usa do Amostrador: historico(segundos) e ultima()."""

    def __init__(self, amostras):
        self.amostras = amostras
        self.pedidos = []

    def historico(self, segundos=None):
        self.pedidos.append(segundos)
        return list(self.amostras)

    def ultima(self):
        return self.amostras[-1] if self.amostras else None


def amostra(instante, cpu=10.0, memoria=50.0, **taxas):
    base = dict(disco_leitura=1000.0, disco_escrita=2000.0, rede_envio=300.0,
                rede_recebimento=400.0)
    base.update(taxas)
    return Amostra(instante, cpu, memoria, **base)


@pytest.fixture
def sistema_falso(monkeypatch):
    estado = {"processos": [sistema.Processo(1, "a.exe", 900, None),
                            sistema.Processo(2, "b.exe", None, None)]}
    monkeypatch.setattr(sistema, "coletar_memoria",
                        lambda: sistema.UsoMemoria(16 * GB, 8 * GB, 50.0, 4 * GB, GB))
    monkeypatch.setattr(sistema, "coletar_discos", lambda: (
        sistema.UsoDisco("C:\\", "C:\\", "NTFS", 200 * GB, 50 * GB, 25.0),))
    monkeypatch.setattr(sistema, "segundos_ligado", lambda: 3600.0)
    monkeypatch.setattr(sistema, "listar_processos",
                        lambda ordenar, limite: estado["processos"][:limite])
    monkeypatch.setattr(dados.psutil, "cpu_count", lambda logical=True: 8 if logical else 4)
    return estado


# ------------------------------------------------------------ desempenho


def test_desempenho_traz_o_estado_atual(sistema_falso):
    agora = time.time()
    a = AmostradorFalso([amostra(agora - 2, cpu=20.0), amostra(agora - 1, cpu=33.33)])

    d = dados.desempenho(a)

    assert d["cpu"] == {"percentual": 33.3, "nucleos_logicos": 8, "nucleos_fisicos": 4}
    assert d["memoria"] == {"total": 16 * GB, "usada": 8 * GB, "percentual": 50.0,
                            "swap_total": 4 * GB, "swap_usada": GB}
    assert d["segundos_ligado"] == 3600.0
    assert abs(d["instante"] - time.time()) < 5


def test_historico_vem_em_colunas_do_mesmo_tamanho(sistema_falso):
    agora = time.time()
    a = AmostradorFalso([amostra(agora - 3), amostra(agora - 2), amostra(agora - 1)])

    h = dados.desempenho(a)["historico"]

    assert set(h) == {"segundos_atras", "cpu", "memoria", "disco_leitura", "disco_escrita",
                      "rede_envio", "rede_recebimento"}
    assert {len(v) for v in h.values()} == {3}


def test_tempo_do_historico_e_relativo_ao_agora_e_nunca_positivo(sistema_falso):
    agora = time.time()
    a = AmostradorFalso([amostra(agora - 10), amostra(agora - 5), amostra(agora - 0.5)])

    atras = dados.desempenho(a)["historico"]["segundos_atras"]

    assert atras == pytest.approx([-10, -5, -0.5], abs=0.2)
    assert all(t <= 0.05 for t in atras)
    assert atras == sorted(atras)  # do mais antigo para o mais novo


def test_valores_do_historico_sao_arredondados(sistema_falso):
    a = AmostradorFalso([amostra(time.time(), cpu=12.3456, memoria=67.891,
                                 disco_leitura=1234.567, rede_envio=0.4)])

    h = dados.desempenho(a)["historico"]

    assert h["cpu"] == [12.3] and h["memoria"] == [67.9]
    assert h["disco_leitura"] == [1235.0] and h["rede_envio"] == [0.0]


def test_taxa_indisponivel_continua_nula_e_nao_vira_zero(sistema_falso):
    a = AmostradorFalso([amostra(time.time(), disco_leitura=None, rede_envio=None)])

    h = dados.desempenho(a)["historico"]

    assert h["disco_leitura"] == [None] and h["rede_envio"] == [None]
    assert h["disco_escrita"] == [2000.0]


def test_sem_nenhuma_medicao_ainda(sistema_falso):
    d = dados.desempenho(AmostradorFalso([]))

    assert d["cpu"]["percentual"] is None  # a tela mostra "—" até a primeira medição
    assert all(v == [] for v in d["historico"].values())
    assert d["memoria"]["total"] == 16 * GB  # o resto não depende do amostrador


def test_o_periodo_pedido_chega_ao_amostrador(sistema_falso):
    a = AmostradorFalso([])

    dados.desempenho(a)
    dados.desempenho(a, segundos=60)

    assert a.pedidos == [dados.SEGUNDOS_DE_HISTORICO, 60]


# --------------------------------------------- armazenamento e processos


def test_armazenamento_lista_os_discos(sistema_falso):
    assert dados.armazenamento() == [{
        "dispositivo": "C:\\", "ponto_de_montagem": "C:\\", "sistema_de_arquivos": "NTFS",
        "total": 200 * GB, "usado": 50 * GB, "percentual": 25.0}]


def test_processos_respeitam_o_limite_e_mantem_o_acesso_negado_como_nulo(sistema_falso):
    todos = dados.processos(limite=2)

    assert todos == [{"pid": 1, "nome": "a.exe", "memoria": 900, "cpu": None},
                     {"pid": 2, "nome": "b.exe", "memoria": None, "cpu": None}]
    assert len(dados.processos(limite=1)) == 1


def test_limite_de_processos_padrao(sistema_falso, monkeypatch):
    pedidos = []
    monkeypatch.setattr(sistema, "listar_processos",
                        lambda ordenar, limite: pedidos.append((ordenar, limite)) or [])

    dados.processos()

    assert pedidos == [("memoria", dados.PROCESSOS_PADRAO)]


# ---------------------------------------------------------------- retrato


def test_retrato_junta_as_tres_secoes(sistema_falso):
    a = AmostradorFalso([amostra(time.time())])

    r = dados.retrato(a, limite_processos=1)

    assert r["versao"] == dados.VERSAO and r["erros"] == []
    assert set(r) == {"versao", "erros", "desempenho", "armazenamento", "processos"}
    assert len(r["processos"]) == 1
    assert r["armazenamento"][0]["dispositivo"] == "C:\\"


@pytest.mark.parametrize("secao, alvo, erro", [
    ("processos", "listar_processos", psutil.AccessDenied()),
    ("armazenamento", "coletar_discos", OSError("sem disco")),
    ("desempenho", "coletar_memoria", psutil.Error()),
])
def test_uma_secao_com_falha_nao_derruba_as_outras(sistema_falso, monkeypatch, secao, alvo, erro):
    def falhar(*args, **kwargs):
        raise erro

    monkeypatch.setattr(sistema, alvo, falhar)

    r = dados.retrato(AmostradorFalso([amostra(time.time())]))

    assert r[secao] is None and r["erros"] == [secao]
    for outra in {"desempenho", "armazenamento", "processos"} - {secao}:
        assert r[outra] is not None


def test_varias_secoes_com_falha_aparecem_na_ordem(sistema_falso, monkeypatch):
    def falhar(*args, **kwargs):
        raise OSError("x")

    monkeypatch.setattr(sistema, "coletar_discos", falhar)
    monkeypatch.setattr(sistema, "listar_processos", falhar)

    assert dados.retrato(AmostradorFalso([]))["erros"] == ["armazenamento", "processos"]


def test_erro_que_nao_e_do_sistema_nao_e_escondido(sistema_falso, monkeypatch):
    def defeito(*args, **kwargs):
        raise KeyError("bug nosso")

    monkeypatch.setattr(sistema, "coletar_discos", defeito)

    with pytest.raises(KeyError):
        dados.retrato(AmostradorFalso([]))  # só erros do sistema viram "indisponível"


def test_retrato_sempre_vira_json_valido(sistema_falso):
    agora = time.time()
    a = AmostradorFalso([amostra(agora - 1, disco_leitura=None), amostra(agora)])

    texto = json.dumps(dados.retrato(a), allow_nan=False)  # NaN/infinito levantariam erro

    assert json.loads(texto)["versao"] == dados.VERSAO


def test_o_esquema_e_um_contrato_com_o_html(sistema_falso):
    """Se este teste quebrar, o HTML precisa ser atualizado junto: ele lê estes nomes."""
    a = AmostradorFalso([amostra(time.time())])

    r = dados.retrato(a)

    assert r["versao"] == 1  # literal de propósito: mudar o esquema tem que ser consciente
    assert set(r["desempenho"]) == {"instante", "cpu", "memoria", "segundos_ligado",
                                    "historico"}
    assert set(r["desempenho"]["cpu"]) == {"percentual", "nucleos_logicos", "nucleos_fisicos"}
    assert set(r["desempenho"]["memoria"]) == {"total", "usada", "percentual", "swap_total",
                                               "swap_usada"}
    assert set(r["armazenamento"][0]) == {"dispositivo", "ponto_de_montagem",
                                          "sistema_de_arquivos", "total", "usado", "percentual"}
    assert set(r["processos"][0]) == {"pid", "nome", "memoria", "cpu"}


# ------------------------------------ com o sistema de verdade (sem simulação)


def test_retrato_real_e_json_e_plausivel():
    with Amostrador(intervalo=0.1, janela=5.0) as amostras:
        time.sleep(0.45)
        r = dados.retrato(amostras, limite_processos=3)

    json.dumps(r, allow_nan=False)
    assert r["erros"] == []
    h = r["desempenho"]["historico"]
    assert len(h["cpu"]) >= 2 and len({len(v) for v in h.values()}) == 1
    assert 0 < r["desempenho"]["memoria"]["percentual"] <= 100
    assert len(r["processos"]) == 3
    assert len(r["armazenamento"]) >= 1
