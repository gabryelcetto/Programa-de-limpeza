import http.client
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from zelador import agenda, arquivos, discos
from zelador.painel import servidor
from zelador.regras import IdadeMinima, Mover, Regra, executar_regra

DIA = 24 * 3600


@pytest.fixture(autouse=True)
def so_data_de_modificacao(monkeypatch):
    """No Windows o os.utime não muda a data de criação: as idades usam só o mtime."""
    monkeypatch.setattr(arquivos, "_data_referencia", lambda info: info.st_mtime)


@pytest.fixture(autouse=True)
def discos_fixos(monkeypatch):
    monkeypatch.setattr(discos, "coletar_discos", lambda: (
        discos.UsoDisco("C:\\", "C:\\", "NTFS", 100 * 1024**3, 50 * 1024**3, 50.0),))


def criar(pasta: Path, nome: str, dias: float, conteudo: str = "x") -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome
    caminho.write_text(conteudo, encoding="utf-8")
    t = time.time() - dias * DIA
    os.utime(caminho, (t, t))
    return caminho


def sem_agenda(agora):
    return {"existe": False, "frequencia": "diaria", "hora": "09:00", "dias": [],
            "simulacao": False}


@pytest.fixture
def cfg(tmp_path):
    return servidor.Configuracao(regras=tmp_path / "regras.yml",
                                 historico=tmp_path / "historico.jsonl",
                                 downloads=tmp_path / "Downloads",
                                 pdfs=tmp_path / "Docs" / "PDFs")


# ------------------------------------------------------------------ motor


def test_observar_recebe_os_itens_tambem_na_simulacao(tmp_path):
    criar(tmp_path / "d", "velho.txt", 10)
    criar(tmp_path / "d", "novo.txt", 0)
    vistos = []
    resumo = executar_regra(Regra("r", tmp_path / "d", [IdadeMinima(5)],
                                  Mover(tmp_path / "destino")),
                            dry_run=True, observar=vistos.append)
    assert [i.nome for i in vistos] == ["velho.txt"]
    assert resumo.aplicados == 1
    assert (tmp_path / "d" / "velho.txt").exists()  # simulação: nada saiu do lugar


# ------------------------------------------------------------------ dados


def test_coletar_dados_sem_regras_e_sem_historico(cfg):
    criar(cfg.downloads, "antigo.zip", 40, "z" * 100)
    criar(cfg.downloads, "boleto.pdf", 3)
    dados = servidor.coletar_dados(cfg, ler_agenda=sem_agenda)

    assert dados["regras"] == [] and dados["historico"] == []
    assert dados["ultima_execucao"] is None
    assert dados["discos"][0]["ponto_de_montagem"] == "C:\\"
    parados = dados["simulacoes"]["limpar_downloads"]
    assert [i["nome"] for i in parados] == ["antigo.zip"]
    assert parados[0]["acao"] == "lixeira" and parados[0]["idade_dias"] == 40
    assert [i["nome"] for i in dados["simulacoes"]["organizar_pdfs"]] == ["boleto.pdf"]
    json.dumps(dados)  # tudo precisa virar JSON


def test_coletar_dados_com_regras_validas_e_com_problema(cfg):
    criar(cfg.downloads, "velho.txt", 10, "abc")
    cfg.regras.write_text(
        "versao: 1\nregras:\n"
        f"  - nome: Velhos\n    pasta: '{cfg.downloads}'\n    condicoes:\n      idade_dias: 5\n"
        "    acao: lixeira\n"
        f"  - nome: Quebrada\n    pasta: '{cfg.downloads}'\n    acao: lixeira\n",
        encoding="utf-8")
    dados = servidor.coletar_dados(cfg, ler_agenda=sem_agenda)

    assert len(dados["regras"]) == 1
    regra = dados["regras"][0]
    assert regra["nome"] == "Velhos" and regra["acao"] == "lixeira"
    assert regra["simulacao"] == {"itens": 1, "bytes": 3}
    assert dados["simulacoes"]["regras"][0]["regra"] == "1 · Velhos"
    assert dados["recomendacoes"][0]["id"] == "regras-com-problema"


def test_downloads_inexistente_nao_quebra(cfg):
    dados = servidor.coletar_dados(cfg, ler_agenda=sem_agenda)
    assert dados["simulacoes"]["limpar_downloads"] == []


# ------------------------------------------------------------------ agenda


@pytest.mark.parametrize("frequencia, dias, simulacao", [
    ("diaria", (), False), ("semanal", ("seg", "sex"), True), ("aoentrar", (), False)])
def test_ler_tarefa_xml_le_o_que_o_zelador_gera(frequencia, dias, simulacao):
    argumentos = "-m zelador rodar --config \"C:\\r.yml\"" + (" --dry-run" if simulacao else "")
    xml = agenda.gerar_xml(frequencia, "07:30", dias, "python.exe", argumentos, "d",
                           agora=datetime(2026, 1, 5))
    tarefa = servidor.ler_tarefa_xml(xml)
    assert tarefa["frequencia"] == frequencia
    assert tarefa["dias"] == list(dias)
    assert tarefa["simulacao"] is simulacao
    if frequencia != "aoentrar":
        assert tarefa["hora"] == "07:30"


