"""Ponto de entrada do .exe. O PyInstaller precisa de um script com imports absolutos
(o `zelador/__main__.py` usa import relativo, que só funciona dentro do pacote)."""

import sys

from zelador.cli import main

if __name__ == "__main__":
    sys.exit(main())
