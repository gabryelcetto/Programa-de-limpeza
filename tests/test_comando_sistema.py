import os

import psutil
import pytest

from zelador import cli, comando_sistema, logs, sistema
from zelador.comando_sistema import barra, formatar_duracao, formatar_processos, formatar_status

GB = 1024**3
MB = 1024**2


@pytest.fixture(autouse=True)
def fechar_logs():
    """Todo cli.main abre um arquivo de log; no Windows isso impede apagar o tmp_path."""
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


def status_exemplo(**mudancas):
    base = dict(
        cpu=sistema.UsoCpu(21.0, 8, 4),
        memoria=sistema.UsoMemoria(16 * GB, 8 * GB, 50.0, 4 * GB, 1 * GB),
        discos=(sistema.UsoDisco("C:\\", "C:\\", "NTFS", 200 * GB, 50 * GB, 25.0),),
        segundos_ligado=3 * 3600 + 5 * 60,
    )
    base.update(mudancas)
    return sistema.Status(**base)


@pytest.fixture
def cli_com(tmp_path):
    def executar(*args):
        return cli.main([*args, "--log-dir", str(tmp_path / "logs")])
    return executar


# --------------------------------------------------------------- barra


@pytest.mark.parametrize("percentual, cheios", [
    (0, 0), (25, 5), (50, 10), (100, 20), (150, 20), (-5, 0), (3, 1), (2, 0),
])
def test_barra_tem_sempre_a_mesma_largura(percentual, cheios):
    texto = barra(percentual)

    assert texto == "[" + "#" * cheios + "-" * (20 - cheios) + "]"
    assert len(texto) == 22


def test_barra_com_largura_personalizada():
    assert barra(50, largura=4) == "[##--]"


# ------------------------------------------------------------- duração


@pytest.mark.parametrize("segundos, esperado", [
    (0, "menos de 1 min"),
    (59, "menos de 1 min"),
    (60, "1min"),
    (59 * 60, "59min"),
    (3600, "1h 00min"),
    (3660, "1h 01min"),
    (23 * 3600 + 59 * 60, "23h 59min"),
    (24 * 3600, "1 dia 0h 00min"),
    (25 * 3600, "1 dia 1h 00min"),
    (2 * 24 * 3600 + 3 * 3600 + 7 * 60, "2 dias 3h 07min"),
    (1659797, "19 dias 5h 03min"),
])
def test_formatar_duracao(segundos, esperado):
    assert formatar_duracao(segundos) == esperado


# ---------------------------------------------------------------- status


def test_formatar_status_mostra_cada_informacao():
    texto = formatar_status(status_exemplo())
    linhas = texto.splitlines()

    assert linhas[0].startswith("CPU")
    assert "21.0%" in linhas[0] and "8 lógicos / 4 físicos" in linhas[0]
    assert linhas[1].startswith("Memória")
    assert "50.0%" in linhas[1] and "8.0 GB de 16.0 GB em uso" in linhas[1]
    assert linhas[2].startswith("Swap") and "25.0%" in linhas[2]
    assert "1.0 GB de 4.0 GB em uso" in linhas[2]
    assert linhas[3] == "Ligado    há 3h 05min"
    assert linhas[5] == "Discos"
    assert linhas[6].split()[:2] == ["C:\\", "NTFS"]
    assert "25.0%" in linhas[6] and "50.0 GB de 200.0 GB em uso" in linhas[6]


def test_formatar_status_barras_acompanham_os_percentuais():
    texto = formatar_status(status_exemplo())

    assert barra(21.0) in texto and barra(50.0) in texto and barra(25.0) in texto


def test_formatar_status_sem_swap_nao_mostra_a_linha():
    memoria = sistema.UsoMemoria(16 * GB, 8 * GB, 50.0, 0, 0)

    assert "Swap" not in formatar_status(status_exemplo(memoria=memoria))


def test_formatar_status_sem_nucleos_fisicos():
    texto = formatar_status(status_exemplo(cpu=sistema.UsoCpu(10.0, 4, None)))

    assert "núcleos: 4 lógicos" in texto and "físicos" not in texto


def test_formatar_status_sem_discos_nao_mostra_o_titulo():
    assert "Discos" not in formatar_status(status_exemplo(discos=()))


