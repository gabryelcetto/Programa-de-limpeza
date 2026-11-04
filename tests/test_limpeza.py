import os
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from zelador import arquivos, limpeza, logs, regras

DIAS = 7
DIA = 24 * 3600


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    """No Windows o os.utime não altera a data de criação, então os testes
    de idade usam apenas o mtime. A regra da criação é testada à parte."""
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


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
    assert (resumo.aplicados, resumo.ignorados, resumo.erros) == (1, 1, 0)


def test_dry_run_nao_apaga_nada(tmp_path):
    antigo = criar_arquivo(tmp_path, "antigo.txt", dias=10, conteudo="12345")

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=True)

    assert antigo.exists()
    assert resumo.aplicados == 1
    assert resumo.bytes == 5


def test_pasta_com_arquivo_recente_dentro_e_mantida(tmp_path):
    pasta = tmp_path / "projeto"
    pasta.mkdir()
    criar_arquivo(pasta, "velho.txt", dias=30)
    criar_arquivo(pasta, "novo.txt", dias=1)
    envelhecer(pasta, 30)  # o mtime da pasta em si é antigo

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert pasta.exists()
    assert resumo.aplicados == 0
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
    assert resumo.bytes == 5


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

    arquivos.deletar_item(atalho)

    assert not atalho.exists() and not atalho.is_symlink()
    assert arquivo_destino.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="junctions só existem no Windows")
def test_junction_para_pasta_remove_so_o_atalho(tmp_path):
    destino = tmp_path / "fora" / "importante"
    destino.mkdir(parents=True)
    arquivo_destino = criar_arquivo(destino, "dado.txt", dias=1)

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    atalho = downloads / "atalho"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(atalho), str(destino)],
                   check=True, capture_output=True)

    arquivos.deletar_item(atalho)

    assert not atalho.exists()
    assert arquivo_destino.exists()


def test_erro_ao_apagar_de_vez_e_contado_e_nao_derruba_o_resto(tmp_path, monkeypatch):
    ruim = criar_arquivo(tmp_path, "a_ruim.txt", dias=10)
    bom = criar_arquivo(tmp_path, "b_bom.txt", dias=10)
    apagar_de_verdade = regras.deletar_item

    def apagar(item):
        if item.name == "a_ruim.txt":
            raise PermissionError("arquivo em uso")
        apagar_de_verdade(item)

    monkeypatch.setattr(regras, "deletar_item", apagar)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False, permanente=True)

    assert (resumo.aplicados, resumo.erros) == (1, 1)
    assert ruim.exists()
    assert not bom.exists()


def test_erro_ao_enviar_para_a_lixeira_e_contado_e_nao_derruba_o_resto(
        tmp_path, monkeypatch, lixeira_falsa):
    ruim = criar_arquivo(tmp_path, "a_ruim.txt", dias=10)
    bom = criar_arquivo(tmp_path, "b_bom.txt", dias=10)

    def enviar(item):
        if Path(item).name == "a_ruim.txt":
            raise OSError("o arquivo está em uso")
        lixeira_falsa.enviar_para_lixeira(item)

    monkeypatch.setattr(arquivos, "enviar_para_lixeira", enviar)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)  # o padrão é a Lixeira

    assert (resumo.aplicados, resumo.erros) == (1, 1)
    assert ruim.exists() and not bom.exists()


def test_por_padrao_vai_para_a_lixeira_e_nao_apaga_de_vez(tmp_path, monkeypatch, lixeira_falsa):
    antigo = criar_arquivo(tmp_path, "antigo.txt", dias=10, conteudo="guardado")
    recente = criar_arquivo(tmp_path, "recente.txt", dias=1)

    def proibido(caminho):
        raise AssertionError("o padrão não pode apagar de vez")

    monkeypatch.setattr(regras, "deletar_item", proibido)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert resumo.aplicados == 1 and resumo.erros == 0
    assert not antigo.exists() and recente.exists()
    assert lixeira_falsa.enviados == [antigo]
    assert (lixeira_falsa.pasta / "0_antigo.txt").read_text() == "guardado"  # dá para restaurar


def test_permanente_apaga_de_vez_e_nao_usa_a_lixeira(tmp_path, lixeira_falsa):
    antigo = criar_arquivo(tmp_path, "antigo.txt", dias=10)

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False, permanente=True)

    assert resumo.aplicados == 1 and not antigo.exists()
    assert lixeira_falsa.enviados == [] and list(lixeira_falsa.pasta.iterdir()) == []


@pytest.mark.parametrize("permanente", [False, True])
def test_simulacao_nao_mexe_em_nada_em_nenhum_dos_modos(tmp_path, lixeira_falsa, permanente):
    antigo = criar_arquivo(tmp_path, "antigo.txt", dias=10, conteudo="12345")

    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=True, permanente=permanente)

    assert antigo.exists() and lixeira_falsa.enviados == []
    assert (resumo.aplicados, resumo.bytes) == (1, 5)


def test_pasta_vazia_nao_quebra(tmp_path):
    resumo = limpeza.limpar(tmp_path, DIAS, dry_run=False)

    assert (resumo.aplicados, resumo.ignorados, resumo.erros) == (0, 0, 0)


def test_data_referencia_usa_o_maior_entre_modificacao_e_criacao(monkeypatch):
    # desfaz o patch do autouse para testar a função real
    monkeypatch.undo()
    info = SimpleNamespace(st_mtime=100.0, st_birthtime=500.0, st_ctime=0.0)
    assert arquivos._data_referencia(info) == 500.0

    info = SimpleNamespace(st_mtime=900.0, st_birthtime=500.0, st_ctime=0.0)
    assert arquivos._data_referencia(info) == 900.0


@pytest.mark.parametrize(
    "bytes_, esperado",
    [(0, "0.0 B"), (1023, "1023.0 B"), (1024, "1.0 KB"), (5 * 1024**2, "5.0 MB"),
     (3 * 1024**4, "3.0 TB")],
)
def test_formatar_tamanho(bytes_, esperado):
    assert arquivos.formatar_tamanho(bytes_) == esperado


def test_logs_antigos_sao_removidos_e_pastas_estranhas_ignoradas(tmp_path):
    antigo = tmp_path / "2000-01-01"
    recente = tmp_path / datetime.now().strftime(logs.FORMATO_DATA_LOG)
    estranha = tmp_path / "nao-e-data"
    for pasta in (antigo, recente, estranha):
        pasta.mkdir()

    logs.deletar_logs_antigos(tmp_path)

    assert not antigo.exists()
    assert recente.exists()
    assert estranha.exists()
