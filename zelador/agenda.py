"""Agendamento no Windows: monta a definição da tarefa do Agendador de Tarefas (XML).

Aqui só se monta texto; quem fala com o `schtasks` é `comando_agenda`. Usa-se o XML, e não as
opções de linha de comando do `schtasks`, por dois motivos: `/TR` aceita no máximo 261
caracteres (um caminho longo em AppData estoura) e só o XML permite configurar "rodar sem
administrador" e "recuperar a execução perdida se o PC estava desligado".
"""

import getpass
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

NOME_PADRAO = "Zelador"
HORA_PADRAO = "09:00"
FREQUENCIAS = ("diaria", "semanal", "aoentrar")
ESPERA_APOS_ENTRAR = "PT2M"  # ao entrar no Windows, espera 2 minutos: o PC está ocupado iniciando
LIMITE_DE_TEMPO = "PT1H"  # uma execução presa por mais de 1 hora é encerrada

DIAS_DA_SEMANA = {
    "seg": "Monday", "ter": "Tuesday", "qua": "Wednesday", "qui": "Thursday",
    "sex": "Friday", "sab": "Saturday", "dom": "Sunday",
}
_ROTULO_DIAS = {"seg": "segundas", "ter": "terças", "qua": "quartas", "qui": "quintas",
                "sex": "sextas", "sab": "sábados", "dom": "domingos"}

# sem barra: o Agendador trata "\" como separador de pastas de tarefas
_NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}$")
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# -------------------------------------------------------------- validações


def hora_valida(texto: str) -> str:
    """'9:05' ou '09:05' -> '09:05'. Levanta ValueError se não for uma hora de 00:00 a 23:59."""
    correspondencia = _HORA.match(texto.strip())
    if not correspondencia:
        raise ValueError(f"'{texto}' não é uma hora válida (use HH:MM, de 00:00 a 23:59)")
    return f"{int(correspondencia.group(1)):02d}:{correspondencia.group(2)}"


def nome_valido(texto: str) -> str:
    if not _NOME_VALIDO.match(texto):
        raise ValueError(
            f"'{texto}' não é um nome válido para a tarefa: use de 1 a 100 letras, números, "
            "espaços, pontos, hífens ou sublinhados, começando por letra ou número (sem barras)")
    return texto


def dias_validos(texto: str) -> tuple:
    """'seg,qua' -> ('seg', 'qua'), sem repetir e na ordem da semana."""
    pedidos = [d.strip().lower() for d in texto.split(",") if d.strip()]
    if not pedidos:
        raise ValueError("informe ao menos um dia, como seg,qua,sex")
    desconhecidos = [d for d in pedidos if d not in DIAS_DA_SEMANA]
    if desconhecidos:
        raise ValueError(f"dia desconhecido: {', '.join(desconhecidos)} "
                         f"(use: {', '.join(DIAS_DA_SEMANA)})")
    return tuple(d for d in DIAS_DA_SEMANA if d in pedidos)


# --------------------------------------------------------------- comando


def _citar(caminho) -> str:
    texto = str(caminho)
    if '"' in texto:
        raise ValueError(f"o caminho não pode conter aspas: {texto}")
    return f'"{texto}"'


def comando_da_tarefa(config: Path, simulacao: bool = False,
                      pasta_log: Optional[Path] = None) -> tuple:
    """(executável, argumentos) que a tarefa vai rodar. Os caminhos são absolutos: a tarefa roda
    de outra pasta de trabalho. Usa `pythonw.exe` (sem janela de console) quando existe."""
    if getattr(sys, "frozen", False):  # programa empacotado (.exe): ele mesmo é o comando
        executavel, argumentos = sys.executable, []
    else:
        python = Path(sys.executable)
        sem_console = python.with_name("pythonw.exe")
        executavel = str(sem_console if sem_console.exists() else python)
        argumentos = ["-m", "zelador"]
    argumentos += ["rodar", "--config", _citar(os.path.abspath(config))]
    if pasta_log is not None:
        argumentos += ["--log-dir", _citar(os.path.abspath(pasta_log))]
    if simulacao:
        argumentos.append("--dry-run")
    return executavel, " ".join(argumentos)


# ------------------------------------------------------------------- XML


def _usuario_atual() -> str:
    dominio = os.environ.get("USERDOMAIN", "")
    usuario = getpass.getuser()
    return f"{dominio}\\{usuario}" if dominio else usuario


def _juntar(itens: Sequence) -> str:
    """['a'] -> 'a'; ['a', 'b'] -> 'a e b'; ['a', 'b', 'c'] -> 'a, b e c'."""
    itens = list(itens)
    if len(itens) <= 1:
        return "".join(itens)
    return f"{', '.join(itens[:-1])} e {itens[-1]}"


def descrever_quando(frequencia: str, hora: str, dias: Sequence = ()) -> str:
    """O gatilho em português, para mostrar ao usuário."""
    if frequencia == "diaria":
        return f"todo dia às {hora}"
    if frequencia == "semanal":
        return f"toda semana ({_juntar([_ROTULO_DIAS[d] for d in dias])}), às {hora}"
    minutos = ESPERA_APOS_ENTRAR[2:-1]  # "PT2M" -> "2"
    return f"{minutos} minutos depois que você entrar no Windows"


def _gatilho(frequencia: str, hora: str, dias: Sequence, agora: datetime) -> str:
    inicio = f"{agora:%Y-%m-%d}T{hora}:00"
    if frequencia == "diaria":
        return (f"    <CalendarTrigger>\n      <StartBoundary>{inicio}</StartBoundary>\n"
                "      <Enabled>true</Enabled>\n"
                "      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                "    </CalendarTrigger>\n")
    if frequencia == "semanal":
        dias_xml = "".join(f"<{DIAS_DA_SEMANA[d]} />" for d in dias)
        return (f"    <CalendarTrigger>\n      <StartBoundary>{inicio}</StartBoundary>\n"
                "      <Enabled>true</Enabled>\n"
                f"      <ScheduleByWeek><DaysOfWeek>{dias_xml}</DaysOfWeek>"
                "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>\n"
                "    </CalendarTrigger>\n")
    return ("    <LogonTrigger>\n      <Enabled>true</Enabled>\n"
            f"      <UserId>{escape(_usuario_atual())}</UserId>\n"
            f"      <Delay>{ESPERA_APOS_ENTRAR}</Delay>\n    </LogonTrigger>\n")


def gerar_xml(frequencia: str, hora: str, dias: Sequence, executavel: str, argumentos: str,
              descricao: str, agora: Optional[datetime] = None) -> str:
    """O XML da tarefa. Roda como o usuário atual, SEM administrador, e só com ele logado."""
    if frequencia not in FREQUENCIAS:
        raise ValueError(f"frequência desconhecida: {frequencia!r} (use: {', '.join(FREQUENCIAS)})")
    if frequencia == "semanal" and not dias:
        raise ValueError("a frequência semanal precisa de ao menos um dia")
    agora = agora or datetime.now()
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{escape(descricao)}</Description>\n"
        "    <Author>Zelador</Author>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        f"{_gatilho(frequencia, hora, dias, agora)}"
        "  </Triggers>\n"
        '  <Principals>\n    <Principal id="Author">\n'
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        f"    <ExecutionTimeLimit>{LIMITE_DE_TEMPO}</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n    <Exec>\n'
        f"      <Command>{escape(executavel)}</Command>\n"
        f"      <Arguments>{escape(argumentos)}</Arguments>\n"
        "    </Exec>\n  </Actions>\n"
        "</Task>\n"
    )
