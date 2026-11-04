import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zelador import agenda, caminhos, cli, comando_agenda, logs
from zelador.comando_agenda import resumir_xml


@pytest.fixture(autouse=True)
def fechar_logs():
    """Todo cli.main abre um arquivo de log; no Windows isso impede apagar o tmp_path."""
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


class AgendadorFalso:
    """No lugar do `schtasks`: guarda as tarefas em memória e registra o que foi pedido."""

    def __init__(self):
        self.tarefas = {}  # nome -> texto do XML
        self.chamadas = []  # tuplas com os argumentos de cada chamada
        self.arquivos_xml = []  # caminhos temporários recebidos em /Create
        self.falhar_em = {}  # "/Create" -> (codigo, mensagem)
        self.erro = None  # exceção a levantar em qualquer chamada

    def __call__(self, *argumentos):
        self.chamadas.append(argumentos)
        if self.erro:
            raise self.erro
        operacao = argumentos[0]
        nome = argumentos[argumentos.index("/TN") + 1]
        if operacao in self.falhar_em:
            codigo, mensagem = self.falhar_em[operacao]
            return SimpleNamespace(returncode=codigo, stdout="", stderr=mensagem)
        if operacao == "/Create":
            arquivo = argumentos[argumentos.index("/XML") + 1]
            self.arquivos_xml.append(arquivo)
            self.tarefas[nome] = Path(arquivo).read_bytes().decode("utf-16")
            return SimpleNamespace(returncode=0, stdout="ÊXITO", stderr="")
        if operacao == "/Query":
            if nome not in self.tarefas:
                return SimpleNamespace(returncode=1, stdout="", stderr="não encontrada")
            return SimpleNamespace(returncode=0, stdout=self.tarefas[nome] if "/XML" in argumentos
                                   else "", stderr="")
        if operacao == "/Delete":
            self.tarefas.pop(nome, None)
            return SimpleNamespace(returncode=0, stdout="ÊXITO", stderr="")
        raise AssertionError(f"chamada inesperada ao schtasks: {argumentos}")

    def operacoes(self):
        return [c[0] for c in self.chamadas]


@pytest.fixture
def agendador(monkeypatch):
    falso = AgendadorFalso()
    monkeypatch.setattr(comando_agenda, "_schtasks", falso)
    monkeypatch.setattr(comando_agenda.sys, "platform", "win32")
    return falso


