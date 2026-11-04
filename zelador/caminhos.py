"""Caminhos conhecidos do sistema."""

import ctypes
import os
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

# GUIDs dos Known Folders do Windows (respeitam redirecionamento por OneDrive/GPO)
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
FOLDERID_PICTURES = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"


def _pasta_conhecida(folderid: str, nome_padrao: str) -> Path:
    """A pasta do usuário que o Windows chama por esse identificador (Known Folder). Ela pode
    estar redirecionada (OneDrive, política de grupo) e até ter outro nome: `~/Documents` não
    serve. Fora do Windows, ou se a API falhar, cai em ~/<nome_padrao>."""
    if sys.platform == "win32":
        try:

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            guid = GUID.from_buffer_copy(uuid.UUID(folderid).bytes_le)
            caminho = ctypes.c_wchar_p()
            resultado = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(caminho)
            )
            if resultado == 0:
                try:
                    return Path(caminho.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(caminho)
        except Exception:
            pass  # cai no caminho padrão abaixo

    return Path.home() / nome_padrao


def obter_pasta_downloads() -> Path:
    return _pasta_conhecida(FOLDERID_DOWNLOADS, "Downloads")


def obter_pasta_documentos() -> Path:
    return _pasta_conhecida(FOLDERID_DOCUMENTS, "Documents")


def obter_pasta_imagens() -> Path:
    return _pasta_conhecida(FOLDERID_PICTURES, "Pictures")


def arquivo_de_historico_padrao() -> Path:
    """Onde o histórico do que o Zelador fez é guardado: ao lado dos logs, em LOCALAPPDATA no
    Windows (dado da máquina, não do perfil que viaja), ou ~/.local/share/zelador nos demais."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Zelador" / "historico.jsonl"
    return Path.home() / ".local" / "share" / "zelador" / "historico.jsonl"


def arquivo_de_regras_padrao() -> Path:
    """Onde o `rodar` procura as regras quando não se usa --config: a pasta Zelador dentro
    de APPDATA no Windows, ou ~/.config/zelador nos demais sistemas."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Zelador" / "regras.yml"
    return Path.home() / ".config" / "zelador" / "regras.yml"
