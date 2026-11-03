"""Caminhos conhecidos do sistema."""

import ctypes
import os
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

# GUID do Known Folder "Downloads" (respeita redirecionamento por OneDrive/GPO)
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def obter_pasta_downloads() -> Path:
    """Resolve a pasta de Downloads real do usuário (Known Folders no Windows)."""
    if sys.platform == "win32":
        try:

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            guid = GUID.from_buffer_copy(uuid.UUID(FOLDERID_DOWNLOADS).bytes_le)
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

    return Path.home() / "Downloads"


def arquivo_de_regras_padrao() -> Path:
    """Onde o `rodar` procura as regras quando não se usa --config: a pasta Zelador dentro
    de APPDATA no Windows, ou ~/.config/zelador nos demais sistemas."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Zelador" / "regras.yml"
    return Path.home() / ".config" / "zelador" / "regras.yml"