@pytest.fixture(autouse=True)
def pastas_falsas(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(caminhos, "obter_pasta_documentos", lambda: tmp_path / "Documentos")


@pytest.fixture
def config(tmp_path):
    arquivo = tmp_path / "minhas regras" / "regras.yml"
    arquivo.parent.mkdir()
    arquivo.write_text("""
regras:
  - nome: Limpar para a Lixeira
    pasta: "{downloads}"
    condicoes: {idade_dias: 30}
    acao: lixeira
  - nome: Organizar PDFs
    pasta: "{downloads}"
    condicoes: {extensoes: [pdf], idade_dias: 1}
    acao: {mover: "{documentos}/PDFs"}
""", encoding="utf-8")
    return arquivo


def escrever_regras(tmp_path, corpo: str) -> Path:
    arquivo = tmp_path / "regras_teste.yml"
    arquivo.write_text("regras:\n" + corpo, encoding="utf-8")
    return arquivo


@pytest.fixture
def cli_com(tmp_path):
    def executar(*args):
        return cli.main([*args, "--log-dir", str(tmp_path / "logs")])
    return executar


def criar(cli_com, config, *extras):
    return cli_com("agendar", "criar", "--config", str(config), *extras)


# ------------------------------------------------------------------ criar


def test_criar_registra_a_tarefa_com_o_xml_certo(cli_com, agendador, config, tmp_path):
    codigo = criar(cli_com, config)

    assert codigo == 0
    assert agendador.operacoes() == ["/Query", "/Create"]
    criacao = agendador.chamadas[1]
    assert criacao[:5] == ("/Create", "/TN", "Zelador", "/XML", agendador.arquivos_xml[0])
    assert criacao[-1] == "/F"  # atualiza se já existir, em vez de falhar
    resumo = resumir_xml(agendador.tarefas["Zelador"])
    assert resumo["gatilhos"] == ["todo dia às 09:00"] and resumo["ativada"]
    assert f'--config "{os.path.abspath(config)}"' in resumo["comando"]
    assert "--dry-run" not in resumo["comando"]


def test_o_xml_temporario_e_apagado_depois(cli_com, agendador, config):
    criar(cli_com, config)

    assert agendador.arquivos_xml and not Path(agendador.arquivos_xml[0]).exists()


def test_o_xml_enviado_e_utf16_e_guarda_acentos_e_espacos_do_caminho(cli_com, agendador, config):
    criar(cli_com, config)

    xml = agendador.tarefas["Zelador"]
    assert "minhas regras" in xml  # o espaço do caminho sobreviveu à ida e volta em UTF-16
    assert "Zelador: executa as regras de" in xml


def test_criar_de_novo_atualiza_em_vez_de_duplicar(cli_com, agendador, config, caplog):
    criar(cli_com, config)
    criar(cli_com, config, "--hora", "18:30")

    assert len(agendador.tarefas) == 1
    assert resumir_xml(agendador.tarefas["Zelador"])["gatilhos"] == ["todo dia às 18:30"]
    assert "criada" in caplog.text and "atualizada" in caplog.text


def test_opcoes_de_horario_e_nome(cli_com, agendador, config):
    criar(cli_com, config, "--hora", "7:05", "--nome", "Limpeza do escritório".replace("ó", "o"))

    resumo = resumir_xml(agendador.tarefas["Limpeza do escritorio"])
    assert resumo["gatilhos"] == ["todo dia às 07:05"]


def test_frequencia_semanal_com_dias(cli_com, agendador, config):
    codigo = criar(cli_com, config, "--frequencia", "semanal", "--dias", "qua,seg,sab",
                   "--hora", "08:15")

    assert codigo == 0
    assert resumir_xml(agendador.tarefas["Zelador"])["gatilhos"] == [
        "toda semana (segundas, quartas e sábados), às 08:15"]


def test_frequencia_ao_entrar_no_windows(cli_com, agendador, config):
    assert criar(cli_com, config, "--frequencia", "aoentrar") == 0

    assert resumir_xml(agendador.tarefas["Zelador"])["gatilhos"] == [
        "2 minutos depois que você entrar no Windows"]


def test_simulacao_agenda_so_o_dry_run(cli_com, agendador, config, caplog):
    criar(cli_com, config, "--simulacao")

    comando = resumir_xml(agendador.tarefas["Zelador"])["comando"]
    assert comando.endswith("--dry-run")
    assert "somente simulação" in caplog.text
    assert "somente simulação" in agendador.tarefas["Zelador"]  # também na descrição da tarefa


def test_a_pasta_de_log_informada_vai_junto_para_a_tarefa(cli_com, agendador, config, tmp_path):
    criar(cli_com, config)

    comando = resumir_xml(agendador.tarefas["Zelador"])["comando"]
    assert f'--log-dir "{os.path.abspath(tmp_path / "logs")}"' in comando


def test_sem_config_usa_o_arquivo_padrao_do_zelador(cli_com, agendador, tmp_path, monkeypatch):
    appdata = tmp_path / "AppData"
    (appdata / "Zelador").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    (appdata / "Zelador" / "regras.yml").write_text(
        'regras:\n  - nome: x\n    pasta: "{downloads}"\n    condicoes: {idade_dias: 9}\n'
        "    acao: lixeira\n", encoding="utf-8")

    assert cli_com("agendar", "criar") == 0

    assert str(appdata / "Zelador" / "regras.yml") in agendador.tarefas["Zelador"]


def test_config_relativo_vira_absoluto_no_comando_e_na_descricao(
        cli_com, agendador, config, monkeypatch):
    monkeypatch.chdir(config.parent)  # a tarefa roda de OUTRA pasta: relativo não serviria

    criar(cli_com, Path("regras.yml"))

    xml = agendador.tarefas["Zelador"]
    absoluto = os.path.abspath(config)
    assert f"executa as regras de {absoluto}" in xml
    assert f'--config "{absoluto}"' in xml


def test_mensagem_final_diz_como_testar_ver_e_remover(cli_com, agendador, config, caplog):
    criar(cli_com, config)

    assert "Tarefa 'Zelador' criada: todo dia às 09:00." in caplog.text
    assert "schtasks /Run" in caplog.text
    assert "zelador agendar mostrar" in caplog.text and "zelador agendar remover" in caplog.text


# ---------------------------------------------- recusas de segurança em criar


def test_regra_invalida_impede_o_agendamento(cli_com, agendador, tmp_path, caplog):
    arquivo = escrever_regras(tmp_path, '  - nome: Ruim\n    pasta: "{downloads}"\n'
                                        "    condicoes: {idade_dia: 7}\n    acao: lixeira\n")

    codigo = criar(cli_com, arquivo)

    assert codigo == 1 and agendador.chamadas == []  # nem consultou o Agendador
    assert "Regra com problema" in caplog.text and "zelador listar" in caplog.text


def test_boas_regras_misturadas_com_ruins_tambem_impedem(cli_com, agendador, tmp_path):
    arquivo = escrever_regras(
        tmp_path,
        '  - nome: Boa\n    pasta: "{downloads}"\n    condicoes: {idade_dias: 7}\n'
        "    acao: lixeira\n"
        '  - nome: Ruim\n    pasta: "{downloads}"\n    condicoes: {}\n    acao: lixeira\n')

    assert criar(cli_com, arquivo) == 1 and agendador.chamadas == []


def test_arquivo_sem_regras_nao_e_agendado(cli_com, agendador, tmp_path, caplog):
    arquivo = tmp_path / "vazio.yml"
    arquivo.write_text("regras: []\n", encoding="utf-8")

    assert criar(cli_com, arquivo) == 1 and agendador.chamadas == []
    assert "nenhuma regra" in caplog.text


def test_arquivo_inexistente_ou_yaml_quebrado_saem_com_codigo_2(cli_com, agendador, tmp_path):
    quebrado = tmp_path / "quebrado.yml"
    quebrado.write_text("regras: [aberta\n", encoding="utf-8")

    assert criar(cli_com, tmp_path / "nao_existe.yml") == 2
    assert criar(cli_com, quebrado) == 2
    assert agendador.chamadas == []


def test_regra_que_apaga_de_vez_e_recusada(cli_com, agendador, tmp_path, caplog):
    arquivo = escrever_regras(tmp_path, '  - nome: Apagar tudo\n    pasta: "{downloads}"\n'
                                        "    condicoes: {idade_dias: 7}\n    acao: apagar\n")

    codigo = criar(cli_com, arquivo)

    assert codigo == 1 and agendador.chamadas == []
    assert "DE VEZ" in caplog.text and "Apagar tudo" in caplog.text
    assert "--permitir-apagar-de-vez" in caplog.text and "lixeira" in caplog.text  # ensina a saída


def test_apagar_de_vez_so_com_confirmacao_explicita(cli_com, agendador, tmp_path):
    arquivo = escrever_regras(tmp_path, '  - nome: Apagar\n    pasta: "{downloads}"\n'
                                        "    condicoes: {idade_dias: 7}\n    acao: apagar\n")

    assert criar(cli_com, arquivo, "--permitir-apagar-de-vez") == 0
    assert "Apagar" in agendador.tarefas["Zelador"] or agendador.tarefas


def test_apagar_de_vez_e_aceito_na_simulacao_porque_nada_e_apagado(cli_com, agendador, tmp_path):
    arquivo = escrever_regras(tmp_path, '  - nome: Apagar\n    pasta: "{downloads}"\n'
                                        "    condicoes: {idade_dias: 7}\n    acao: apagar\n")

    assert criar(cli_com, arquivo, "--simulacao") == 0


def test_lixeira_e_mover_nao_precisam_de_confirmacao(cli_com, agendador, config):
    assert criar(cli_com, config) == 0  # o `config` tem lixeira + mover


def test_semanal_sem_dias_e_recusado_com_codigo_2(cli_com, agendador, config, caplog):
    assert criar(cli_com, config, "--frequencia", "semanal") == 2
    assert agendador.chamadas == [] and "--dias" in caplog.text


def test_dias_com_outra_frequencia_e_recusado(cli_com, agendador, config):
    assert criar(cli_com, config, "--dias", "seg") == 2 and agendador.chamadas == []


@pytest.mark.parametrize("argumentos", [
    ["--hora", "24:00"], ["--hora", "9h"], ["--hora", ""],
    ["--nome", "pasta\\tarefa"], ["--nome", ""], ["--nome", "x" * 101],
    ["--frequencia", "mensal"], ["--frequencia", "semanal", "--dias", "xyz"],
])
def test_argumentos_invalidos_saem_com_codigo_2_sem_tocar_no_agendador(
        cli_com, agendador, config, argumentos):
    with pytest.raises(SystemExit) as saida:
        criar(cli_com, config, *argumentos)

    assert saida.value.code == 2 and agendador.chamadas == []


# ------------------------------------------------------------ falhas do sistema


def test_recusa_do_agendador_vira_erro_claro_e_o_temporario_some(
        cli_com, agendador, config, caplog):
    agendador.falhar_em["/Create"] = (1, "ERRO: Acesso negado.")

    codigo = criar(cli_com, config)

    assert codigo == 1
    assert "O Agendador de Tarefas recusou a tarefa: ERRO: Acesso negado." in caplog.text
    assert not Path(agendador.arquivos_xml[0]).exists() if agendador.arquivos_xml else True


def test_schtasks_ausente_ou_travado_vira_erro_e_nao_traceback(cli_com, agendador, config, caplog):
    for erro in (FileNotFoundError("schtasks não encontrado"),
                 subprocess.TimeoutExpired("schtasks", 60)):
        agendador.erro = erro
        caplog.clear()

        assert criar(cli_com, config) == 1
        assert "Não foi possível falar com o Agendador de Tarefas" in caplog.text


def test_fora_do_windows_nenhum_comando_funciona_e_nenhum_schtasks_e_chamado(
        monkeypatch, config, cli_com, caplog):
    falso = AgendadorFalso()
    monkeypatch.setattr(comando_agenda, "_schtasks", falso)
    monkeypatch.setattr(comando_agenda.sys, "platform", "linux")

    assert criar(cli_com, config) == 1
    assert cli_com("agendar", "remover") == 1
    assert cli_com("agendar", "mostrar") == 1

    assert falso.chamadas == [] and "Agendador de Tarefas do Windows" in caplog.text


# ----------------------------------------------------------------- remover


def test_remover_apaga_a_tarefa(cli_com, agendador, config, caplog):
    criar(cli_com, config)

    assert cli_com("agendar", "remover") == 0

    assert agendador.tarefas == {} and agendador.operacoes()[-1] == "/Delete"
    assert agendador.chamadas[-1] == ("/Delete", "/TN", "Zelador", "/F")
    assert "Tarefa 'Zelador' removida." in caplog.text


def test_remover_o_que_nao_existe_nao_e_erro_e_nao_apaga_nada(cli_com, agendador, caplog):
    assert cli_com("agendar", "remover") == 0

    assert agendador.operacoes() == ["/Query"]  # só consultou
    assert "nada a remover" in caplog.text


def test_remover_so_mexe_na_tarefa_pedida(cli_com, agendador, config):
    criar(cli_com, config)
    criar(cli_com, config, "--nome", "Outra")

    cli_com("agendar", "remover", "--nome", "Outra")

    assert list(agendador.tarefas) == ["Zelador"]


def test_remover_com_falha_do_agendador(cli_com, agendador, config, caplog):
    criar(cli_com, config)
    agendador.falhar_em["/Delete"] = (1, "ERRO: Acesso negado.")

    assert cli_com("agendar", "remover") == 1
    assert "não removeu" in caplog.text and "Acesso negado" in caplog.text


# ----------------------------------------------------------------- mostrar


def test_mostrar_resume_a_tarefa(cli_com, agendador, config, capsys):
    criar(cli_com, config, "--frequencia", "semanal", "--dias", "seg,sex", "--hora", "08:00")

    codigo = cli_com("agendar", "mostrar")
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "Tarefa : Zelador (ativada)" in saida
    assert "Quando : toda semana (segundas e sextas), às 08:00" in saida
    assert "-m zelador rodar --config" in saida and "regras.yml" in saida


def test_mostrar_tarefa_inexistente(cli_com, agendador, capsys, caplog):
    assert cli_com("agendar", "mostrar") == 1

    assert capsys.readouterr().out == ""
    assert "zelador agendar criar" in caplog.text  # diz como criar


def test_mostrar_tarefa_desativada(cli_com, agendador, config, capsys):
    criar(cli_com, config)
    agendador.tarefas["Zelador"] = agendador.tarefas["Zelador"].replace(
        "<Enabled>true</Enabled>\n  </Settings>", "<Enabled>false</Enabled>\n  </Settings>")

    cli_com("agendar", "mostrar")

    assert "(DESATIVADA)" in capsys.readouterr().out


def test_mostrar_xml_ilegivel(cli_com, agendador, caplog):
    agendador.tarefas["Zelador"] = "isto não é xml"

    assert cli_com("agendar", "mostrar") == 1
    assert "Não foi possível ler a definição" in caplog.text


# -------------------------------------------------------------- resumir_xml


def _xml(gatilhos: str, extra: str = "") -> str:
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            f"<Triggers>{gatilhos}</Triggers>{extra}"
            "<Actions><Exec><Command>C:\\py.exe</Command><Arguments>-m zelador</Arguments>"
            "</Exec></Actions></Task>")


