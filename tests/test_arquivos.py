import errno
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from zelador import arquivos
from zelador.arquivos import enviar_para_lixeira as lixeira_real


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


def _simular_outro_disco(monkeypatch):
    """Faz o rename falhar como entre discos diferentes: o código tem de copiar e apagar."""
    def rename(origem, destino):
        raise OSError(errno.EXDEV, "outro disco (simulado)")

    monkeypatch.setattr(arquivos.os, "rename", rename)


def test_entre_discos_move_arquivo_copiando_e_apagando(tmp_path, monkeypatch):
    _simular_outro_disco(monkeypatch)
    origem = criar(tmp_path, "a.txt", "conteudo")

    resultado = arquivos.mover_item(origem, tmp_path / "destino")

    assert resultado.read_text() == "conteudo"
    assert not origem.exists()


def test_entre_discos_move_pasta_inteira(tmp_path, monkeypatch):
    _simular_outro_disco(monkeypatch)
    pasta = tmp_path / "proj"
    (pasta / "sub").mkdir(parents=True)
    criar(pasta / "sub", "dado.txt", "oi")

    resultado = arquivos.mover_item(pasta, tmp_path / "destino")

    assert (resultado / "sub" / "dado.txt").read_text() == "oi"
    assert not pasta.exists()


def test_entre_discos_falha_na_copia_desfaz_a_copia_parcial(tmp_path, monkeypatch):
    _simular_outro_disco(monkeypatch)
    origem = criar(tmp_path, "a.txt", "original")
    destino = tmp_path / "destino"

    def copiar_e_falhar(src, dst, **_):
        Path(dst).write_text("pela met")  # começou a copiar...
        raise OSError("disco cheio")  # ...e o disco encheu

    monkeypatch.setattr(arquivos.shutil, "copy2", copiar_e_falhar)

    with pytest.raises(OSError, match="disco cheio"):
        arquivos.mover_item(origem, destino)

    assert origem.read_text() == "original"
    assert list(destino.iterdir()) == []  # nada de cópia pela metade


def test_entre_discos_arquivo_que_nao_sai_da_origem_nao_fica_duplicado(tmp_path, monkeypatch):
    _simular_outro_disco(monkeypatch)
    origem = criar(tmp_path, "a.txt", "original")
    destino = tmp_path / "destino"
    deletar_real = arquivos.deletar_item

    def deletar(item):
        if item == origem:
            raise PermissionError("em uso por outro programa")
        deletar_real(item)

    monkeypatch.setattr(arquivos, "deletar_item", deletar)

    with pytest.raises(PermissionError):
        arquivos.mover_item(origem, destino)

    assert origem.read_text() == "original"
    assert list(destino.iterdir()) == []


def test_entre_discos_pasta_apagada_so_em_parte_nao_perde_nenhum_arquivo(tmp_path, monkeypatch):
    """O defeito que perdia dados: a origem era apagada em parte (um arquivo em uso) e a cópia
    completa no destino era desfeita em seguida, levando junto os arquivos que já tinham saído."""
    _simular_outro_disco(monkeypatch)
    pasta = tmp_path / "proj"
    pasta.mkdir()
    for nome in ("1.txt", "2.txt", "3.txt"):
        criar(pasta, nome, "dado " + nome)
    deletar_real = arquivos.deletar_item

    def deletar(item):
        if item == pasta:  # apaga dois e trava no terceiro, que está aberto em outro programa
            (pasta / "1.txt").unlink()
            (pasta / "2.txt").unlink()
            raise PermissionError("3.txt em uso")
        deletar_real(item)

    monkeypatch.setattr(arquivos, "deletar_item", deletar)

    with pytest.raises(OSError, match="Nada se perdeu"):
        arquivos.mover_item(pasta, tmp_path / "destino")

    copia = tmp_path / "destino" / "proj"
    for nome in ("1.txt", "2.txt", "3.txt"):
        assert (copia / nome).read_text() == "dado " + nome  # a cópia completa ficou
    assert (pasta / "3.txt").read_text() == "dado 3.txt"


def test_nome_ocupado_no_ultimo_instante_nao_apaga_o_arquivo_de_outro(tmp_path, monkeypatch):
    """Outro programa cria um arquivo com o mesmo nome entre a escolha do nome e a movimentação:
    o arquivo dele não pode ser apagado como se fosse uma cópia nossa."""
    origem = criar(tmp_path, "a.txt", "nosso")
    destino = tmp_path / "destino"
    destino.mkdir()
    rename_real = os.rename

    def rename(src, dst):
        Path(dst).write_text("de outro programa")
        if os.path.exists(dst) and sys.platform != "win32":
            raise FileExistsError(errno.EEXIST, "já existe", dst)  # como o Windows faz
        return rename_real(src, dst)

    monkeypatch.setattr(arquivos.os, "rename", rename)

    with pytest.raises(OSError):
        arquivos.mover_item(origem, destino)

    assert origem.read_text() == "nosso"
    assert (destino / "a.txt").read_text() == "de outro programa"


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


