"""Painel no navegador: um servidor local que entrega painel.html com os dados reais do PC e
executa as ações que a pessoa confirma na tela.

Segurança (o servidor mexe em arquivos, então nada aqui confia em quem chama):
  - escuta só em 127.0.0.1, numa porta escolhida pelo sistema;
  - recusa pedidos cujo cabeçalho Host não seja 127.0.0.1/localhost nessa porta (protege contra
    "DNS rebinding", em que um site externo se passa por localhost);
  - toda ação exige um token aleatório que só a página entregue por este servidor conhece, num
    cabeçalho próprio: outro site aberto no navegador não consegue enviá-lo;
  - caminhos nunca vêm da página: o destino dos PDFs e as pastas são decididos aqui;
  - uma ação por vez.
"""

import json
import logging
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
import xml.etree.ElementTree as ET
from argparse import Namespace
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Optional

from .. import __version__, agenda, comando_agenda
from .. import discos as _discos
from ..arquivos import formatar_tamanho
from ..configuracao import ErroConfiguracao, carregar_regras
from ..desfazer import Estado, achar_lote, desfazer_lote, lote_desfazivel
from ..historico import Historico
from ..regras import (
    EnviarParaLixeira,
    ExtensaoEm,
    IdadeMinima,
    Item,
    Mover,
    Regra,
    Resumo,
    executar_regra,
)
from . import recomendacoes as _rec

logger = logging.getLogger("zelador")

LIMITE_DO_PEDIDO = 64 * 1024  # bytes: as ações levam só alguns parâmetros
CABECALHO_TOKEN = "X-Zelador-Token"
_DIAS_EM_ORDEM = tuple(agenda.DIAS_DA_SEMANA)  # seg..dom, na ordem de datetime.weekday()


@dataclass
class Configuracao:
    regras: Path  # arquivo regras.yml
    historico: Path  # arquivo historico.jsonl
    downloads: Path
    pdfs: Path  # para onde "organizar PDFs" move


# ================================================================== dados para a tela


