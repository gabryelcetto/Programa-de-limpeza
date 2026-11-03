# Zelador

![Pipeline](../../actions/workflows/pipeline.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Ferramenta de manutenção de arquivos para Windows, em Python. Hoje o comando `limpar` remove da
pasta de **Downloads** os arquivos e pastas sem atividade há mais de N dias. Ele nasceu de uma
necessidade real do trabalho, onde a pasta de Downloads enchia sem ninguém perceber, e roda sobre
um motor de regras (condições + ações) que é a base dos próximos recursos. Não tem dependências
externas (apenas a biblioteca padrão).

## Uso

O programa é organizado em **comandos**. Por enquanto existe o `limpar`, e a ideia é que outros
sejam adicionados.

```bash
# Simulação: lista o que seria apagado, sem apagar nada (comece sempre por aqui)
python -m zelador limpar --dry-run

# Apaga o que está há mais de 7 dias (padrão) sem atividade
python -m zelador limpar

# Muda o limite e a pasta
python -m zelador limpar --dias 14 --pasta "D:\Temporarios"

# Ajuda geral e de um comando
python -m zelador --help
python -m zelador limpar --help
```

Chamadas sem comando (`python -m zelador --dry-run`, ou sem nenhum argumento) continuam
funcionando e equivalem a `limpar`, para não quebrar tarefas agendadas criadas antes dos comandos.

**Opções comuns a todos os comandos**

| Opção | Descrição | Padrão |
|---|---|---|
| `--dry-run` | Apenas mostra o que seria feito, sem alterar nada | desligado |
| `--log-dir CAMINHO` | Pasta onde os logs são gravados | `%LOCALAPPDATA%\Zelador\logs` |

**Comando `limpar`**

| Opção | Descrição | Padrão |
|---|---|---|
| `--dias N` | Dias sem atividade para considerar o item antigo (mínimo 1) | `7` |
| `--pasta CAMINHO` | Pasta a limpar | Downloads do usuário |

**Código de saída:** `0` quando tudo ocorreu bem, `1` se houve algum erro ou a pasta não existe,
e `2` para argumentos inválidos. Isso permite usar o resultado em agendadores e pipelines.

Para instalar como comando: `pip install .` e depois `zelador limpar --dry-run`.

## Decisões técnicas

- **Segurança primeiro:** o modo `--dry-run` mostra exatamente o que seria apagado, e
  `--dias 0` ou negativo é rejeitado (apagaria tudo).
- **Não segue atalhos:** symlinks e junctions do Windows são tratados como o próprio atalho.
  Apagar um atalho nunca apaga o conteúdo para onde ele aponta.
- **Idade real de pastas:** o `mtime` de uma pasta não muda quando um arquivo dentro de uma
  subpasta é alterado. Por isso a idade de uma pasta é a atividade mais recente de *tudo* que
  há dentro dela.
- **Data de criação + modificação:** arquivos baixados ou extraídos costumam manter a data de
  modificação original. O programa usa a maior entre criação e modificação, que reflete quando
  o arquivo chegou na máquina.
- **Pasta de Downloads correta:** é resolvida pela API de Known Folders do Windows, então
  funciona mesmo quando a pasta foi redirecionada (OneDrive, política de grupo).
- **Arquivos somente leitura** são tratados e apagados normalmente.
- **Logs por dia** (`<logs>/AAAA-MM-DD/log.txt`) com rotação automática: logs com mais de
  30 dias são removidos.

## Agendando no Windows

Com o pacote instalado (`pip install .`), o comando `zelador` funciona de qualquer
pasta. Para rodar todo dia às 09:00 pelo Agendador de Tarefas:

```powershell
schtasks /Create /SC DAILY /ST 09:00 /TN "Zelador" /TR "zelador limpar" /F
```

Teste antes com `zelador limpar --dry-run` e confira o log gerado.

## Desenvolvimento

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

A pipeline em [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml) roda, a cada
push e pull request: verificação de sintaxe, análise estática (`ruff`), testes (`pytest`) e
auditoria de vulnerabilidades das dependências (`pip-audit`).

## Estrutura

```
zelador/
  cli.py          comandos e opções comuns (ponto de entrada)
  limpeza.py      comando `limpar` (uma regra do motor: apagar o que é antigo)
  regras.py       motor de regras: condições (idade, extensão, tamanho, nome) e ações (apagar, mover)
  arquivos.py     operações sobre arquivos: medir, apagar, mover, formatar tamanho
  logs.py         configuração e rotação dos logs
  __main__.py     permite `python -m zelador`
tests/
  test_cli.py     testes da linha de comando
  test_limpeza.py testes do comando `limpar`
  test_regras.py  testes do motor de regras
  test_arquivos.py testes de mover/numerar arquivos
.github/workflows/pipeline.yml
```

## Licença

[MIT](LICENSE)