def test_formatar_status_alinha_discos_de_nomes_diferentes():
    discos = (sistema.UsoDisco("a", "/", "ext4", GB, GB // 2, 50.0),
              sistema.UsoDisco("b", "/mnt/dados", "ext4", GB, GB // 4, 25.0))

    linhas = formatar_status(status_exemplo(discos=discos)).splitlines()[-2:]

    assert linhas[0].index("[") == linhas[1].index("[")


def test_status_executa_e_imprime(cli_com, monkeypatch, capsys):
    monkeypatch.setattr(sistema, "coletar_status", lambda: status_exemplo())

    codigo = cli_com("status")
    saida = capsys.readouterr().out

    assert codigo == 0
    assert saida.strip() == formatar_status(status_exemplo())


def test_status_com_falha_do_sistema_retorna_um(cli_com, monkeypatch, capsys, caplog):
    def falhar():
        raise psutil.AccessDenied()

    monkeypatch.setattr(sistema, "coletar_status", falhar)

    codigo = cli_com("status")

    assert codigo == 1
    assert capsys.readouterr().out == ""
    assert "Não foi possível ler o estado do sistema" in caplog.text


def test_status_nao_aceita_dry_run_porque_so_le(cli_com, capsys):
    with pytest.raises(SystemExit) as saida:
        cli_com("status", "--dry-run")

    assert saida.value.code == 2


# ------------------------------------------------------------- processos


def processos_exemplo():
    return [sistema.Processo(4321, "chrome.exe", 812 * MB, None),
            sistema.Processo(7, "protegido.exe", None, None),
            sistema.Processo(99, "x" * 50 + ".exe", 5 * MB, None)]


def test_formatar_processos_por_memoria():
    linhas = formatar_processos(processos_exemplo(), "memoria").splitlines()

    assert linhas[0] == "Top 3 processos por memória"
    assert linhas[1].split() == ["PID", "Nome", "Memória", "CPU"]
    assert linhas[2].split() == ["4321", "chrome.exe", "812.0", "MB", "-"]
    assert linhas[3].split() == ["7", "protegido.exe", "n/d", "-"]


def test_formatar_processos_por_cpu_mostra_o_percentual():
    processos = [sistema.Processo(1, "a.exe", 10 * MB, 12.34)]

    linhas = formatar_processos(processos, "cpu").splitlines()

    assert "CPU (% do total da máquina)" in linhas[0]
    assert linhas[2].split()[-1] == "12.3%"


def test_formatar_processos_corta_nomes_longos_sem_desalinhar():
    linhas = formatar_processos(processos_exemplo(), "memoria").splitlines()

    nome_longo = linhas[4]
    assert "..." in nome_longo and len(nome_longo) == len(linhas[2])


def test_processos_executa_com_os_argumentos(cli_com, monkeypatch, capsys):
    chamadas = []

    def listar(ordenar, limite):
        chamadas.append((ordenar, limite))
        return processos_exemplo()

    monkeypatch.setattr(sistema, "listar_processos", listar)

    assert cli_com("processos") == 0
    assert cli_com("processos", "--top", "3", "--ordenar", "cpu") == 0

    assert chamadas == [("memoria", 10), ("cpu", 3)]
    assert "chrome.exe" in capsys.readouterr().out


@pytest.mark.parametrize("argumentos", [
    ["--top", "0"], ["--top", "-3"], ["--top", "abc"], ["--top", "2.5"],
    ["--ordenar", "nome"], ["--ordenar", "MEMORIA"], ["--dry-run"],
])
def test_processos_rejeita_argumentos_invalidos(cli_com, argumentos, monkeypatch):
    monkeypatch.setattr(sistema, "listar_processos",
                        lambda *a: pytest.fail("não deveria ter listado nada"))

    with pytest.raises(SystemExit) as saida:
        cli_com("processos", *argumentos)

    assert saida.value.code == 2


def test_processos_com_falha_do_sistema_retorna_um(cli_com, monkeypatch, caplog):
    def falhar(*args):
        raise OSError("sem acesso")

    monkeypatch.setattr(sistema, "listar_processos", falhar)

    assert cli_com("processos") == 1
    assert "Não foi possível listar os processos" in caplog.text


# --------------------------------------------- de verdade (sem simulação)


def test_status_real(cli_com, capsys):
    codigo = cli_com("status")
    saida = capsys.readouterr().out

    assert codigo == 0
    for rotulo in ("CPU", "Memória", "Ligado", "Discos"):
        assert rotulo in saida


def test_processos_real_lista_o_pedido(cli_com, capsys):
    codigo = cli_com("processos", "--top", "3")
    linhas = capsys.readouterr().out.strip().splitlines()

    assert codigo == 0
    assert linhas[0] == "Top 3 processos por memória"
    assert len(linhas) == 2 + 3


def test_processos_real_por_cpu(cli_com, capsys):
    assert cli_com("processos", "--ordenar", "cpu", "--top", "2") == 0

    assert "por CPU" in capsys.readouterr().out


def test_ajuda_lista_os_comandos_de_sistema(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["--help"])
    texto = capsys.readouterr().out

    assert saida.value.code == 0
    assert "status" in texto and "processos" in texto
    assert comando_sistema.__doc__ and os.sep