def _item_para_tela(item: Item, regra: str, acao: str, agora: datetime) -> dict:
    idade = max(0, int((agora.timestamp() - item.ultima_atividade) // 86400))
    return {"nome": item.nome + ("\\" if item.eh_pasta else ""), "regra": regra,
            "tamanho": item.tamanho, "idade_dias": idade, "acao": acao}


def _simular(regra: Regra, rotulo: str, agora: datetime) -> tuple:
    """(Resumo, itens) da simulação. Levanta OSError/ValueError como o motor."""
    tipo = getattr(regra.acao, "tipo", "outra")
    itens = []
    resumo = executar_regra(regra, dry_run=True, agora=agora, detalhar=False,
                            observar=lambda i: itens.append(_item_para_tela(i, rotulo, tipo, agora)))
    return resumo, itens


def _destino_para_tela(acao) -> str:
    tipo = getattr(acao, "tipo", "")
    if tipo == "mover":
        return str(acao.destino)
    if tipo == "lixeira":
        return "Lixeira do Windows"
    if tipo == "apagar":
        return "apagado de vez, sem Lixeira"
    return acao.resumo()


def regra_limpar_downloads(cfg: Configuracao, dias: int) -> Regra:
    return Regra("Downloads parados", cfg.downloads, [IdadeMinima(dias)], EnviarParaLixeira())


def regra_organizar_pdfs(cfg: Configuracao) -> Regra:
    return Regra("Organizar PDFs", cfg.downloads,
                 [ExtensaoEm(("pdf",)), IdadeMinima(_rec.PDFS_DIAS)], Mover(cfg.pdfs))


def _regras_para_tela(cfg: Configuracao, agora: datetime) -> tuple:
    """(regras, itens simulados de todas, avisos). Sem arquivo de regras, tudo vazio."""
    try:
        carga = carregar_regras(cfg.regras)
    except ErroConfiguracao as erro:
        return [], [], ([] if not cfg.regras.exists() else [str(erro)])
    regras, itens = [], []
    for numero, regra in enumerate(carga.regras, start=1):
        try:
            resumo, lista = _simular(regra, f"{numero} · {regra.nome}", agora)
        except (OSError, ValueError) as erro:
            logger.warning("Regra '%s': não foi possível simular: %s", regra.nome, erro)
            resumo, lista = Resumo(), []
        regras.append({
            "nome": regra.nome, "pasta": str(regra.pasta),
            "condicoes": [c.resumo() for c in regra.condicoes],
            "acao": getattr(regra.acao, "tipo", "outra"),
            "destino": _destino_para_tela(regra.acao),
            "simulacao": {"itens": resumo.aplicados, "bytes": resumo.bytes},
        })
        itens.extend(lista)
    return regras, itens, [str(e) for e in carga.erros]


def _simulacao_ou_vazia(montar, rotulo: str, agora: datetime) -> list:
    try:
        return _simular(montar(), rotulo, agora)[1]
    except (OSError, ValueError):  # Downloads não existe, é protegida...
        return []


def _historico_para_tela(cfg: Configuracao) -> list:
    """Do mais novo para o mais antigo."""
    try:
        leitura = Historico(cfg.historico).ler()
    except OSError as erro:
        logger.warning("Não foi possível ler o histórico: %s", erro)
        return []
    lista = []
    for lote in reversed(leitura.lotes):
        conta = Counter(a.tipo for a in lote.acoes)
        lista.append({
            "id": lote.id, "instante": lote.instante.isoformat(), "comando": lote.comando,
            "origem": lote.comando, "movidos": conta["mover"], "lixeira": conta["lixeira"],
            "apagados": conta["apagar"], "bytes": sum(a.tamanho for a in lote.acoes),
            "desfeito": bool(conta["mover"]) and not lote_desfazivel(lote),
        })
    return lista


def ler_tarefa_xml(texto: str) -> dict:
    """{frequencia, hora, dias, simulacao, ativada} da definição da tarefa (o primeiro gatilho).
    Levanta ET.ParseError se o XML for inválido."""
    ns = comando_agenda._NS
    raiz = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", texto))
    resultado = {"frequencia": "diaria", "hora": agenda.HORA_PADRAO, "dias": []}
    gatilhos = raiz.find(f"{ns}Triggers")
    gatilho = next(iter(gatilhos), None) if gatilhos is not None else None
    if gatilho is not None:
        inicio = gatilho.findtext(f"{ns}StartBoundary") or ""
        if len(inicio) >= 16:
            resultado["hora"] = inicio[11:16]
        if gatilho.tag == f"{ns}LogonTrigger":
            resultado["frequencia"] = "aoentrar"
        elif gatilho.find(f"{ns}ScheduleByWeek") is not None:
            resultado["frequencia"] = "semanal"
            dias = gatilho.find(f"{ns}ScheduleByWeek/{ns}DaysOfWeek")
            nomes = {d.tag.replace(ns, "") for d in (dias if dias is not None else [])}
            resultado["dias"] = [abrev for abrev, ingles in agenda.DIAS_DA_SEMANA.items()
                                 if ingles in nomes]
    argumentos = raiz.findtext(f"{ns}Actions/{ns}Exec/{ns}Arguments") or ""
    resultado["simulacao"] = "--dry-run" in argumentos.split()
    resultado["ativada"] = (raiz.findtext(f"{ns}Settings/{ns}Enabled") or "true").lower() != "false"
    return resultado


def proxima_execucao(frequencia: str, hora: str, dias, agora: datetime) -> Optional[str]:
    if frequencia == "aoentrar":
        return None
    h, m = (int(x) for x in hora.split(":"))
    for adiante in range(8):
        momento = (agora + timedelta(days=adiante)).replace(hour=h, minute=m, second=0,
                                                             microsecond=0)
        if momento <= agora:
            continue
        if frequencia == "diaria" or _DIAS_EM_ORDEM[momento.weekday()] in dias:
            return momento.isoformat()
    return None


def _agenda_para_tela(agora: datetime) -> dict:
    vazia = {"existe": False, "frequencia": "diaria", "hora": agenda.HORA_PADRAO, "dias": [],
             "simulacao": False}
    if sys.platform != "win32":
        return vazia
    try:
        resultado = comando_agenda._schtasks("/Query", "/TN", agenda.NOME_PADRAO, "/XML")
    except (OSError, subprocess.SubprocessError):
        return vazia
    if resultado.returncode != 0:
        return vazia
    try:
        tarefa = ler_tarefa_xml(resultado.stdout)
    except (ET.ParseError, ValueError):
        return {**vazia, "existe": True}
    tarefa["proxima"] = proxima_execucao(tarefa["frequencia"], tarefa["hora"], tarefa["dias"],
                                         agora)
    return {**tarefa, "existe": True}


def coletar_dados(cfg: Configuracao, agora: Optional[datetime] = None,
                  ler_agenda=_agenda_para_tela) -> dict:
    """Tudo que o painel mostra, no formato descrito no topo de painel.html."""
    agora = agora or datetime.now()
    try:
        discos = _discos.coletar_discos()
    except OSError as erro:
        logger.warning("Não foi possível ler os discos: %s", erro)
        discos = ()
    contexto = _rec.Contexto(discos=discos,
                             pasta_downloads=cfg.downloads if cfg.downloads.is_dir() else None,
                             pasta_pdfs=cfg.pdfs)
    recomendacoes = _rec.para_dicts(_rec.gerar(contexto))

    regras, itens_das_regras, avisos = _regras_para_tela(cfg, agora)
    if avisos:  # regra com problema: a pessoa precisa saber, e a tela já tem onde mostrar
        recomendacoes.insert(0, {
            "id": "regras-com-problema", "titulo": "Há regras com problema no regras.yml",
            "gravidade": _rec.IMPORTANTE,
            "explicacao": "Essas regras são ignoradas até serem corrigidas. As outras funcionam.",
            "evidencias": avisos, "acao": None})

    historico = _historico_para_tela(cfg)
    return {
        "versao": __version__,
        "ultima_execucao": ({"instante": historico[0]["instante"],
                             "origem": historico[0]["comando"]} if historico else None),
        "discos": [asdict(d) for d in discos],
        "recomendacoes": recomendacoes,
        "regras": regras,
        "simulacoes": {
            "regras": itens_das_regras,
            _rec.LIMPAR_DOWNLOADS: _simulacao_ou_vazia(
                lambda: regra_limpar_downloads(cfg, _rec.DOWNLOADS_DIAS), "Downloads parados",
                agora),
            _rec.ORGANIZAR_PDFS: _simulacao_ou_vazia(
                lambda: regra_organizar_pdfs(cfg), "Organizar PDFs", agora),
        },
        "historico": historico,
        "agenda": ler_agenda(agora),
    }


# ======================================================================== ações


class _Captura(logging.Handler):
    """Guarda os avisos e erros registrados durante uma ação, para mostrá-los na tela."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.mensagens = []

    def emit(self, registro):
        self.mensagens.append(registro.getMessage())

    def __enter__(self):
        logger.addHandler(self)
        return self

    def __exit__(self, *_):
        logger.removeHandler(self)


def _inteiro_positivo(valor, campo: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, str)):
        raise ValueError(f"{campo} inválido")
    numero = int(valor)
    if numero < 1:
        raise ValueError(f"{campo} precisa ser de pelo menos 1")
    return numero


def _texto_resumo(resumo: Resumo, verbo: str) -> str:
    texto = (f"{resumo.aplicados} {'item' if resumo.aplicados == 1 else 'itens'} {verbo} "
             f"({formatar_tamanho(resumo.bytes)}).")
    if resumo.erros:
        texto += f" {resumo.erros} com erro (veja abaixo)."
    return texto


class Painel:
    def __init__(self, cfg: Configuracao, ler_agenda=_agenda_para_tela):
        self.cfg = cfg
        self._ler_agenda = ler_agenda
        self._trava = threading.Lock()

    def dados(self) -> dict:
        return coletar_dados(self.cfg, ler_agenda=self._ler_agenda)

    def executar(self, acao: str, parametros: dict) -> dict:
        funcao = self._ACOES.get(acao)
        if funcao is None:
            return {"ok": False, "mensagem": f"ação desconhecida: {acao}", "problemas": []}
        with self._trava:
            with _Captura() as captura:
                try:
                    ok, mensagem = funcao(self, parametros)
                except (ValueError, OSError) as erro:
                    ok, mensagem = False, str(erro)
            logger.info("Painel: %s -> %s", acao, mensagem)
            return {"ok": ok, "mensagem": mensagem, "problemas": captura.mensagens,
                    "dados": self.dados()}

    # ------------------------------------------------------------ cada ação

    def _gravador(self, comando: str):
        return Historico(self.cfg.historico).gravador(comando)

    def _limpar_downloads(self, p: dict) -> tuple:
        dias = _inteiro_positivo(p.get("dias", _rec.DOWNLOADS_DIAS), "dias")
        resumo = executar_regra(regra_limpar_downloads(self.cfg, dias),
                                registrar=self._gravador("limpar"))
        return not resumo.erros, _texto_resumo(resumo, "enviados para a Lixeira")

    def _organizar_pdfs(self, p: dict) -> tuple:
        # o destino da página é ignorado de propósito: quem decide o caminho é o servidor
        resumo = executar_regra(regra_organizar_pdfs(self.cfg),
                                registrar=self._gravador("organizar pdfs"))
        return not resumo.erros, _texto_resumo(resumo, f"movidos para {self.cfg.pdfs}")

    def _rodar_regras(self, p: dict) -> tuple:
        try:
            carga = carregar_regras(self.cfg.regras)
        except ErroConfiguracao as erro:
            return False, f"Arquivo de regras: {erro}"
        if not carga.regras:
            return False, "Não há nenhuma regra válida para rodar."
        gravador = self._gravador("rodar")
        total, falhou = Resumo(), bool(carga.erros)
        for regra in carga.regras:
            try:
                resumo = executar_regra(regra, registrar=gravador)
            except OSError as erro:
                logger.error("Regra '%s': não foi possível ler a pasta %s: %s",
                             regra.nome, regra.pasta, erro)
                falhou = True
                continue
            total.aplicados += resumo.aplicados
            total.erros += resumo.erros
            total.bytes += resumo.bytes
        return not (falhou or total.erros), _texto_resumo(total, "tratados pelas regras")

    def _desfazer(self, p: dict) -> tuple:
        lote_id = p.get("lote")
        if not isinstance(lote_id, str):
            raise ValueError("informe o lote a desfazer")
        historico = Historico(self.cfg.historico)
        lote = achar_lote(historico.ler(), lote_id)
        if lote is None:
            return False, f"A execução {lote_id} não está no histórico."
        resultados = desfazer_lote(historico, lote)
        voltaram = sum(r.estado is Estado.DESFEITO for r in resultados)
        problemas = [r for r in resultados if r.estado in (Estado.ERRO, Estado.RECUSADO,
                                                            Estado.NAO_ENCONTRADO)]
        for r in problemas:
            logger.warning("%s: %s (%s)", r.acao.origem.name, r.estado.value, r.detalhe)
        return not problemas, f"{voltaram} {'item voltou' if voltaram == 1 else 'itens voltaram'} " \
                              "para o lugar de origem."

    def _agendar(self, p: dict) -> tuple:
        frequencia = p.get("frequencia")
        if frequencia not in agenda.FREQUENCIAS:
            raise ValueError("frequência inválida")
        dias = None
        if frequencia == "semanal":
            escolhidos = p.get("dias") or []
            if not isinstance(escolhidos, list) or not all(isinstance(d, str) for d in escolhidos):
                raise ValueError("dias inválidos")
            dias = agenda.dias_validos(",".join(escolhidos) or "seg")
        args = Namespace(frequencia=frequencia, hora=agenda.hora_valida(str(p.get("hora", ""))),
                         dias=dias, config=self.cfg.regras, nome=agenda.NOME_PADRAO,
                         simulacao=bool(p.get("simulacao")), permitir_apagar_de_vez=False,
                         log_dir=None)
        codigo = comando_agenda.executar_criar(args)
        return codigo == 0, ("Tarefa agendada: " + agenda.descrever_quando(
            frequencia, args.hora, dias or ()) + ".") if codigo == 0 else "Não foi possível agendar."

    def _remover_agenda(self, p: dict) -> tuple:
        codigo = comando_agenda.executar_remover(Namespace(nome=agenda.NOME_PADRAO))
        return codigo == 0, "Tarefa removida." if codigo == 0 else "Não foi possível remover."

    _ACOES = {
        _rec.LIMPAR_DOWNLOADS: _limpar_downloads,
        _rec.ORGANIZAR_PDFS: _organizar_pdfs,
        "rodar_regras": _rodar_regras,
        "desfazer": _desfazer,
        "agendar": _agendar,
        "remover_agenda": _remover_agenda,
    }


# ===================================================================== página


def _json_para_script(valor) -> str:
    """JSON que pode ir dentro de <script>: nomes de arquivo vêm do disco e podem conter
    '</script>'; escapar '<', '>' e '&' impede que fechem a tag."""
    texto = json.dumps(valor, ensure_ascii=False)
    return texto.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


_PONTE = """<script>
window.ZELADOR_DADOS = %(dados)s;
window.zelador = {
  async executar(acao, parametros) {
    const r = await fetch("/api/acao", {
      method: "POST",
      headers: { "Content-Type": "application/json", "%(cabecalho)s": %(token)s },
      body: JSON.stringify({ acao, parametros }),
    });
    const corpo = await r.json().catch(() => ({ ok: false, mensagem: "resposta inválida (" + r.status + ")" }));
    if (corpo.dados && window.zeladorAtualizar) window.zeladorAtualizar(corpo.dados);
    if (!corpo.ok) throw new Error([corpo.mensagem, ...(corpo.problemas || [])].filter(Boolean).join(" · "));
    return corpo;
  },
};
</script>
"""


def montar_pagina(dados: dict, token: str) -> str:
    html = resources.files(__package__).joinpath("painel.html").read_text(encoding="utf-8")
    ponte = _PONTE % {"dados": _json_para_script(dados), "cabecalho": CABECALHO_TOKEN,
                      "token": _json_para_script(token)}
    if "</head>" not in html:
        raise RuntimeError("painel.html sem </head>: não há onde pôr os dados")
    return html.replace("</head>", ponte + "</head>", 1)


# ==================================================================== servidor


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, painel: Painel, porta: int = 0):
        super().__init__(("127.0.0.1", porta), _Manipulador)
        self.painel = painel
        self.token = secrets.token_urlsafe(32)
        porta_real = self.server_address[1]
        self.hosts = {f"127.0.0.1:{porta_real}", f"localhost:{porta_real}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


class _Manipulador(BaseHTTPRequestHandler):
    server: _Servidor

    def log_message(self, formato, *args):  # o padrão escreve tudo no stderr
        logger.debug("painel: " + formato, *args)

    def _responder(self, codigo: int, corpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, codigo: int, valor) -> None:
        self._responder(codigo, json.dumps(valor, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def _host_valido(self) -> bool:
        return self.headers.get("Host", "") in self.server.hosts

    def do_GET(self):
        if not self._host_valido():
            return self._json(403, {"ok": False, "mensagem": "endereço não permitido"})
        if self.path.split("?")[0] not in ("/", "/index.html"):
            return self._json(404, {"ok": False, "mensagem": "não encontrado"})
        html = montar_pagina(self.server.painel.dados(), self.server.token)
        self._responder(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_valido():
            return self._json(403, {"ok": False, "mensagem": "endereço não permitido"})
        if self.path != "/api/acao":
            return self._json(404, {"ok": False, "mensagem": "não encontrado"})
        if not secrets.compare_digest(self.headers.get(CABECALHO_TOKEN, ""), self.server.token):
            return self._json(403, {"ok": False, "mensagem": "token inválido: recarregue a página"})
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if not 0 < tamanho <= LIMITE_DO_PEDIDO:
            return self._json(413, {"ok": False, "mensagem": "pedido grande demais ou vazio"})
        try:
            pedido = json.loads(self.rfile.read(tamanho))
            acao = pedido["acao"]
            parametros = pedido.get("parametros") or {}
            if not isinstance(acao, str) or not isinstance(parametros, dict):
                raise TypeError
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._json(400, {"ok": False, "mensagem": "pedido inválido"})
        self._json(200, self.server.painel.executar(acao, parametros))


def criar_servidor(cfg: Configuracao, porta: int = 0, **opcoes) -> _Servidor:
    return _Servidor(Painel(cfg, **opcoes), porta)


def servir(cfg: Configuracao, porta: int = 0, abrir_navegador: bool = True) -> int:
    try:
        servidor = criar_servidor(cfg, porta)
    except OSError as erro:
        logger.error("Não foi possível abrir o painel na porta %d: %s", porta, erro)
        return 1
    logger.info("Painel aberto em %s  (deixe esta janela aberta; Ctrl+C para fechar)",
                servidor.url)
    if abrir_navegador:
        webbrowser.open(servidor.url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        logger.info("Painel fechado.")
    finally:
        servidor.server_close()
    return 0
