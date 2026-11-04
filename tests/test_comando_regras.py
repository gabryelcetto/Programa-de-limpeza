import os
import time
from pathlib import Path
from textwrap import dedent

import pytest

from zelador import arquivos, caminhos, cli, comando_regras, logs

DIA = 24 * 3600
RAIZ_DO_PROJETO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    """No Windows o os.utime não muda a data de criação; os testes usam só o mtime."""
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


@pytest.fixture(autouse=True)
def fechar_logs():
    """Todo cli.main abre um arquivo de log; no Windows isso impede apagar o tmp_path."""
    yield
    for handler in list(logs.logger.handlers):
        handler.close()
        logs.logger.removeHandler(handler)


class Ambiente:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.downloads = tmp_path / "Downloads"
        self.downloads.mkdir()
        self.destino = tmp_path / "Docs" / "PDFs"
        self.pasta_logs = tmp_path / "logs"
        monkeypatch.setattr(caminhos, "obter_pasta_downloads", lambda: self.downloads)

    def arquivo(self, nome, dias, pasta=None, conteudo="x"):
        caminho = (pasta or self.downloads) / nome
        caminho.write_text(conteudo)
        t = time.time() - dias * DIA
        os.utime(caminho, (t, t))
        return caminho

    def yml(self, texto, nome="regras.yml"):
        caminho = self.tmp / nome
        caminho.write_text(dedent(texto), encoding="utf-8")
        return caminho

    def cli(self, *args):
        return cli.main([*args, "--log-dir", str(self.pasta_logs)])


@pytest.fixture
def amb(tmp_path, monkeypatch):
    return Ambiente(tmp_path, monkeypatch)


REGRAS_PADRAO = """
    regras:
      - nome: Organizar PDFs
        pasta: "{{downloads}}"
        condicoes: {{extensoes: [pdf], idade_dias: 1}}
        acao: {{mover: '{destino}'}}
      - nome: Limpar antigos
        pasta: "{{downloads}}"
        condicoes: {{idade_dias: 7}}
        acao: apagar
"""


def regras_padrao(amb):
    return amb.yml(REGRAS_PADRAO.format(destino=amb.destino))


# ----------------------------------------------------------------- rodar


def test_rodar_executa_as_regras_em_ordem(amb):
    pdf = amb.arquivo("a.pdf", 10)  # a regra 1 (mover) vem antes da 2 (apagar): o pdf se salva
    antigo = amb.arquivo("velho.txt", 10)
    recente = amb.arquivo("novo.txt", 1)
    config = regras_padrao(amb)

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 0
    assert (amb.destino / "a.pdf").exists() and not pdf.exists()
    assert not antigo.exists()
    assert recente.exists()


def test_rodar_com_acao_lixeira_envia_para_a_lixeira(amb, lixeira_falsa, caplog):
    antigo = amb.arquivo("velho.txt", 10, conteudo="guardado")
    recente = amb.arquivo("novo.txt", 1)
    config = amb.yml("""
        regras:
          - nome: Limpar para a Lixeira
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: lixeira
    """)

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 0
    assert not antigo.exists() and recente.exists()
    assert lixeira_falsa.enviados == [antigo]
    assert (lixeira_falsa.pasta / "0_velho.txt").read_text() == "guardado"
    assert "enviado(a) para a Lixeira: velho.txt" in caplog.text


def test_rodar_dry_run_com_lixeira_nao_envia_nada(amb, lixeira_falsa):
    antigo = amb.arquivo("velho.txt", 10)
    config = amb.yml("""
        regras:
          - nome: Limpar para a Lixeira
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: lixeira
    """)

    assert amb.cli("rodar", "--config", str(config), "--dry-run") == 0
    assert antigo.exists() and lixeira_falsa.enviados == []


def test_listar_mostra_a_acao_lixeira_e_a_apagar_de_forma_distinta(amb, capsys):
    config = amb.yml("""
        regras:
          - nome: Para a Lixeira
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: lixeira
          - nome: De vez
            pasta: "{downloads}"
            condicoes: {idade_dias: 30}
            acao: apagar
    """)

    amb.cli("listar", "--config", str(config))
    saida = capsys.readouterr().out

    assert "acao  : enviar para a Lixeira" in saida
    assert "acao  : apagar" in saida and saida.count("acao  : apagar") == 1


