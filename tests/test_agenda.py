import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from zelador import agenda
from zelador.agenda import (
    comando_da_tarefa,
    descrever_quando,
    dias_validos,
    gerar_xml,
    hora_valida,
    nome_valido,
)

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
AGORA = datetime(2026, 11, 4, 15, 30)


def analisar(xml: str) -> ET.Element:
    """O XML precisa ser bem formado: o cabeçalho UTF-16 é tirado só porque o parser de texto
    não aceita a declaração de codificação numa string."""
    return ET.fromstring(xml.split("?>", 1)[1])


def achar(raiz, caminho):
    return raiz.find(caminho, NS)


# -------------------------------------------------------------- validações


@pytest.mark.parametrize("texto, esperado", [
    ("09:00", "09:00"), ("9:05", "09:05"), ("00:00", "00:00"), ("23:59", "23:59"),
    (" 7:30 ", "07:30"), ("12:00", "12:00"),
])
def test_hora_valida(texto, esperado):
    assert hora_valida(texto) == esperado


@pytest.mark.parametrize("texto", ["24:00", "9:60", "25:10", "abc", "", "9", "9h30", "09:5",
                                   "-1:00", "09:00:00", "9:00 PM"])
def test_hora_invalida(texto):
    with pytest.raises(ValueError, match="hora válida"):
        hora_valida(texto)


@pytest.mark.parametrize("nome", ["Zelador", "Zelador Limpeza", "limpeza-semanal_2", "a", "A.b",
                                  "x" * 100])
def test_nome_valido(nome):
    assert nome_valido(nome) == nome


@pytest.mark.parametrize("nome", ["", " Zelador", "\\Zelador", "pasta\\tarefa", "a/b", "x" * 101,
                                  "-comeca-com-hifen", "nome:ruim", "nome*", "nome?", "ação"])
def test_nome_invalido(nome):
    with pytest.raises(ValueError, match="nome válido"):
        nome_valido(nome)


def test_dias_ficam_na_ordem_da_semana_e_sem_repetir():
    assert dias_validos("qua,seg,qua, SEX ") == ("seg", "qua", "sex")
    assert dias_validos("dom") == ("dom",)
    assert dias_validos("seg,ter,qua,qui,sex,sab,dom") == tuple(agenda.DIAS_DA_SEMANA)


@pytest.mark.parametrize("texto", ["", " , ", ","])
def test_dias_vazios_sao_recusados(texto):
    with pytest.raises(ValueError, match="ao menos um dia"):
        dias_validos(texto)


def test_dia_desconhecido_lista_os_validos():
    with pytest.raises(ValueError) as erro:
        dias_validos("seg,segunda,xyz")

    mensagem = str(erro.value)
    assert "segunda" in mensagem and "xyz" in mensagem and "sab" in mensagem


# --------------------------------------------------------------- comando


@pytest.fixture
def python_falso(tmp_path, monkeypatch):
    """Um 'python' com e sem pythonw.exe ao lado."""
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_text("")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.delattr(sys, "frozen", raising=False)
    return scripts


def test_usa_pythonw_quando_existe_para_nao_abrir_janela(python_falso):
    (python_falso / "pythonw.exe").write_text("")

    executavel, argumentos = comando_da_tarefa(Path("regras.yml"))

    assert executavel == str(python_falso / "pythonw.exe")
    assert argumentos.startswith("-m zelador rodar --config ")


def test_cai_no_python_normal_se_nao_houver_pythonw(python_falso):
    executavel, _ = comando_da_tarefa(Path("regras.yml"))

    assert executavel == str(python_falso / "python.exe")


def test_os_caminhos_viram_absolutos_e_entre_aspas(python_falso):
    _, argumentos = comando_da_tarefa(Path("minhas regras") / "regras.yml",
                                      pasta_log=Path("logs de teste"))

    entre_aspas = re.findall(r'"([^"]*)"', argumentos)

    assert len(entre_aspas) == 2  # o config e a pasta de log, cada um inteiro entre aspas
    assert all(os.path.isabs(c) for c in entre_aspas)  # a tarefa roda de outra pasta de trabalho
    assert entre_aspas[0].endswith(os.path.join("minhas regras", "regras.yml"))
    assert entre_aspas[1].endswith("logs de teste")  # os espaços ficam dentro das aspas