def test_resumir_xml_gatilhos_conhecidos_e_personalizados():
    gatilhos = (
        "<CalendarTrigger><StartBoundary>2026-11-04T09:30:00</StartBoundary>"
        "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
        "<LogonTrigger><Enabled>true</Enabled></LogonTrigger>"
        "<BootTrigger><Enabled>true</Enabled></BootTrigger>")

    resumo = resumir_xml(_xml(gatilhos))

    assert resumo["gatilhos"] == ["todo dia às 09:30",
                                  "2 minutos depois que você entrar no Windows",
                                  "gatilho personalizado (BootTrigger)"]
    assert resumo["comando"] == "C:\\py.exe -m zelador" and resumo["ativada"] is True


def test_resumir_xml_semanal_editado_a_mao_sem_lista_de_dias_nao_quebra():
    gatilhos = ("<CalendarTrigger><StartBoundary>2026-11-04T09:30:00</StartBoundary>"
                "<ScheduleByWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
                "</CalendarTrigger>")

    resumo = resumir_xml(_xml(gatilhos))

    assert len(resumo["gatilhos"]) == 1 and "09:30" in resumo["gatilhos"][0]


def test_resumir_xml_ignora_dias_que_nao_conhece():
    gatilhos = ("<CalendarTrigger><StartBoundary>2026-11-04T09:30:00</StartBoundary>"
                "<ScheduleByWeek><DaysOfWeek><Monday /><Someday /></DaysOfWeek>"
                "</ScheduleByWeek></CalendarTrigger>")

    assert resumir_xml(_xml(gatilhos))["gatilhos"] == ["toda semana (segundas), às 09:30"]