def test_rodar_dry_run_nao_altera_nada(amb, caplog):
    pdf = amb.arquivo("a.pdf", 10)
    antigo = amb.arquivo("velho.txt", 10)
    config = regras_padrao(amb)

    codigo = amb.cli("rodar", "--config", str(config), "--dry-run")

    assert codigo == 0
    assert pdf.exists() and antigo.exists()
    assert not amb.destino.exists()
    assert "SIMULAÇÃO" in caplog.text
    assert "seriam aplicados" in caplog.text


def test_rodar_relatorio_soma_as_regras(amb, caplog):
    amb.arquivo("a.pdf", 10, conteudo="12345")
    amb.arquivo("velho.txt", 10, conteudo="123")
    config = regras_padrao(amb)

    amb.cli("rodar", "--config", str(config))

    assert "Concluído: 2 itens aplicados (8.0 B), 0 erros." in caplog.text


def test_rodar_ignora_a_regra_invalida_e_executa_as_outras(amb, caplog):
    antigo = amb.arquivo("velho.txt", 10)
    config = amb.yml("""
        regras:
          - nome: Com erro de digitacao
            pasta: "{downloads}"
            condicoes: {idade_dia: 7}
            acao: apagar
          - nome: Boa
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
    """)

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 1  # algo foi ignorado: o resultado não é "tudo certo"
    assert not antigo.exists()  # mas a regra boa rodou
    assert "Regra ignorada: regra 1 ('Com erro de digitacao')" in caplog.text
    assert "idade_dia" in caplog.text


def test_rodar_regra_numa_pasta_que_nao_existe_nao_impede_as_outras(amb, caplog):
    antigo = amb.arquivo("velho.txt", 10)
    config = amb.yml(f"""
        regras:
          - nome: Pasta sumiu
            pasta: '{amb.tmp / "nao_existe"}'
            condicoes: {{idade_dias: 7}}
            acao: apagar
          - nome: Boa
            pasta: "{{downloads}}"
            condicoes: {{idade_dias: 7}}
            acao: apagar
    """)

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 1
    assert not antigo.exists()
    assert "Regra 'Pasta sumiu': não foi possível ler a pasta" in caplog.text


def test_rodar_erro_ao_apagar_um_item_resulta_em_codigo_um(amb, monkeypatch):
    amb.arquivo("velho.txt", 10)
    config = amb.yml("""
        regras:
          - nome: Limpar
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
    """)

    def falhar(caminho):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr("zelador.regras.deletar_item", falhar)

    assert amb.cli("rodar", "--config", str(config)) == 1


def test_rodar_recusa_pasta_protegida_sem_nem_listar_a_pasta(amb, monkeypatch, caplog):
    """Segurança: mesmo com --dry-run (para nada ser apagado se a proteção falhar), uma regra
    na raiz do disco tem que ser recusada ANTES de ser executada."""
    raiz = Path(os.path.realpath(amb.tmp)).anchor
    executadas = []
    original = comando_regras.executar_regra

    def registrar_e_executar(regra, dry_run, **resto):
        executadas.append(regra.nome)
        return original(regra, dry_run, **resto)

    monkeypatch.setattr(comando_regras, "executar_regra", registrar_e_executar)
    config = amb.yml(f"""
        regras:
          - nome: Perigosa
            pasta: '{raiz}'
            condicoes: {{idade_dias: 1}}
            acao: apagar
          - nome: Segura
            pasta: "{{downloads}}"
            condicoes: {{idade_dias: 1}}
            acao: apagar
    """)

    codigo = amb.cli("rodar", "--config", str(config), "--dry-run")

    assert codigo == 1
    assert executadas == ["Segura"]
    assert "regra 1 ('Perigosa')" in caplog.text
    assert "raiz de um disco" in caplog.text


