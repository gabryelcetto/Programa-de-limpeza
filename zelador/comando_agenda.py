"""Comandos `agendar criar|remover|mostrar`: o Zelador roda sozinho pelo Agendador de Tarefas.

A tarefa roda `zelador rodar --config <arquivo>` como o usuário atual, sem administrador.

Códigos de saída: 0 tudo certo; 1 não foi possível (fora do Windows, regras inválidas, o
Agendador recusou...); 2 argumento ou arquivo de regras inválido.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from . import agenda, caminhos
from .configuracao import ErroConfiguracao, carregar_regras
from .logs import logger
from .regras import Apagar

_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
_DIA_PELO_INGLES = {ingles: abreviado for abreviado, ingles in agenda.DIAS_DA_SEMANA.items()}


# --------------------------------------------------------------- argumentos


def _tipo(funcao):
    """Adapta uma validação que levanta ValueError para o argparse (exit 2 com a mensagem)."""
    def converter(texto):
        try:
            return funcao(texto)
        except ValueError as erro:
            raise argparse.ArgumentTypeError(str(erro)) from None
    converter.__name__ = funcao.__name__
    return converter


def registrar(subparsers, comum: argparse.ArgumentParser) -> None:
    agendar = subparsers.add_parser(
        "agendar",
        help="faz o Zelador rodar sozinho, pelo Agendador de Tarefas do Windows",
        description="Cria, remove ou mostra a tarefa agendada que executa `zelador rodar`. "
                    "A tarefa roda como você, sem administrador.",
    )
    acoes = agendar.add_subparsers(dest="acao_agenda", required=True, metavar="AÇÃO")

    criar = acoes.add_parser(
        "criar", parents=[comum],
        help="cria (ou atualiza) a tarefa agendada",
        description="Cria a tarefa agendada. Antes, confere o arquivo de regras: regras "
                    "inválidas impedem o agendamento, e uma regra que apaga DE VEZ só é aceita "
                    "com --permitir-apagar-de-vez (ou em --simulacao).",
    )
    criar.add_argument("--frequencia", choices=agenda.FREQUENCIAS, default="diaria",
                       help="diaria, semanal (com --dias) ou aoentrar (ao entrar no Windows); "
                            "padrão: %(default)s")
    criar.add_argument("--hora", type=_tipo(agenda.hora_valida), default=agenda.HORA_PADRAO,
                       metavar="HH:MM", help="horário (padrão: %(default)s)")
    criar.add_argument("--dias", type=_tipo(agenda.dias_validos), default=None,
                       metavar="seg,qua", help="dias da semana, só com --frequencia semanal")
    criar.add_argument("--config", type=Path, default=None,
                       help="arquivo de regras (padrão: o arquivo padrão do Zelador)")
    criar.add_argument("--nome", type=_tipo(agenda.nome_valido), default=agenda.NOME_PADRAO,
                       help="nome da tarefa (padrão: %(default)s)")
    criar.add_argument("--simulacao", action="store_true",
                       help="agenda só a simulação (--dry-run): a tarefa registra o que faria, "
                            "sem alterar nada")
    criar.add_argument("--permitir-apagar-de-vez", action="store_true",
                       help="aceita agendar regras com `acao: apagar` (sem volta)")
    criar.set_defaults(executar=executar_criar)

    for nome, texto, funcao in (("remover", "remove a tarefa agendada", executar_remover),
                                ("mostrar", "mostra a tarefa agendada", executar_mostrar)):
        parser = acoes.add_parser(nome, parents=[comum], help=texto, description=texto + ".")
        parser.add_argument("--nome", type=_tipo(agenda.nome_valido), default=agenda.NOME_PADRAO,
                            help="nome da tarefa (padrão: %(default)s)")
        parser.set_defaults(executar=funcao)


# ----------------------------------------------------------------- schtasks


def _windows() -> bool:
    if sys.platform != "win32":
        logger.error("O agendamento usa o Agendador de Tarefas do Windows e não está disponível "
                     "neste sistema.")
        return False
    return True


def _schtasks(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *argumentos], capture_output=True, text=True,
                          encoding="oem", errors="replace", timeout=60)


def _saida(resultado: subprocess.CompletedProcess) -> str:
    return " ".join(f"{resultado.stdout or ''} {resultado.stderr or ''}".split())


def _existe(nome: str) -> bool:
    return _schtasks("/Query", "/TN", nome).returncode == 0


# ------------------------------------------------------------------- criar


def _validar_regras(config: Path, simulacao: bool, permitir_apagar: bool) -> Optional[int]:
    """None se o arquivo pode ser agendado; senão, o código de saída (já com a mensagem)."""
    try:
        carga = carregar_regras(config)
    except ErroConfiguracao as erro:
        logger.error("Arquivo de regras: %s", erro)
        return 2
    if carga.erros:
        for erro in carga.erros:
            logger.error("Regra com problema: %s", erro)
        logger.error("Corrija as regras (veja `zelador listar --config \"%s\"`) antes de agendar.",
                     config)
        return 1
    if not carga.regras:
        logger.error("O arquivo %s não tem nenhuma regra para agendar.", config)
        return 1
    de_vez = [r.nome for r in carga.regras if isinstance(r.acao, Apagar)]
    if de_vez and not simulacao and not permitir_apagar:
        logger.error("Estas regras apagam DE VEZ, sem Lixeira, e rodariam sozinhas: %s. Troque "
                     "`acao: apagar` por `acao: lixeira`, use --simulacao, ou confirme com "
                     "--permitir-apagar-de-vez.", ", ".join(de_vez))
        return 1
    return None


def executar_criar(args: argparse.Namespace) -> int:
    if not _windows():
        return 1
    if args.frequencia == "semanal" and not args.dias:
        logger.error("A frequência semanal precisa dos dias: --dias seg,qua,sex")
        return 2
    if args.frequencia != "semanal" and args.dias:
        logger.error("--dias só vale com --frequencia semanal.")
        return 2

    config = Path(os.path.abspath(args.config or caminhos.arquivo_de_regras_padrao()))
    codigo = _validar_regras(config, args.simulacao, args.permitir_apagar_de_vez)
    if codigo is not None:
        return codigo

    try:
        executavel, argumentos = agenda.comando_da_tarefa(config, args.simulacao, args.log_dir)
    except ValueError as erro:
        logger.error("%s", erro)
        return 2
    descricao = f"Zelador: executa as regras de {config}" + (
        " (somente simulação)" if args.simulacao else "")
    xml = agenda.gerar_xml(args.frequencia, args.hora, args.dias or (), executavel, argumentos,
                           descricao)

    arquivo = None
    try:
        existia = _existe(args.nome)
        descritor, arquivo = tempfile.mkstemp(suffix=".xml", prefix="zelador-tarefa-")
        with os.fdopen(descritor, "wb") as f:
            f.write(xml.encode("utf-16"))  # com BOM: é como o schtasks espera ler o XML
        resultado = _schtasks("/Create", "/TN", args.nome, "/XML", arquivo, "/F")
    except (OSError, subprocess.SubprocessError) as erro:
        logger.error("Não foi possível falar com o Agendador de Tarefas: %s", erro)
        return 1
    finally:
        if arquivo:
            Path(arquivo).unlink(missing_ok=True)

    if resultado.returncode != 0:
        logger.error("O Agendador de Tarefas recusou a tarefa: %s", _saida(resultado))
        return 1

    quando = agenda.descrever_quando(args.frequencia, args.hora, args.dias or ())
    logger.info("Tarefa '%s' %s: %s%s.", args.nome, "atualizada" if existia else "criada", quando,
                " (somente simulação)" if args.simulacao else "")
    logger.info("Executa: %s %s", executavel, argumentos)
    logger.info("Para ver: zelador agendar mostrar | testar agora: schtasks /Run /TN \"%s\" | "
                "remover: zelador agendar remover", args.nome)
    return 0


# ----------------------------------------------------------------- remover


def executar_remover(args: argparse.Namespace) -> int:
    if not _windows():
        return 1
    try:
        if not _existe(args.nome):
            logger.info("Não há tarefa agendada chamada '%s': nada a remover.", args.nome)
            return 0
        resultado = _schtasks("/Delete", "/TN", args.nome, "/F")
    except (OSError, subprocess.SubprocessError) as erro:
        logger.error("Não foi possível falar com o Agendador de Tarefas: %s", erro)
        return 1
    if resultado.returncode != 0:
        logger.error("O Agendador de Tarefas não removeu a tarefa: %s", _saida(resultado))
        return 1
    logger.info("Tarefa '%s' removida.", args.nome)
    return 0


# ----------------------------------------------------------------- mostrar


def resumir_xml(texto: str) -> dict:
    """O que importa da definição da tarefa. Levanta ET.ParseError se o XML for inválido."""
    raiz = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", texto))
    gatilhos = []
    bloco = raiz.find(f"{_NS}Triggers")
    # `is not None`, e não `or []`: o valor de verdade de um elemento XML é enganoso e obsoleto
    for gatilho in (bloco if bloco is not None else []):
        tipo = gatilho.tag.replace(_NS, "")
        inicio = gatilho.findtext(f"{_NS}StartBoundary") or ""
        hora = inicio[11:16] if len(inicio) >= 16 else "?"
        if tipo == "CalendarTrigger" and gatilho.find(f"{_NS}ScheduleByDay") is not None:
            gatilhos.append(agenda.descrever_quando("diaria", hora))
        elif tipo == "CalendarTrigger" and gatilho.find(f"{_NS}ScheduleByWeek") is not None:
            dias_xml = gatilho.find(f"{_NS}ScheduleByWeek/{_NS}DaysOfWeek")
            dias = tuple(_DIA_PELO_INGLES[d.tag.replace(_NS, "")]
                         for d in (dias_xml if dias_xml is not None else [])
                         if d.tag.replace(_NS, "") in _DIA_PELO_INGLES)
            gatilhos.append(agenda.descrever_quando("semanal", hora, dias))
        elif tipo == "LogonTrigger":
            gatilhos.append(agenda.descrever_quando("aoentrar", hora))
        else:
            gatilhos.append(f"gatilho personalizado ({tipo})")
    comando = raiz.findtext(f"{_NS}Actions/{_NS}Exec/{_NS}Command") or "?"
    argumentos = raiz.findtext(f"{_NS}Actions/{_NS}Exec/{_NS}Arguments") or ""
    ativada = (raiz.findtext(f"{_NS}Settings/{_NS}Enabled") or "true").lower() != "false"
    return {"gatilhos": gatilhos, "comando": f"{comando} {argumentos}".strip(), "ativada": ativada}


def executar_mostrar(args: argparse.Namespace) -> int:
    if not _windows():
        return 1
    try:
        resultado = _schtasks("/Query", "/TN", args.nome, "/XML")
    except (OSError, subprocess.SubprocessError) as erro:
        logger.error("Não foi possível falar com o Agendador de Tarefas: %s", erro)
        return 1
    if resultado.returncode != 0:
        logger.info("Não há tarefa agendada chamada '%s'. Crie uma com `zelador agendar criar`.",
                    args.nome)
        return 1
    try:
        resumo = resumir_xml(resultado.stdout)
    except ET.ParseError as erro:
        logger.error("Não foi possível ler a definição da tarefa '%s': %s", args.nome, erro)
        return 1

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # consoles que não têm algum caractere
    print(f"Tarefa : {args.nome} ({'ativada' if resumo['ativada'] else 'DESATIVADA'})")
    for gatilho in resumo["gatilhos"] or ["(nenhum gatilho)"]:
        print(f"Quando : {gatilho}")
    print(f"Executa: {resumo['comando']}")
    return 0