def test_resumir_xml_sem_gatilhos_nem_acoes():
    resumo = resumir_xml('<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"/>')

    assert resumo["gatilhos"] == [] and resumo["comando"] == "?"


def test_resumir_xml_ignora_o_cabecalho_utf16():
    assert resumir_xml(_xml(""))["gatilhos"] == []  # o parser de texto não aceita a declaração


# ----------------------------------------------------- o `schtasks` de verdade


def test_o_wrapper_chama_o_schtasks_com_os_argumentos_e_um_limite_de_tempo(monkeypatch):
    chamadas = []

    def falso_run(comando, **opcoes):
        chamadas.append((comando, opcoes))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(comando_agenda.subprocess, "run", falso_run)

    comando_agenda._schtasks("/Query", "/TN", "Zelador")

    comando, opcoes = chamadas[0]
    assert comando == ["schtasks", "/Query", "/TN", "Zelador"]
    assert opcoes["timeout"] == 60 and opcoes["capture_output"] and opcoes["errors"] == "replace"


def test_saida_junta_stdout_e_stderr_sem_quebras():
    resultado = SimpleNamespace(stdout="linha 1\nlinha 2", stderr="  ERRO:\n  falhou ")

    assert comando_agenda._saida(resultado) == "linha 1 linha 2 ERRO: falhou"
    assert comando_agenda._saida(SimpleNamespace(stdout=None, stderr=None)) == ""


# ------------------------------------------------------------------- ajuda


def test_a_ajuda_lista_o_comando_agendar_e_suas_acoes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "agendar" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        cli.main(["agendar", "--help"])
    texto = capsys.readouterr().out
    assert all(acao in texto for acao in ("criar", "remover", "mostrar"))


def test_agendar_sem_acao_e_rejeitado(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["agendar"])

    assert saida.value.code == 2


def test_ajuda_do_criar_mostra_as_opcoes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["agendar", "criar", "--help"])
    texto = capsys.readouterr().out

    for opcao in ("--frequencia", "--hora", "--dias", "--config", "--nome", "--simulacao",
                  "--permitir-apagar-de-vez"):
        assert opcao in texto


@pytest.mark.parametrize("acao", ["criar", "remover", "mostrar"])
def test_agendar_nao_aceita_dry_run(acao):
    with pytest.raises(SystemExit) as saida:
        cli.main(["agendar", acao, "--dry-run"])

    assert saida.value.code == 2 and re.fullmatch(r"[a-z]+", acao)
    assert agenda.NOME_PADRAO == "Zelador"