def test_rodar_recusa_mover_para_pasta_do_sistema(amb, monkeypatch, caplog):
    sistema = amb.tmp / "Windows"
    sistema.mkdir()
    monkeypatch.setenv("SystemRoot", str(sistema))
    amb.arquivo("a.pdf", 10)
    config = amb.yml(f"""
        regras:
          - nome: Mover para o sistema
            pasta: "{{downloads}}"
            condicoes: {{idade_dias: 1}}
            acao: {{mover: '{sistema / "System32"}'}}
    """)

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 1
    assert (amb.downloads / "a.pdf").exists()
    assert not (sistema / "System32").exists()
    assert "pasta do sistema" in caplog.text


def test_rodar_arquivo_inexistente_retorna_dois(amb, caplog):
    codigo = amb.cli("rodar", "--config", str(amb.tmp / "nao_existe.yml"))

    assert codigo == 2
    assert "arquivo não encontrado" in caplog.text


def test_rodar_yaml_invalido_retorna_dois_e_nao_executa_nada(amb, caplog):
    antigo = amb.arquivo("velho.txt", 10)
    config = amb.yml("regras:\n  - nome: x\n    pasta: [aberta\n")

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 2
    assert antigo.exists()
    assert "YAML inválido" in caplog.text


def test_rodar_sem_config_usa_o_arquivo_padrao(amb, monkeypatch):
    appdata = amb.tmp / "AppData"
    (appdata / "Zelador").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    antigo = amb.arquivo("velho.txt", 10)
    amb.yml("""
        regras:
          - nome: Limpar
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
    """, nome="AppData/Zelador/regras.yml")

    assert amb.cli("rodar") == 0
    assert not antigo.exists()


def test_rodar_sem_config_e_sem_arquivo_padrao_explica_o_que_fazer(amb, monkeypatch, caplog):
    monkeypatch.setenv("APPDATA", str(amb.tmp / "AppDataVazio"))

    codigo = amb.cli("rodar")

    assert codigo == 2
    assert "exemplos/regras.yml" in caplog.text
    assert "--config" in caplog.text


def test_rodar_arquivo_sem_regras_avisa_e_retorna_zero(amb, caplog):
    config = amb.yml("regras: []\n")

    codigo = amb.cli("rodar", "--config", str(config))

    assert codigo == 0
    assert "Nenhuma regra válida" in caplog.text


def test_arquivo_padrao_fica_na_pasta_zelador_do_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert caminhos.arquivo_de_regras_padrao() == tmp_path / "Zelador" / "regras.yml"


