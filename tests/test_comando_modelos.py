import os
import re

import pytest

from zelador import caminhos, cli, comando_modelos, logs, modelos


@pytest.fixture(autouse=True)
def fechar_logs():
    """Todo cli.main abre um arquivo de log; no Windows isso impede apagar o tmp_path."""
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def pastas_falsas(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(caminhos, "obter_pasta_documentos", lambda: tmp_path / "Documentos")
    monkeypatch.setattr(caminhos, "obter_pasta_imagens", lambda: tmp_path / "Imagens")


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    pasta = tmp_path / "AppData"
    monkeypatch.setenv("APPDATA", str(pasta))
    return pasta


@pytest.fixture
def cli_com(tmp_path):
    def executar(*args):
        return cli.main([*args, "--log-dir", str(tmp_path / "logs")])
    return executar


# ---------------------------------------------------------------- modelos


def test_modelos_lista_cada_id_com_o_titulo_e_marca_os_recomendados(cli_com, capsys):
    codigo = cli_com("modelos")
    saida = capsys.readouterr().out

    assert codigo == 0
    for modelo in modelos.MODELOS:
        assert modelo.id in saida and modelo.titulo in saida
    assert saida.count("(recomendado)") == sum(m.padrao for m in modelos.MODELOS)
    assert "zelador iniciar" in saida  # diz qual é o próximo passo


# ---------------------------------------------------------------- iniciar


def test_iniciar_grava_no_arquivo_padrao_e_cria_as_pastas(cli_com, appdata):
    destino = appdata / "Zelador" / "regras.yml"
    assert not appdata.exists()

    codigo = cli_com("iniciar")

    assert codigo == 0
    assert destino.read_text(encoding="utf-8") == modelos.gerar_yaml()


def test_o_arquivo_criado_e_o_que_o_rodar_procura_sem_config(cli_com, appdata, tmp_path):
    cli_com("iniciar")

    assert cli_com("listar") == 0  # sem --config: acha o arquivo padrão que o iniciar criou
    assert cli_com("rodar", "--dry-run") == 0


def test_iniciar_com_destino_e_modelos_escolhidos(cli_com, tmp_path):
    destino = tmp_path / "minhas" / "regras.yml"

    codigo = cli_com("iniciar", "--destino", str(destino), "--modelo", "organizar-imagens",
                     "--modelo", "organizar-pdfs")

    assert codigo == 0
    assert destino.read_text(encoding="utf-8") == modelos.gerar_yaml(
        ["organizar-imagens", "organizar-pdfs"])


def test_modelo_inexistente_e_rejeitado_antes_de_gravar_qualquer_coisa(
        cli_com, tmp_path, capsys):
    destino = tmp_path / "regras.yml"

    with pytest.raises(SystemExit) as saida:
        cli_com("iniciar", "--destino", str(destino), "--modelo", "nao-existe")

    assert saida.value.code == 2
    assert not destino.exists()
    assert "organizar-pdfs" in capsys.readouterr().err  # o erro lista os ids válidos


def test_nunca_sobrescreve_um_arquivo_existente_sem_forcar(cli_com, tmp_path, caplog):
    destino = tmp_path / "regras.yml"
    destino.write_text("# as minhas regras, escritas à mão\n", encoding="utf-8")

    codigo = cli_com("iniciar", "--destino", str(destino))

    assert codigo == 1
    assert destino.read_text(encoding="utf-8") == "# as minhas regras, escritas à mão\n"
    assert "Já existe" in caplog.text and "--forcar" in caplog.text
    assert [p.name for p in tmp_path.glob("regras.yml*")] == ["regras.yml"]  # nem cópia nem tmp


def test_forcar_guarda_uma_copia_datada_e_depois_substitui(cli_com, tmp_path, caplog):
    destino = tmp_path / "regras.yml"
    destino.write_text("# conteudo antigo\n", encoding="utf-8")

    codigo = cli_com("iniciar", "--destino", str(destino), "--forcar")

    copias = list(tmp_path.glob("regras.yml.*.bak"))
    assert codigo == 0
    assert destino.read_text(encoding="utf-8") == modelos.gerar_yaml()
    assert len(copias) == 1
    assert re.fullmatch(r"regras\.yml\.\d{8}-\d{6}\.bak", copias[0].name)
    assert copias[0].read_text(encoding="utf-8") == "# conteudo antigo\n"  # nada se perdeu
    assert "Cópia do arquivo anterior guardada" in caplog.text


def test_forcar_duas_vezes_nunca_apaga_uma_copia_antiga(cli_com, tmp_path):
    destino = tmp_path / "regras.yml"
    destino.write_text("# versao 1\n", encoding="utf-8")
    cli_com("iniciar", "--destino", str(destino), "--forcar")
    destino.write_text("# versao 2\n", encoding="utf-8")

    cli_com("iniciar", "--destino", str(destino), "--forcar")

    conteudos = sorted(p.read_text(encoding="utf-8") for p in tmp_path.glob("regras.yml.*.bak"))
    assert conteudos == ["# versao 1\n", "# versao 2\n"]  # as duas cópias, mesmo no mesmo segundo


def test_forcar_sem_arquivo_anterior_nao_cria_copia(cli_com, tmp_path):
    destino = tmp_path / "regras.yml"

    assert cli_com("iniciar", "--destino", str(destino), "--forcar") == 0

    assert list(tmp_path.glob("*.bak")) == []


def test_imprimir_mostra_na_tela_e_nao_grava_nada(cli_com, appdata, tmp_path, capsys):
    codigo = cli_com("iniciar", "--imprimir")

    assert codigo == 0
    assert capsys.readouterr().out == modelos.gerar_yaml()
    assert not appdata.exists()  # nem a pasta foi criada


def test_imprimir_ignora_um_arquivo_existente(cli_com, tmp_path, capsys):
    destino = tmp_path / "regras.yml"
    destino.write_text("# meu\n", encoding="utf-8")

    codigo = cli_com("iniciar", "--imprimir", "--destino", str(destino))

    assert codigo == 0 and destino.read_text(encoding="utf-8") == "# meu\n"


def test_destino_que_e_uma_pasta_e_recusado(cli_com, tmp_path, caplog):
    codigo = cli_com("iniciar", "--destino", str(tmp_path))

    assert codigo == 1
    assert "é uma pasta" in caplog.text


def test_falha_na_gravacao_deixa_o_original_intacto_e_sem_lixo(cli_com, tmp_path,
                                                               monkeypatch, caplog):
    destino = tmp_path / "regras.yml"
    destino.write_text("# original precioso\n", encoding="utf-8")

    def falhar(origem, alvo):
        raise PermissionError("disco cheio ou arquivo em uso")

    monkeypatch.setattr(comando_modelos.os, "replace", falhar)

    codigo = cli_com("iniciar", "--destino", str(destino), "--forcar")

    assert codigo == 1
    assert destino.read_text(encoding="utf-8") == "# original precioso\n"
    assert not list(tmp_path.glob("*.tmp"))  # o temporário foi limpo
    assert "Não foi possível gravar" in caplog.text


def test_falha_ao_criar_a_pasta_vira_erro_claro_e_nao_traceback(cli_com, tmp_path, caplog):
    bloqueio = tmp_path / "arquivo_no_lugar_da_pasta"
    bloqueio.write_text("x")

    codigo = cli_com("iniciar", "--destino", str(bloqueio / "sub" / "regras.yml"))

    assert codigo == 1
    assert "Não foi possível gravar" in caplog.text


def test_o_arquivo_gerado_e_gravado_em_utf8_com_quebras_de_linha_unix(cli_com, tmp_path):
    destino = tmp_path / "regras.yml"

    cli_com("iniciar", "--destino", str(destino))

    bruto = destino.read_bytes()
    assert b"\r\n" not in bruto  # newline="\n": igual em qualquer sistema
    assert "Edite à vontade" in bruto.decode("utf-8")  # acentos preservados


def test_mensagem_final_ensina_os_proximos_passos(cli_com, tmp_path, caplog):
    destino = tmp_path / "regras.yml"

    cli_com("iniciar", "--destino", str(destino))

    assert f"Arquivo de regras criado em {destino} (3 regra(s))." in caplog.text
    assert "zelador listar" in caplog.text and "--dry-run" in caplog.text


def test_avisa_quando_uma_regra_nao_vale_neste_computador(cli_com, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(os.path, "expanduser", os.path.expanduser)
    # Downloads == pasta pessoal: as regras seriam recusadas pelas pastas protegidas
    monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    destino = tmp_path / "saida" / "regras.yml"

    codigo = cli_com("iniciar", "--destino", str(destino))

    assert codigo == 0 and destino.exists()  # grava mesmo assim: o usuário pode editar
    assert "Atenção, não vale neste computador" in caplog.text
    assert "pasta pessoal" in caplog.text


def test_o_arquivo_criado_passa_pelo_listar_e_pelo_rodar_em_simulacao(cli_com, tmp_path, capsys):
    destino = tmp_path / "regras.yml"
    cli_com("iniciar", "--destino", str(destino), "--modelo", "organizar-pdfs",
            "--modelo", "limpar-downloads-parados")
    capsys.readouterr()

    assert cli_com("listar", "--config", str(destino)) == 0
    saida = capsys.readouterr().out
    assert "2 regra(s) válida(s), 0 com problema." in saida
    assert "mover para" in saida and "enviar para a Lixeira" in saida
    assert cli_com("rodar", "--config", str(destino), "--dry-run") == 0


def test_a_ajuda_lista_os_comandos_novos(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    texto = capsys.readouterr().out

    assert "modelos" in texto and "iniciar" in texto


def test_ajuda_do_iniciar_mostra_todas_as_opcoes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["iniciar", "--help"])
    texto = capsys.readouterr().out

    for opcao in ("--modelo", "--destino", "--forcar", "--imprimir"):
        assert opcao in texto
    assert "organizar-pdfs" in texto  # os ids aparecem como escolhas válidas


@pytest.mark.parametrize("comando", ["modelos", "iniciar"])
def test_esses_comandos_nao_aceitam_dry_run(comando, capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main([comando, "--dry-run"])

    assert saida.value.code == 2
