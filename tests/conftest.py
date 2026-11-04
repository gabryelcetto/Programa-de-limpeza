import shutil
from pathlib import Path

import pytest

from zelador import arquivos, caminhos


class LixeiraFalsa:
    """No lugar da Lixeira de verdade: guarda o que foi 'enviado' numa pasta do teste.

    Aplicada a TODOS os testes (autouse), nas duas camadas: a função do Zelador
    (`arquivos.enviar_para_lixeira`) e a da biblioteca (`send2trash.send2trash`). Assim, nem um
    defeito num teste consegue colocar arquivos na Lixeira de quem roda a suíte."""

    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.enviados = []  # caminhos recebidos pela função do Zelador
        self.da_biblioteca = []  # caminhos recebidos pela send2trash

    def _guardar(self, item) -> None:
        item = Path(item)
        numero = len(list(self.pasta.iterdir()))  # 0, 1, 2...: nomes únicos mesmo se repetidos
        shutil.move(item, self.pasta / f"{numero}_{item.name}")

    def enviar_para_lixeira(self, item) -> None:
        self.enviados.append(Path(item))
        self._guardar(item)

    def enviar_de_baixo_nivel(self, caminho) -> None:
        self.da_biblioteca.append(Path(caminho))
        self._guardar(caminho)


@pytest.fixture(autouse=True)
def lixeira_falsa(monkeypatch, tmp_path_factory):
    falsa = LixeiraFalsa(tmp_path_factory.mktemp("lixeira_falsa"))
    monkeypatch.setattr(arquivos, "enviar_para_lixeira", falsa.enviar_para_lixeira)
    monkeypatch.setattr(arquivos.send2trash, "send2trash", falsa.enviar_de_baixo_nivel)
    return falsa


@pytest.fixture(autouse=True)
def historico_isolado(monkeypatch, tmp_path_factory):
    """O histórico padrão vai para uma pasta do teste: nenhuma execução da suíte escreve no
    histórico de verdade de quem a roda."""
    arquivo = tmp_path_factory.mktemp("historico_padrao") / "historico.jsonl"
    monkeypatch.setattr(caminhos, "arquivo_de_historico_padrao", lambda: arquivo)
    return arquivo
