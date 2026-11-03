import shutil
import subprocess
import sys

import pytest

from limpeza_downloads import arquivos


def criar(pasta, nome, conteudo="x"):
    arquivo = pasta / nome
    arquivo.write_text(conteudo)
    return arquivo


def test_move_arquivo_e_cria_a_pasta_de_destino(tmp_path):
    origem = criar(tmp_path, "a.pdf", "conteudo")
    destino = tmp_path / "novo" / "sub"

    resultado = arquivos.mover_item(origem, destino)

    assert resultado == destino / "a.pdf"
    assert resultado.read_text() == "conteudo"
    assert not origem.exists()


def test_move_pasta_inteira_com_o_conteudo(tmp_path):
    pasta = tmp_path / "projeto"
    (pasta / "sub").mkdir(parents=True)
    criar(pasta / "sub", "dado.txt", "oi")

    resultado = arquivos.mover_item(pasta, tmp_path / "arquivo")

    assert (resultado / "sub" / "dado.txt").read_text() == "oi"
    assert not pasta.exists()


def test_nome_repetido_nao_sobrescreve_e_numera(tmp_path):
    destino = tmp_path / "destino"
    destino.mkdir()
    criar(destino, "a.pdf", "ja estava aqui")
    criar(destino, "a (1).pdf", "tambem estava")
    nova = criar(tmp_path, "a.pdf", "novo")

    resultado = arquivos.mover_item(nova, destino)

    assert resultado.name == "a (2).pdf"
    assert resultado.read_text() == "novo"
    assert (destino / "a.pdf").read_text() == "ja estava aqui"
    assert (destino / "a (1).pdf").read_text() == "tambem estava"


def test_pasta_com_ponto_no_nome_e_numerada_no_fim(tmp_path):
    destino = tmp_path / "destino"
    (destino / "v1.2").mkdir(parents=True)
    pasta = tmp_path / "v1.2"
    pasta.mkdir()

    resultado = arquivos.mover_item(pasta, destino)

    assert resultado.name == "v1.2 (1)"


def test_arquivo_sem_extensao_e_oculto_sao_numerados(tmp_path):
    destino = tmp_path / "destino"
    destino.mkdir()
    for nome in ("LEIAME", ".gitignore"):
        criar(destino, nome)
        resultado = arquivos.mover_item(criar(tmp_path, nome), destino)
        assert resultado.name == f"{nome} (1)"


def test_mover_pasta_para_dentro_de_si_mesma_falha_sem_perder_nada(tmp_path):
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    arquivo = criar(pasta, "dado.txt", "importante")

    with pytest.raises(OSError):
        arquivos.mover_item(pasta, pasta / "dentro")

    assert arquivo.read_text() == "importante"


def test_falha_no_meio_desfaz_a_copia_parcial(tmp_path, monkeypatch):
    origem = criar(tmp_path, "a.txt", "original")
    destino = tmp_path / "destino"

    def mover_e_falhar(src, dst):
        shutil.copy2(src, dst)  # simula a cópia entre volumes que falha ao apagar a origem
        raise PermissionError("não foi possível apagar a origem")

    monkeypatch.setattr(arquivos.shutil, "move", mover_e_falhar)

    with pytest.raises(PermissionError):
        arquivos.mover_item(origem, destino)

    assert origem.read_text() == "original"
    assert list(destino.iterdir()) == []  # nada de duplicata


@pytest.mark.skipif(sys.platform != "win32", reason="junctions só existem no Windows")
def test_recusa_mover_junction_e_nao_toca_no_destino_dela(tmp_path):
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    dado = criar(alvo, "dado.txt", "nao mexer")
    atalho = tmp_path / "atalho"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(atalho), str(alvo)],
                   check=True, capture_output=True)

    with pytest.raises(OSError, match="atalho"):
        arquivos.mover_item(atalho, tmp_path / "destino")

    assert dado.read_text() == "nao mexer"
    assert atalho.exists()


def test_recusa_mover_symlink_de_pasta(tmp_path):
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    atalho = tmp_path / "atalho"
    try:
        atalho.symlink_to(alvo, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar symlinks neste ambiente")

    with pytest.raises(OSError, match="atalho"):
        arquivos.mover_item(atalho, tmp_path / "destino")

    assert alvo.exists()
