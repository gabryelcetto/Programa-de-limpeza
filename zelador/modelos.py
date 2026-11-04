"""Modelos prontos de regras: pontos de partida para o arquivo de regras (YAML).

Cada modelo é UMA regra escrita à mão, com comentários, para o usuário entender e editar depois.
`gerar_yaml` junta os modelos escolhidos num arquivo completo, na ordem certa: primeiro as regras
que ORGANIZAM (movem), por último as que LIMPAM, para a limpeza genérica nunca pegar o que acabou
de ser organizado.

Para somar um modelo novo basta acrescentar um `Modelo` a MODELOS. Dois princípios, garantidos
por testes: nenhum modelo apaga de vez (só `mover` ou `lixeira`, que têm volta) e todos atuam na
pasta Downloads.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

CABECALHO = """\
# Regras do Zelador, geradas por `zelador iniciar`. Edite à vontade.
#
# Confira o que cada regra faz:   zelador listar
# Simule, sem alterar nada:       zelador rodar --dry-run
#
# As regras rodam em ORDEM: as que organizam (movem) vêm primeiro e as que limpam por último,
# para a limpeza nunca pegar o que acabou de ser organizado.
#
# Caminhos: {downloads}, {documentos} e {imagens} são as pastas REAIS do usuário (mesmo no
# OneDrive). Ações: `lixeira` (dá para restaurar), `mover` e, só se você quiser apagar DE VEZ,
# `apagar`.

versao: 1

regras:
"""


@dataclass(frozen=True)
class Modelo:
    id: str
    titulo: str
    descricao: str
    ordem: int  # menor roda antes
    padrao: bool  # entra no arquivo quando o usuário não escolhe nenhum modelo
    regra: str  # o YAML de uma regra, já como item da lista `regras:`


MODELOS = (
    Modelo(
        id="organizar-pdfs",
        titulo="Organizar PDFs",
        descricao="move os PDFs de Downloads para a pasta Documentos/PDFs",
        ordem=10,
        padrao=True,
        regra="""\
  # PDFs saem de Downloads para uma pasta própria. Nada é sobrescrito: se já existir um arquivo
  # com o mesmo nome, o novo vira "nome (1).pdf".
  - nome: Organizar PDFs
    pasta: "{downloads}"
    condicoes:
      extensoes: [pdf]
      idade_dias: 1
    acao:
      mover: "{documentos}/PDFs"
""",
    ),
    Modelo(
        id="organizar-imagens",
        titulo="Organizar imagens",
        descricao="move fotos e imagens de Downloads para Imagens/Downloads",
        ordem=11,
        padrao=False,
        regra="""\
  # Fotos e imagens baixadas vão para a pasta Imagens.
  - nome: Organizar imagens
    pasta: "{downloads}"
    condicoes:
      extensoes: [jpg, jpeg, png, gif, webp, bmp, heic]
      idade_dias: 1
    acao:
      mover: "{imagens}/Downloads"
""",
    ),
    Modelo(
        id="organizar-documentos",
        titulo="Organizar documentos do Office",
        descricao="move Word, Excel e PowerPoint de Downloads para Documentos/Baixados",
        ordem=12,
        padrao=False,
        regra="""\
  # Documentos do Office (e equivalentes do LibreOffice) vão para uma pasta própria.
  - nome: Organizar documentos do Office
    pasta: "{downloads}"
    condicoes:
      extensoes: [doc, docx, xls, xlsx, ppt, pptx, odt, ods, odp]
      idade_dias: 1
    acao:
      mover: "{documentos}/Baixados"
""",
    ),
    Modelo(
        id="limpar-instaladores",
        titulo="Limpar instaladores antigos",
        descricao="envia para a Lixeira instaladores (.exe/.msi) grandes com mais de 3 dias",
        ordem=50,
        padrao=True,
        regra="""\
  # Instaladores grandes, que ninguém mais vai usar depois de instalar. Vão para a Lixeira (dá
  # para restaurar). Atenção: a Lixeira só libera espaço quando é esvaziada.
  - nome: Limpar instaladores antigos
    pasta: "{downloads}"
    condicoes:
      extensoes: [exe, msi]
      tamanho_min: 20MB
      idade_dias: 3
    acao: lixeira
""",
    ),
    Modelo(
        id="limpar-downloads-parados",
        titulo="Limpar downloads parados",
        descricao="envia para a Lixeira o que está parado em Downloads há mais de 30 dias",
        ordem=90,
        padrao=True,
        regra="""\
  # Rede de segurança, por último: o que sobrou e está parado há mais de 30 dias.
  - nome: Limpar downloads parados
    pasta: "{downloads}"
    condicoes:
      idade_dias: 30
    acao: lixeira
""",
    ),
)


def ids() -> tuple:
    return tuple(m.id for m in MODELOS)


def gerar_yaml(escolhidos: Optional[Sequence] = None) -> str:
    """O arquivo de regras completo. Sem `escolhidos`, usa os modelos marcados como padrão.
    Levanta ValueError para um id que não existe (a mensagem lista os válidos)."""
    por_id = {m.id: m for m in MODELOS}
    if escolhidos is None:
        selecionados = [m for m in MODELOS if m.padrao]
    else:
        desconhecidos = [e for e in escolhidos if e not in por_id]
        if desconhecidos:
            raise ValueError(f"modelo desconhecido: {', '.join(desconhecidos)} "
                             f"(modelos: {', '.join(ids())})")
        selecionados = [por_id[e] for e in dict.fromkeys(escolhidos)]  # sem repetir, em ordem
    if not selecionados:
        raise ValueError("escolha ao menos um modelo")
    return CABECALHO + "\n".join(m.regra for m in sorted(selecionados, key=lambda m: m.ordem))
