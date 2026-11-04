"""Histórico do que o Zelador fez: um arquivo de linhas JSON em que só se ACRESCENTA ao fim.

Por que esse formato: se o computador desligar no meio de uma gravação, no máximo a última linha
fica incompleta, e o leitor a ignora; nenhuma linha antiga é reescrita, então um defeito ao gravar
nunca corrompe o que já estava lá. "Desfeito" também é uma linha nova, não uma edição.

Três tipos de linha:
  {"tipo": "lote",    "id", "instante", "comando"}                 uma execução (rodar, limpar...)
  {"tipo": "acao",    "id", "lote", "instante", "regra", "acao",   um item tratado
                      "origem", "destino", "tamanho", "eh_pasta"}
  {"tipo": "desfeito", "acao", "instante", "restaurada_em"}        uma ação que foi desfeita

Só o lote que de fato tratou algum item aparece: execuções que não fizeram nada não deixam rastro.
"""

import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .regras import Registro

logger = logging.getLogger("zelador")

DIAS_DE_HISTORICO = 180  # o que for mais velho que isso é descartado ao podar
LIMITE_PARA_PODAR = 2 * 1024 * 1024  # só poda sozinho quando o arquivo passa de 2 MB


@dataclass
class Acao:
    id: str
    lote: str
    instante: datetime
    regra: str
    tipo: str  # "mover", "lixeira", "apagar" ou "outra"
    origem: Path
    destino: Optional[Path]
    tamanho: int
    eh_pasta: bool
    desfeita: bool = False
    restaurada_em: Optional[Path] = None


@dataclass
class Lote:
    id: str
    instante: datetime
    comando: str
    acoes: list = field(default_factory=list)


@dataclass
class Leitura:
    """O que foi lido, mais quantas linhas tiveram que ser ignoradas por estarem ilegíveis."""

    lotes: list
    linhas_ignoradas: int = 0