def test_recusa_atalho_de_pasta_quando_o_lstat_so_diz_que_e_link(tmp_path, monkeypatch):
    """Reproduz o Linux em qualquer sistema: o lstat de um symlink para pasta só informa
    'é link' (sem o bit de diretório), e só is_dir() revela que o destino é uma pasta."""
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    dado = criar(alvo, "dado.txt", "nao mexer")
    lstat_real = Path.lstat

    def lstat_estilo_posix(self):
        if self == alvo:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        return lstat_real(self)

    monkeypatch.setattr(Path, "lstat", lstat_estilo_posix)

    with pytest.raises(OSError, match="atalho"):
        arquivos.mover_item(alvo, tmp_path / "destino")

    assert dado.read_text() == "nao mexer"
    assert alvo.exists()


# ------------------------------------------------------------- Lixeira


def test_restaurar_devolve_ao_lugar_com_o_nome_original(tmp_path):
    guardado = criar(tmp_path, "a (1).pdf", "conteudo")
    original = tmp_path / "pasta" / "a.pdf"

    resultado = arquivos.restaurar_item(guardado, original)

    assert resultado == original  # "a (1).pdf" volta a ser "a.pdf"
    assert original.read_text() == "conteudo"
    assert not guardado.exists()


def test_restaurar_recria_a_pasta_original_que_sumiu(tmp_path):
    guardado = criar(tmp_path, "a.txt")
    original = tmp_path / "x" / "y" / "z" / "a.txt"

    assert arquivos.restaurar_item(guardado, original) == original


def test_restaurar_com_o_lugar_ocupado_nao_sobrescreve(tmp_path):
    guardado = criar(tmp_path, "guardado.txt", "do historico")
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    ocupante = criar(pasta, "a.txt", "ja estava aqui")

    resultado = arquivos.restaurar_item(guardado, pasta / "a.txt")

    assert resultado == pasta / "a (1).txt"
    assert resultado.read_text() == "do historico"
    assert ocupante.read_text() == "ja estava aqui"


def test_restaurar_pasta_inteira(tmp_path):
    guardada = tmp_path / "arquivo" / "projeto (1)"
    guardada.mkdir(parents=True)
    criar(guardada, "dado.txt", "oi")
    original = tmp_path / "projeto"

    resultado = arquivos.restaurar_item(guardada, original)

    assert (resultado / "dado.txt").read_text() == "oi"
    assert resultado == original


