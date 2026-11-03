import os
import stat
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from limpeza_downloads import limpeza

DIAS = 7
DIA = 24 * 3600


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    """No Windows o os.utime não altera a data de criação, então os testes
    de idade usam apenas o mtime. A regra da criação é testada à parte."""
    monkeypatch.setattr(limpeza, "_data_referencia", lambda info: info.st_mtime)


def envelhecer(caminho, dias):
    t = time.time() - dias * DIA
    os.utime(caminho, (t, t))


def criar_arquivo(pasta, nome, dias, conteudo="x"):
    arquivo = pasta / nome
    arquivo.write_text(conteudo)
    envelhecer(arquivo, dias)
    return arquivo


def test_apaga_arquivo_antigo_e_mantem_recente(tmp_path):
    antigo = criar_arquivo(tmp_path, "antigo.txt", dias=10)
    recente = criar_arquivo(tmp_path, "recente.txt", dias=1)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert not antigo.exists()
    assert recente.exists()
    assert (resumo.apagados, resumo.ignorados, resumo.erros) == (1, 1, 0)


def test_dry_run_nao_apaga_nada(tmp_path):
    antigo = criar_arquivo(tmp_path, "antigo.txt", dias=10, conteudo="12345")

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=True)

    assert antigo.exists()
    assert resumo.apagados == 1
    assert resumo.bytes_liberados == 5


def test_pasta_com_arquivo_recente_dentro_e_mantida(tmp_path):
    pasta = tmp_path / "projeto"
    pasta.mkdir()
    criar_arquivo(pasta, "velho.txt", dias=30)
    criar_arquivo(pasta, "novo.txt", dias=1)
    envelhecer(pasta, 30)  # o mtime da pasta em si é antigo

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert pasta.exists()
    assert resumo.apagados == 0
    assert resumo.ignorados == 1


def test_pasta_inteira_antiga_e_apagada_com_tamanho_somado(tmp_path):
    pasta = tmp_path / "velha"
    (pasta / "sub").mkdir(parents=True)
    criar_arquivo(pasta, "a.txt", dias=30, conteudo="aaa")
    criar_arquivo(pasta / "sub", "b.txt", dias=30, conteudo="bb")
    envelhecer(pasta / "sub", 30)
    envelhecer(pasta, 30)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert not pasta.exists()
    assert resumo.bytes_liberados == 5


def test_apaga_arquivo_somente_leitura(tmp_path):
    arquivo = criar_arquivo(tmp_path, "protegido.txt", dias=10)
    os.chmod(arquivo, stat.S_IREAD)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert not arquivo.exists()
    assert resumo.erros == 0


def test_apaga_pasta_com_arquivo_somente_leitura(tmp_path):
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    arquivo = criar_arquivo(pasta, "protegido.txt", dias=10)
    os.chmod(arquivo, stat.S_IREAD)
    envelhecer(pasta, 10)

    limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert not pasta.exists()


def test_symlink_para_pasta_remove_so_o_atalho(tmp_path):
    destino = tmp_path / "fora" / "importante"
    destino.mkdir(parents=True)
    arquivo_destino = criar_arquivo(destino, "dado.txt", dias=1)

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    atalho = downloads / "atalho"
    try:
        atalho.symlink_to(destino, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar symlinks neste ambiente")

    limpeza.deletar_item(atalho)

    assert not atalho.exists() and not atalho.is_symlink()
    assert arquivo_destino.exists()


def test_pasta_vazia_nao_quebra(tmp_path):
    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert (resumo.apagados, resumo.ignorados, resumo.erros) == (0, 0, 0)


def test_data_referencia_usa_o_maior_entre_modificacao_e_criacao(monkeypatch):
    # desfaz o patch do autouse para testar a função real
    monkeypatch.undo()
    info = SimpleNamespace(st_mtime=100.0, st_birthtime=500.0, st_ctime=0.0)
    assert limpeza._data_referencia(info) == 500.0

    info = SimpleNamespace(st_mtime=900.0, st_birthtime=500.0, st_ctime=0.0)
    assert limpeza._data_referencia(info) == 900.0


def test_item_antigo_respeita_o_limite():
    agora = datetime(2026, 1, 20)
    antigo = limpeza.Medicao(datetime(2026, 1, 10).timestamp(), 0)
    recente = limpeza.Medicao(datetime(2026, 1, 18).timestamp(), 0)

    assert limpeza.item_antigo(antigo, agora, 7)
    assert not limpeza.item_antigo(recente, agora, 7)


@pytest.mark.parametrize(
    "bytes_, esperado",
    [(0, "0.0 B"), (1023, "1023.0 B"), (1024, "1.0 KB"), (5 * 1024**2, "5.0 MB"),
     (3 * 1024**4, "3.0 TB")],
)
def test_formatar_tamanho(bytes_, esperado):
    assert limpeza._formatar_tamanho(bytes_) == esperado


@pytest.mark.parametrize("valor", ["0", "-3", "abc", "1.5"])
def test_dias_invalidos_sao_rejeitados(valor, tmp_path, capsys):
    with pytest.raises(SystemExit) as saida:
        limpeza.main(["--dias", valor, "--pasta", str(tmp_path), "--log-dir", str(tmp_path / "l")])

    assert saida.value.code == 2


def test_main_dry_run_retorna_zero_e_preserva_arquivos(tmp_path):
    pasta = tmp_path / "dl"
    pasta.mkdir()
    antigo = criar_arquivo(pasta, "antigo.txt", dias=30)

    codigo = limpeza.main(["--dry-run", "--pasta", str(pasta), "--log-dir", str(tmp_path / "logs")])

    assert codigo == 0
    assert antigo.exists()


def test_main_pasta_inexistente_retorna_um(tmp_path):
    codigo = limpeza.main(["--pasta", str(tmp_path / "nao_existe"),
                           "--log-dir", str(tmp_path / "logs")])

    assert codigo == 1


def test_logs_antigos_sao_removidos_e_pastas_estranhas_ignoradas(tmp_path):
    antigo = tmp_path / "2000-01-01"
    recente = tmp_path / datetime.now().strftime(limpeza.FORMATO_DATA_LOG)
    estranha = tmp_path / "nao-e-data"
    for pasta in (antigo, recente, estranha):
        pasta.mkdir()

    limpeza.deletar_logs_antigos(tmp_path)

    assert not antigo.exists()
    assert recente.exists()
    assert estranha.exists()