def test_simulacao_acrescenta_dry_run_no_fim(python_falso):
    _, com = comando_da_tarefa(Path("r.yml"), simulacao=True)
    _, sem = comando_da_tarefa(Path("r.yml"), simulacao=False)

    assert com.endswith(" --dry-run")
    assert "--dry-run" not in sem


def test_sem_pasta_de_log_o_argumento_nao_aparece(python_falso):
    _, argumentos = comando_da_tarefa(Path("r.yml"))

    assert "--log-dir" not in argumentos


def test_programa_empacotado_e_o_proprio_comando(tmp_path, monkeypatch):
    exe = tmp_path / "Zelador.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    executavel, argumentos = comando_da_tarefa(Path("r.yml"))

    assert executavel == str(exe)
    assert argumentos.startswith("rodar --config ")  # sem "-m zelador": o .exe já é o zelador


def test_caminho_com_aspas_e_recusado(python_falso):
    with pytest.raises(ValueError, match="aspas"):
        comando_da_tarefa(Path('pasta"ruim') / "r.yml")


@pytest.mark.parametrize("caminho, citado", [
    ("C:\\", '"C:\\\\"'),  # raiz de disco: a barra final é dobrada
    ("C:\\pasta\\\\", '"C:\\pasta\\\\\\\\"'),
    ("C:\\pasta", '"C:\\pasta"'),  # barras no meio ficam como estão
])
def test_barra_no_fim_e_dobrada_para_nao_virar_aspa(caminho, citado):
    assert agenda._citar(caminho) == citado


@pytest.mark.skipif(sys.platform != "win32", reason="usa a divisão de argumentos do Windows")
def test_o_windows_entrega_a_raiz_do_disco_inteira_ao_programa():
    import ctypes
    from ctypes import wintypes

    dividir = ctypes.windll.shell32.CommandLineToArgvW
    dividir.restype = ctypes.POINTER(wintypes.LPWSTR)
    quantos = ctypes.c_int()
    linha = f"zelador.exe --log-dir {agenda._citar('C:' + chr(92))} --dry-run"

    partes = dividir(linha, ctypes.byref(quantos))

    assert [partes[i] for i in range(quantos.value)] == [
        "zelador.exe", "--log-dir", "C:" + chr(92), "--dry-run"]


# ------------------------------------------------------------------- XML


def gerar(frequencia="diaria", hora="09:30", dias=(), executavel="C:\\py\\pythonw.exe",
          argumentos='-m zelador rodar --config "C:\\r.yml"', descricao="desc"):
    return gerar_xml(frequencia, hora, dias, executavel, argumentos, descricao, agora=AGORA)


def test_xml_diario():
    raiz = analisar(gerar("diaria", "09:30"))

    gatilho = achar(raiz, "t:Triggers/t:CalendarTrigger")
    assert achar(gatilho, "t:StartBoundary").text == "2026-11-04T09:30:00"
    assert achar(gatilho, "t:ScheduleByDay/t:DaysInterval").text == "1"
    assert achar(gatilho, "t:Enabled").text == "true"


def test_xml_semanal_lista_so_os_dias_pedidos():
    raiz = analisar(gerar("semanal", "08:00", ("seg", "qua", "sab")))

    dias = achar(raiz, "t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:DaysOfWeek")
    assert [d.tag.split("}")[1] for d in dias] == ["Monday", "Wednesday", "Saturday"]
    assert achar(raiz, "t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:WeeksInterval").text == "1"
    assert achar(raiz, "t:Triggers/t:CalendarTrigger/t:StartBoundary").text.endswith("T08:00:00")


def test_xml_ao_entrar_tem_espera_e_o_usuario():
    raiz = analisar(gerar("aoentrar"))

    gatilho = achar(raiz, "t:Triggers/t:LogonTrigger")
    assert achar(gatilho, "t:Delay").text == agenda.ESPERA_APOS_ENTRAR
    assert achar(gatilho, "t:UserId").text  # só para o usuário atual, não para todo mundo
    assert achar(raiz, "t:Triggers/t:CalendarTrigger") is None


def test_xml_tem_um_unico_gatilho_do_tipo_pedido():
    for frequencia, dias in (("diaria", ()), ("semanal", ("seg",)), ("aoentrar", ())):
        raiz = analisar(gerar(frequencia, dias=dias))

        assert len(achar(raiz, "t:Triggers")) == 1


