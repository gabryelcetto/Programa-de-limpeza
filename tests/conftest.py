import time

import pytest


@pytest.fixture
def esperar_ate():
    """Espera uma condição ficar verdadeira, em vez de dormir um tempo fixo.

    Testes com threads e relógio real não podem supor quanto tempo o sistema leva: num runner
    com poucos núcleos disputados, um `sleep(0.5)` pode não bastar. Aqui o limite é só uma rede de
    segurança contra travar para sempre; o teste segue assim que a condição vale."""
    def esperar(condicao, limite=15.0, passo=0.01):
        fim = time.monotonic() + limite
        while not condicao():
            if time.monotonic() > fim:
                raise AssertionError(f"a condição não ficou verdadeira em {limite} s")
            time.sleep(passo)
    return esperar