def test_arquivo_padrao_sem_appdata_usa_config_da_pasta_pessoal(tmp_path, monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert caminhos.arquivo_de_regras_padrao() == tmp_path / ".config" / "zelador" / "regras.yml"


# ---------------------------------------------------------------- listar


def test_listar_mostra_cada_regra_e_nao_altera_nada(amb, capsys):
    pdf = amb.arquivo("a.pdf", 10)
    antigo = amb.arquivo("velho.txt", 10)
    config = regras_padrao(amb)

    codigo = amb.cli("listar", "--config", str(config))
    saida = capsys.readouterr().out

    assert codigo == 0
    assert pdf.exists() and antigo.exists() and not amb.destino.exists()
    assert "2 regra(s) válida(s), 0 com problema." in saida
    assert "1. Organizar PDFs" in saida and "2. Limpar antigos" in saida
    assert f"pasta : {amb.downloads}" in saida
    assert "quando: extensão: pdf" in saida
    assert "quando: sem atividade há mais de 7 dia(s)" in saida
    assert f"acao  : mover para {amb.destino}" in saida
    assert "acao  : apagar" in saida


def test_listar_descreve_todas_as_condicoes(amb, capsys):
    config = amb.yml("""
        regras:
          - nome: Completa
            pasta: "{downloads}"
            condicoes:
              idade_dias: 3
              extensoes: [pdf, docx]
              tamanho_min: 1MB
              tamanho_max: 50MB
              nome: "^relat"
            acao: apagar
          - nome: So minimo
            pasta: "{downloads}"
            condicoes: {tamanho_min: 2KB}
            acao: apagar
          - nome: So maximo
            pasta: "{downloads}"
            condicoes: {tamanho_max: 3GB}
            acao: apagar
    """)

    amb.cli("listar", "--config", str(config))
    saida = capsys.readouterr().out

    assert "quando: extensão: pdf, docx" in saida
    assert "quando: tamanho entre 1.0 MB e 50.0 MB" in saida
    assert "quando: nome casa com /^relat/" in saida
    assert "quando: tamanho de pelo menos 2.0 KB" in saida
    assert "quando: tamanho de no máximo 3.0 GB" in saida


def test_listar_aponta_as_regras_com_problema_e_retorna_um(amb, capsys):
    config = amb.yml("""
        regras:
          - nome: Boa
            pasta: "{downloads}"
            condicoes: {idade_dias: 7}
            acao: apagar
          - nome: Ruim
            pasta: "{downloads}"
            condicoes: {idade_dias: 0}
            acao: apagar
    """)

    codigo = amb.cli("listar", "--config", str(config))
    saida = capsys.readouterr().out

    assert codigo == 1
    assert "1 regra(s) válida(s), 1 com problema." in saida
    assert "Regras com problema (serão ignoradas):" in saida
    assert "regra 2 ('Ruim')" in saida


def test_listar_arquivo_inexistente_retorna_dois(amb, capsys, caplog):
    codigo = amb.cli("listar", "--config", str(amb.tmp / "nao_existe.yml"))

    assert codigo == 2
    assert capsys.readouterr().out == ""
    assert "arquivo não encontrado" in caplog.text


def test_listar_recusa_pasta_protegida_e_diz_por_que(amb, capsys):
    raiz = Path(os.path.realpath(amb.tmp)).anchor
    config = amb.yml(f"""
        regras:
          - nome: Perigosa
            pasta: '{raiz}'
            condicoes: {{idade_dias: 1}}
            acao: apagar
    """)

    codigo = amb.cli("listar", "--config", str(config))
    saida = capsys.readouterr().out

    assert codigo == 1
    assert "0 regra(s) válida(s), 1 com problema." in saida
    assert "raiz de um disco" in saida


# --------------------------------------------------------------- exemplo


def test_exemplo_do_repositorio_e_valido(amb, monkeypatch, capsys):
    """Se o exemplo deixar de ser válido, a pipeline avisa."""
    monkeypatch.setattr(Path, "home", lambda: amb.tmp / "home")

    codigo = amb.cli("listar", "--config", str(RAIZ_DO_PROJETO / "exemplos" / "regras.yml"))
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "3 regra(s) válida(s), 0 com problema." in saida


def test_exemplo_roda_em_simulacao_sem_alterar_nada(amb, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: amb.tmp / "home")
    pdf = amb.arquivo("a.pdf", 10)
    instalador = amb.arquivo("setup.exe", 10)
    antigo = amb.arquivo("velho.txt", 40)

    codigo = amb.cli("rodar", "--config", str(RAIZ_DO_PROJETO / "exemplos" / "regras.yml"),
                     "--dry-run")

    assert codigo == 0
    assert pdf.exists() and instalador.exists() and antigo.exists()


# ------------------------------------------------------- opção --dry-run


def test_listar_nao_aceita_dry_run_porque_so_le(amb, capsys):
    config = regras_padrao(amb)

    with pytest.raises(SystemExit) as saida:
        amb.cli("listar", "--config", str(config), "--dry-run")

    assert saida.value.code == 2
    assert "--dry-run" in capsys.readouterr().err


@pytest.mark.parametrize("comando, tem_dry_run", [
    ("limpar", True), ("rodar", True), ("listar", False),
])
def test_ajuda_mostra_dry_run_so_nos_comandos_que_alteram_arquivos(comando, tem_dry_run, capsys):
    with pytest.raises(SystemExit):
        cli.main([comando, "--help"])

    assert ("--dry-run" in capsys.readouterr().out) is tem_dry_run