def test_xml_roda_sem_administrador_e_so_com_o_usuario_logado():
    raiz = analisar(gerar())

    assert achar(raiz, "t:Principals/t:Principal/t:RunLevel").text == "LeastPrivilege"
    assert achar(raiz, "t:Principals/t:Principal/t:LogonType").text == "InteractiveToken"


def test_xml_configuracoes_de_seguranca_e_robustez():
    raiz = analisar(gerar())
    config = {filho.tag.split("}")[1]: filho.text for filho in achar(raiz, "t:Settings")}

    assert config["StartWhenAvailable"] == "true"  # se o PC estava desligado, recupera depois
    assert config["MultipleInstancesPolicy"] == "IgnoreNew"  # nunca duas execuções ao mesmo tempo
    assert config["ExecutionTimeLimit"] == agenda.LIMITE_DE_TEMPO
    assert config["DisallowStartIfOnBatteries"] == "false"  # notebook na bateria também limpa
    assert config["StopIfGoingOnBatteries"] == "false"
    assert config["Enabled"] == "true"


def test_xml_acao_tem_comando_e_argumentos():
    raiz = analisar(gerar(executavel="C:\\py\\pythonw.exe",
                          argumentos='-m zelador rodar --config "C:\\r.yml"'))

    assert achar(raiz, "t:Actions/t:Exec/t:Command").text == "C:\\py\\pythonw.exe"
    argumentos = achar(raiz, "t:Actions/t:Exec/t:Arguments").text
    assert argumentos == '-m zelador rodar --config "C:\\r.yml"'


def test_xml_escapa_caracteres_especiais_e_acentos():
    raiz = analisar(gerar(executavel="C:\\Arq & <Prog>\\py.exe",
                          argumentos='--config "C:\\Meus <docs> & mais\\ação.yml"',
                          descricao="Limpeza <semanal> & \"rápida\""))

    assert achar(raiz, "t:Actions/t:Exec/t:Command").text == "C:\\Arq & <Prog>\\py.exe"
    assert "ação.yml" in achar(raiz, "t:Actions/t:Exec/t:Arguments").text
    assert achar(raiz, "t:RegistrationInfo/t:Description").text == "Limpeza <semanal> & \"rápida\""


def test_xml_declara_utf16_como_o_agendador_espera():
    xml = gerar()

    assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    assert analisar(xml).tag == "{http://schemas.microsoft.com/windows/2004/02/mit/task}Task"
    assert xml.encode("utf-16")[:2] in (b"\xff\xfe", b"\xfe\xff")  # com BOM, como o schtasks lê


def test_data_de_inicio_vem_do_agora_informado():
    outro = datetime(2027, 1, 15, 23, 59)

    xml = gerar_xml("diaria", "06:05", (), "x", "y", "d", agora=outro)

    assert "<StartBoundary>2027-01-15T06:05:00</StartBoundary>" in xml


def test_sem_agora_usa_a_data_de_hoje():
    xml = gerar_xml("diaria", "06:05", (), "x", "y", "d")

    assert f"<StartBoundary>{datetime.now():%Y-%m-%d}T06:05:00" in xml


def test_frequencia_desconhecida_e_recusada():
    with pytest.raises(ValueError, match="frequência desconhecida"):
        gerar_xml("mensal", "09:00", (), "x", "y", "d")


def test_semanal_sem_dias_e_recusado():
    with pytest.raises(ValueError, match="ao menos um dia"):
        gerar_xml("semanal", "09:00", (), "x", "y", "d")


# ---------------------------------------------------------- descrição humana


@pytest.mark.parametrize("frequencia, dias, esperado", [
    ("diaria", (), "todo dia às 09:30"),
    ("semanal", ("seg",), "toda semana (segundas), às 09:30"),
    ("semanal", ("seg", "qua"), "toda semana (segundas e quartas), às 09:30"),
    ("semanal", ("seg", "qua", "sab"), "toda semana (segundas, quartas e sábados), às 09:30"),
    ("semanal", ("dom",), "toda semana (domingos), às 09:30"),
    ("aoentrar", (), "2 minutos depois que você entrar no Windows"),
])
def test_descrever_quando(frequencia, dias, esperado):
    assert descrever_quando(frequencia, "09:30", dias) == esperado