class Historico:
    def __init__(self, arquivo: Path, agora: Callable[[], datetime] = datetime.now):
        self.arquivo = Path(arquivo)
        self._agora = agora

    # ---------------------------------------------------------- gravação

    def gravador(self, comando: str) -> "Gravador":
        """Um objeto para passar como `registrar` ao motor: grava cada item tratado."""
        return Gravador(self, comando)

    def _anexar(self, dados: dict) -> None:
        linha = json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        with open(self.arquivo, "a", encoding="utf-8", newline="\n") as f:
            f.write(linha)  # uma única escrita: duas execuções ao mesmo tempo não se misturam

    def marcar_desfeito(self, acao: Acao, restaurada_em: Path) -> None:
        """Registra que a ação foi desfeita. Levanta OSError se não conseguir gravar."""
        self._anexar({"tipo": "desfeito", "acao": acao.id,
                      "instante": self._agora().isoformat(timespec="seconds"),
                      "restaurada_em": str(restaurada_em)})

    # ------------------------------------------------------------ leitura

    def ler(self) -> Leitura:
        """Os lotes, do mais antigo para o mais novo. Linhas ilegíveis são ignoradas e contadas.
        Levanta OSError se o arquivo existe mas não pode ser lido."""
        if not self.arquivo.exists():
            return Leitura([])
        lotes, acoes, ignoradas = {}, {}, 0
        with open(self.arquivo, encoding="utf-8-sig", errors="replace") as f:
            for linha in f:
                if not linha.strip():
                    continue
                try:
                    dados = json.loads(linha)
                    ignoradas += 0 if self._aplicar(dados, lotes, acoes) else 1
                except (ValueError, KeyError, TypeError):  # JSON ruim, campo faltando, tipo errado
                    ignoradas += 1
        return Leitura([lote for lote in lotes.values() if lote.acoes], ignoradas)

    @staticmethod
    def _aplicar(dados: dict, lotes: dict, acoes: dict) -> bool:
        """Incorpora uma linha. False se ela não faz sentido (lote desconhecido, tipo novo...)."""
        tipo = dados["tipo"]
        if tipo == "lote":
            lotes[dados["id"]] = Lote(dados["id"], datetime.fromisoformat(dados["instante"]),
                                      dados["comando"])
            return True
        if tipo == "acao":
            lote = lotes.get(dados["lote"])
            if lote is None:
                return False
            acao = Acao(dados["id"], dados["lote"], datetime.fromisoformat(dados["instante"]),
                        dados["regra"], dados["acao"], Path(dados["origem"]),
                        Path(dados["destino"]) if dados["destino"] else None,
                        int(dados["tamanho"]), bool(dados["eh_pasta"]))
            lote.acoes.append(acao)
            acoes[acao.id] = acao
            return True
        if tipo == "desfeito":
            acao = acoes.get(dados["acao"])
            if acao is None:
                return False
            acao.desfeita = True
            acao.restaurada_em = Path(dados["restaurada_em"])
            return True
        return False

    # -------------------------------------------------------------- poda

    def podar(self, dias: int = DIAS_DE_HISTORICO) -> int:
        """Descarta os lotes mais velhos que `dias` (com seus "desfeito"), reescrevendo o arquivo
        de forma atômica. Devolve quantos lotes saíram. Levanta OSError se falhar, deixando o
        arquivo original intacto."""
        corte = self._agora() - timedelta(days=dias)
        leitura = self.ler()
        manter = [lote for lote in leitura.lotes if lote.instante >= corte]
        descartados = len(leitura.lotes) - len(manter)
        if descartados == 0:
            return 0
        temporario = self.arquivo.with_name(self.arquivo.name + ".tmp")
        try:
            temporario.write_bytes("".join(self._linhas_do_lote(lote) for lote in manter)
                                   .encode("utf-8"))
            os.replace(temporario, self.arquivo)
        except OSError:
            temporario.unlink(missing_ok=True)
            raise
        return descartados

    @staticmethod
    def _linhas_do_lote(lote: Lote) -> str:
        def linha(dados):
            return json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + "\n"

        texto = linha({"tipo": "lote", "id": lote.id, "instante": lote.instante.isoformat(
            timespec="seconds"), "comando": lote.comando})
        for a in lote.acoes:
            texto += linha({"tipo": "acao", "id": a.id, "lote": a.lote,
                            "instante": a.instante.isoformat(timespec="seconds"),
                            "regra": a.regra, "acao": a.tipo, "origem": str(a.origem),
                            "destino": str(a.destino) if a.destino else None,
                            "tamanho": a.tamanho, "eh_pasta": a.eh_pasta})
            if a.desfeita:
                texto += linha({"tipo": "desfeito", "acao": a.id,
                                "instante": a.instante.isoformat(timespec="seconds"),
                                "restaurada_em": str(a.restaurada_em)})
        return texto


class Gravador:
    """Passado ao motor como `registrar`. Um problema de disco NÃO interrompe a limpeza: vira um
    aviso (uma vez só) e o que foi feito fica sem registro, sem como ser desfeito."""

    def __init__(self, historico: Historico, comando: str):
        self._historico = historico
        self._comando = comando
        self.lote_id: Optional[str] = None
        self.falhas = 0
        self._sequencia = 0
        self._avisou = False

    def __call__(self, registro: Registro) -> None:
        try:
            if self.lote_id is None:
                self._abrir_lote()
            self._sequencia += 1
            self._historico._anexar({
                "tipo": "acao", "id": f"{self.lote_id}-{self._sequencia}", "lote": self.lote_id,
                "instante": self._historico._agora().isoformat(timespec="seconds"),
                "regra": registro.regra, "acao": registro.tipo, "origem": str(registro.origem),
                "destino": str(registro.destino) if registro.destino else None,
                "tamanho": registro.tamanho, "eh_pasta": registro.eh_pasta})
        except OSError as erro:
            self.falhas += 1
            if not self._avisou:
                self._avisou = True
                logger.warning("Não foi possível gravar no histórico (%s): o que foi feito não "
                               "poderá ser desfeito pelo Zelador.", erro)

    def _abrir_lote(self) -> None:
        agora = self._historico._agora()
        lote_id = f"{agora:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        if (self._historico.arquivo.exists()
                and self._historico.arquivo.stat().st_size > LIMITE_PARA_PODAR):
            try:
                self._historico.podar()
            except OSError as erro:  # poda é conforto: se falhar, segue sem podar
                logger.debug("Não foi possível podar o histórico: %s", erro)
        self._historico._anexar({"tipo": "lote", "id": lote_id,
                                 "instante": agora.isoformat(timespec="seconds"),
                                 "comando": self._comando})
        self.lote_id = lote_id