def test_proxima_execucao():
    quinta = datetime(2026, 11, 5, 10, 0)  # uma quinta-feira, às 10h
    assert servidor.proxima_execucao("diaria", "09:00", [], quinta) == "2026-11-06T09:00:00"
    assert servidor.proxima_execucao("diaria", "11:00", [], quinta) == "2026-11-05T11:00:00"
    assert servidor.proxima_execucao("semanal", "09:00", ["seg"], quinta) == "2026-11-09T09:00:00"
    assert servidor.proxima_execucao("aoentrar", "09:00", [], quinta) is None


# ------------------------------------------------------------------ página


def test_json_para_script_nao_fecha_a_tag():
    texto = servidor._json_para_script({"nome": "</script><script>alert(1)</script>"})
    assert "</script>" not in texto and "<" not in texto
    assert json.loads(texto) == {"nome": "</script><script>alert(1)</script>"}


def test_montar_pagina_injeta_dados_antes_do_script_da_tela(cfg):
    html = servidor.montar_pagina({"versao": "9"}, "segredo")
    assert html.index("window.ZELADOR_DADOS") < html.index('"use strict"')
    assert '"segredo"' in html


# ------------------------------------------------------------------ servidor


@pytest.fixture
def rodando(cfg):
    srv = servidor.criar_servidor(cfg, ler_agenda=sem_agenda)
    fio = threading.Thread(target=srv.serve_forever, daemon=True)
    fio.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def pedir(srv, metodo, caminho, corpo=None, token=True, host=None):
    porta = srv.server_address[1]
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    cabecalhos = {"Host": host or f"127.0.0.1:{porta}"}
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    if token:
        cabecalhos[servidor.CABECALHO_TOKEN] = srv.token
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    conteudo = resposta.read()
    conexao.close()
    return resposta.status, conteudo


def test_get_entrega_a_pagina_com_dados(rodando, cfg):
    criar(cfg.downloads, "antigo.zip", 40)
    status, corpo = pedir(rodando, "GET", "/")
    assert status == 200
    assert b"window.ZELADOR_DADOS" in corpo and b"antigo.zip" in corpo


def test_host_estranho_e_recusado(rodando):
    assert pedir(rodando, "GET", "/", host="malicioso.example:80")[0] == 403


def test_acao_sem_token_e_recusada(rodando, cfg, lixeira_falsa):
    criar(cfg.downloads, "antigo.zip", 40)
    status, _ = pedir(rodando, "POST", "/api/acao",
                      {"acao": "limpar_downloads", "parametros": {"dias": 30}}, token=False)
    assert status == 403
    assert (cfg.downloads / "antigo.zip").exists()
    assert lixeira_falsa.enviados == []


def test_limpar_downloads_manda_para_a_lixeira_e_grava_historico(rodando, cfg, lixeira_falsa):
    criar(cfg.downloads, "antigo.zip", 40)
    criar(cfg.downloads, "novo.zip", 1)
    status, corpo = pedir(rodando, "POST", "/api/acao",
                          {"acao": "limpar_downloads", "parametros": {"dias": 30}})
    resposta = json.loads(corpo)
    assert status == 200 and resposta["ok"], resposta
    assert [p.name for p in lixeira_falsa.enviados] == ["antigo.zip"]
    assert (cfg.downloads / "novo.zip").exists()
    assert resposta["dados"]["historico"][0]["lixeira"] == 1


def test_organizar_pdfs_ignora_destino_da_pagina_e_desfaz(rodando, cfg, tmp_path):
    criar(cfg.downloads, "a.pdf", 3)
    _, corpo = pedir(rodando, "POST", "/api/acao", {
        "acao": "organizar_pdfs", "parametros": {"destino": str(tmp_path / "outro")}})
    resposta = json.loads(corpo)
    assert resposta["ok"], resposta
    assert (cfg.pdfs / "a.pdf").exists() and not (tmp_path / "outro").exists()

    lote = resposta["dados"]["historico"][0]
    assert lote["movidos"] == 1 and not lote["desfeito"]
    _, corpo = pedir(rodando, "POST", "/api/acao",
                     {"acao": "desfazer", "parametros": {"lote": lote["id"]}})
    resposta = json.loads(corpo)
    assert resposta["ok"], resposta
    assert (cfg.downloads / "a.pdf").exists()
    assert resposta["dados"]["historico"][0]["desfeito"]


def test_rodar_regras_sem_arquivo_explica_o_problema(rodando):
    _, corpo = pedir(rodando, "POST", "/api/acao", {"acao": "rodar_regras", "parametros": {}})
    resposta = json.loads(corpo)
    assert not resposta["ok"] and "regras" in resposta["mensagem"].lower()


@pytest.mark.parametrize("corpo", [{"acao": "formatar_disco"}, {"acao": "desfazer",
                                                                 "parametros": {"lote": 3}}])
def test_acao_invalida_nao_faz_nada(rodando, corpo):
    _, resposta = pedir(rodando, "POST", "/api/acao", corpo)
    assert json.loads(resposta)["ok"] is False


def test_pedido_mal_formado(rodando):
    assert pedir(rodando, "POST", "/api/acao", ["nao", "e", "objeto"])[0] == 400