def test_restaurar_recusa_atalho_de_pasta(tmp_path):
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    atalho = tmp_path / "atalho"
    try:
        atalho.symlink_to(alvo, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar symlinks neste ambiente")

    with pytest.raises(OSError, match="atalho"):
        arquivos.restaurar_item(atalho, tmp_path / "volta")

    assert alvo.exists()


def test_restaurar_entre_discos_com_falha_na_copia_nao_perde_nada(tmp_path, monkeypatch):
    _simular_outro_disco(monkeypatch)
    guardado = criar(tmp_path, "g.txt", "original")
    original = tmp_path / "volta" / "g.txt"

    def copiar_e_falhar(src, dst, **_):
        Path(dst).write_text("pela met")
        raise OSError("disco cheio")

    monkeypatch.setattr(arquivos.shutil, "copy2", copiar_e_falhar)

    with pytest.raises(OSError, match="disco cheio"):
        arquivos.restaurar_item(guardado, original)

    assert guardado.read_text() == "original"
    assert not original.exists()


def test_a_lixeira_de_verdade_nunca_e_usada_nos_testes(lixeira_falsa):
    """Rede de segurança: o conftest troca as duas camadas por uma Lixeira falsa."""
    assert arquivos.enviar_para_lixeira == lixeira_falsa.enviar_para_lixeira
    assert arquivos.send2trash.send2trash == lixeira_falsa.enviar_de_baixo_nivel


def test_arquivo_vai_para_a_lixeira_com_o_caminho_como_texto(tmp_path, lixeira_falsa):
    arquivo = criar(tmp_path, "a.txt", "dado")

    lixeira_real(arquivo)

    assert lixeira_falsa.da_biblioteca == [arquivo]
    assert not arquivo.exists()
    guardado = lixeira_falsa.pasta / "0_a.txt"
    assert guardado.read_text() == "dado"  # não foi destruído: foi guardado


def test_pasta_inteira_vai_para_a_lixeira(tmp_path, lixeira_falsa):
    pasta = tmp_path / "projeto"
    (pasta / "sub").mkdir(parents=True)
    criar(pasta / "sub", "dado.txt", "oi")

    lixeira_real(pasta)

    assert not pasta.exists()
    assert (lixeira_falsa.pasta / "0_projeto" / "sub" / "dado.txt").read_text() == "oi"


def test_item_que_nao_existe_falha_sem_chamar_a_lixeira(tmp_path, lixeira_falsa):
    with pytest.raises(OSError):
        lixeira_real(tmp_path / "nao_existe.txt")

    assert lixeira_falsa.da_biblioteca == []


@pytest.mark.parametrize("erro", [
    arquivos.send2trash.TrashPermissionError("a.txt"),
    OSError("falha qualquer da biblioteca"),
    OSError(13, "Permissão negada"),
])
def test_erro_da_lixeira_vira_oserror_com_mensagem_clara(tmp_path, monkeypatch, erro):
    arquivo = criar(tmp_path, "a.txt")

    def falhar(caminho):
        raise erro

    monkeypatch.setattr(arquivos.send2trash, "send2trash", falhar)

    with pytest.raises(OSError) as capturado:  # o motor conta OSError como erro do item e segue
        lixeira_real(arquivo)

    mensagem = str(capturado.value)
    assert "a.txt" in mensagem and "Lixeira" in mensagem and "em uso" in mensagem
    assert capturado.value.__cause__ is erro  # o erro original continua acessível
    assert arquivo.exists()


def test_mensagem_final_inclui_o_codigo_do_sistema_quando_existe(tmp_path, monkeypatch):
    arquivo = criar(tmp_path, "a.txt")

    def negar(caminho):
        raise OSError(13, "Permissão negada")

    monkeypatch.setattr(arquivos.send2trash, "send2trash", negar)

    with pytest.raises(OSError) as capturado:
        lixeira_real(arquivo)

    assert str(capturado.value).endswith("(código do sistema: 13)")


def test_codigo_do_sistema_aparece_no_fim_da_mensagem():
    assert arquivos._codigo(OSError(13, "x")) == " (código do sistema: 13)"
    assert arquivos._codigo(OSError("sem número")) == ""
    erro = OSError("x")
    erro.winerror = -2144927705  # o código OLE que o Windows devolveu para arquivo em uso
    assert arquivos._codigo(erro) == " (código do sistema: 0x80270027)"


def test_arquivo_que_some_no_meio_continua_sendo_filenotfound(tmp_path, monkeypatch):
    arquivo = criar(tmp_path, "a.txt")

    def sumir(caminho):
        raise FileNotFoundError(caminho)

    monkeypatch.setattr(arquivos.send2trash, "send2trash", sumir)

    with pytest.raises(FileNotFoundError):  # não é "em uso": o tipo certo é preservado
        lixeira_real(arquivo)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions só existem no Windows")
def test_junction_nunca_vai_para_a_lixeira_e_o_destino_dela_fica_intacto(tmp_path, lixeira_falsa):
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    dado = criar(alvo, "dado.txt", "nao mexer")
    atalho = tmp_path / "atalho"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(atalho), str(alvo)],
                   check=True, capture_output=True)

    lixeira_real(atalho)

    assert not atalho.exists()  # o atalho saiu
    assert dado.read_text() == "nao mexer"  # o conteúdo apontado não foi tocado
    assert lixeira_falsa.da_biblioteca == []  # e a Lixeira nem foi consultada


def test_symlink_de_pasta_nunca_vai_para_a_lixeira(tmp_path, lixeira_falsa):
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    dado = criar(alvo, "dado.txt", "nao mexer")
    atalho = tmp_path / "atalho"
    try:
        atalho.symlink_to(alvo, target_is_directory=True)
    except OSError:
        pytest.skip("sem permissão para criar symlinks neste ambiente")

    lixeira_real(atalho)

    assert not atalho.is_symlink()
    assert dado.read_text() == "nao mexer"
    assert lixeira_falsa.da_biblioteca == []


def test_symlink_de_arquivo_nunca_vai_para_a_lixeira(tmp_path, lixeira_falsa):
    alvo = criar(tmp_path, "alvo.txt", "nao mexer")
    atalho = tmp_path / "atalho.txt"
    try:
        atalho.symlink_to(alvo)
    except OSError:
        pytest.skip("sem permissão para criar symlinks neste ambiente")

    lixeira_real(atalho)

    assert not atalho.is_symlink()
    assert alvo.read_text() == "nao mexer"
    assert lixeira_falsa.da_biblioteca == []
