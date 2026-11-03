"""Pastas em que nenhuma regra pode atuar: o estrago de apagar ou mover seria grande demais.

Uma regra age sobre os itens que estão DIRETAMENTE na sua pasta. Por isso são protegidas:
- a raiz de um disco (seus itens são o Windows, os Arquivos de Programas...);
- a pasta pessoal do usuário e a pasta que contém os usuários (seus itens são Documentos,
  Área de Trabalho, os perfis dos outros usuários...);
- as pastas do sistema e tudo que há dentro delas.
Subpastas da pasta pessoal (Downloads, por exemplo) não são protegidas.
"""

import os
import sys
from pathlib import Path
from typing import Optional

_VARIAVEIS_DO_WINDOWS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                         "ProgramData")
# Lista de melhor esforço: o programa é voltado ao Windows.
_PASTAS_DO_SISTEMA_POSIX = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin",
                            "/sys", "/usr")


def _resolver(caminho) -> str:
    """Caminho absoluto, com atalhos (symlinks/junctions) resolvidos e sem diferenciar
    maiúsculas no Windows. Resolver evita que um atalho para C:\\Windows passe despercebido."""
    return os.path.normcase(os.path.realpath(caminho))


def _pastas_do_sistema() -> list:
    pastas = [os.environ[v] for v in _VARIAVEIS_DO_WINDOWS if os.environ.get(v)]
    if sys.platform != "win32":
        pastas += _PASTAS_DO_SISTEMA_POSIX
    return pastas


def _dentro_de(alvo: str, pasta: str) -> bool:
    pasta = pasta.rstrip(os.sep)
    return alvo == pasta or alvo.startswith(pasta + os.sep)


def pasta_do_sistema_que_contem(caminho) -> Optional[str]:
    """A pasta do sistema que é (ou contém) o caminho, ou None."""
    alvo = _resolver(caminho)
    for pasta in _pastas_do_sistema():
        if _dentro_de(alvo, _resolver(pasta)):
            return pasta
    return None


def motivo_pasta_protegida(caminho) -> Optional[str]:
    """Por que a pasta é protegida (texto para a mensagem de erro), ou None se pode ser usada."""
    alvo = _resolver(caminho)

    if alvo == os.path.normcase(Path(os.path.realpath(caminho)).anchor):
        return "a raiz de um disco"

    home = Path.home()
    if alvo == _resolver(home):
        return "a pasta pessoal do usuário"
    if alvo == _resolver(home.parent):
        return "a pasta que contém os usuários"

    pasta = pasta_do_sistema_que_contem(caminho)
    if pasta:
        return f"uma pasta do sistema ({pasta})"
    return None
