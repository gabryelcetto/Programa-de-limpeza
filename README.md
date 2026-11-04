# Zelador

![Pipeline](../../actions/workflows/pipeline.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Ferramenta de manutenção de arquivos para Windows, em Python. O comando `limpar` remove da pasta
de **Downloads** os arquivos e pastas sem atividade há mais de N dias, e o comando `rodar`
executa **regras** (condições + ações, como mover PDFs ou apagar instaladores) descritas em um
arquivo YAML, sempre com simulação antes. Ele nasceu de uma necessidade real do trabalho, onde a
pasta de Downloads enchia sem ninguém perceber. As dependências são o PyYAML (arquivo de regras)
e o psutil (leitura dos discos).

## Uso

O programa é organizado em **comandos**: `limpar`, `rodar`, `listar`, `modelos` e `iniciar`.

```bash
# Simulação: lista o que seria enviado para a Lixeira, sem mexer em nada (comece sempre por aqui)
python -m zelador limpar --dry-run

# Envia para a Lixeira o que está há mais de 7 dias (padrão) sem atividade
python -m zelador limpar

# Muda o limite e a pasta
python -m zelador limpar --dias 14 --pasta "D:\Temporarios"

# Apaga de vez, sem passar pela Lixeira (não tem volta)
python -m zelador limpar --permanente

# Ajuda geral e de um comando
python -m zelador --help
python -m zelador limpar --help
```

Chamadas sem comando (`python -m zelador --dry-run`, ou sem nenhum argumento) continuam
funcionando e equivalem a `limpar`, para não quebrar tarefas agendadas criadas antes dos comandos.
Elas herdam o padrão atual: o que sai de Downloads vai para a **Lixeira**, e não é mais apagado
de vez.

**Opção comum a todos os comandos**

| Opção | Descrição | Padrão |
|---|---|---|
| `--log-dir CAMINHO` | Pasta onde os logs são gravados | `%LOCALAPPDATA%\Zelador\logs` |

**Comando `limpar`** (o `rodar` também aceita `--dry-run`; comandos que só leem não têm essa opção)

| Opção | Descrição | Padrão |
|---|---|---|
| `--dry-run` | Apenas mostra o que seria enviado para a Lixeira, sem alterar nada | desligado |
| `--dias N` | Dias sem atividade para considerar o item antigo (mínimo 1) | `7` |
| `--pasta CAMINHO` | Pasta a limpar | Downloads do usuário |
| `--permanente` | Apaga de vez, sem passar pela Lixeira (não tem volta) | desligado |

**Código de saída:** `0` quando tudo ocorreu bem, `1` se houve algum erro ou a pasta não existe,
e `2` para argumentos inválidos ou uma pasta protegida. Isso permite usar o resultado em
agendadores e pipelines.

Para instalar como comando: `pip install .` e depois `zelador limpar --dry-run`.

## Começando com modelos prontos

Você não precisa escrever o YAML do zero. O `iniciar` cria o arquivo de regras a partir de modelos
prontos, com comentários explicando cada regra, para você editar depois:

```bash
zelador modelos                                  # lista os modelos disponíveis
zelador iniciar                                  # cria o arquivo com os modelos recomendados
zelador iniciar --modelo organizar-pdfs --modelo organizar-imagens
zelador iniciar --imprimir                       # só mostra na tela, sem gravar nada
```

| Modelo | O que faz |
|---|---|
| `organizar-pdfs` (recomendado) | move os PDFs de Downloads para `Documentos/PDFs` |
| `organizar-imagens` | move fotos e imagens para `Imagens/Downloads` |
| `organizar-documentos` | move Word, Excel e PowerPoint para `Documentos/Baixados` |
| `limpar-instaladores` (recomendado) | envia para a Lixeira `.exe`/`.msi` grandes com mais de 3 dias |
| `limpar-downloads-parados` (recomendado) | envia para a Lixeira o que está parado há mais de 30 dias |

O arquivo é gravado em `%APPDATA%\Zelador\regras.yml`, que é onde o `rodar` e o `listar` procuram
sem precisar de `--config`. Depois: `zelador listar`, `zelador rodar --dry-run` e, quando estiver
satisfeito, `zelador rodar`.

Cuidados embutidos:

- **Nunca sobrescreve** um arquivo existente. Com `--forcar`, guarda antes uma cópia datada
  (`regras.yml.20261104-153012.bak`, nunca substituindo uma cópia anterior).
- **Grava de forma atômica**: se algo falhar no meio, o arquivo original continua intacto.
- **Nenhum modelo apaga de vez**: só movem ou mandam para a Lixeira, que têm volta. Isso é garantido
  por teste.
- **A ordem é decidida pelo programa**: quem organiza roda antes de quem limpa, para a limpeza
  genérica nunca pegar o que acabou de ser organizado (um PDF de 40 dias é movido, não descartado).
- Os destinos usam as pastas **reais** do Windows (`{documentos}`, `{imagens}`), mesmo no OneDrive.

## Regras em arquivo YAML

Cada regra diz *onde* olhar, *quais itens* escolher e *o que fazer*:

```yaml
versao: 1
regras:
  - nome: Organizar PDFs
    pasta: "{downloads}"
    condicoes:
      extensoes: [pdf]
      idade_dias: 1
    acao:
      mover: "{documentos}/PDFs"

  - nome: Limpar downloads parados
    pasta: "{downloads}"
    condicoes:
      idade_dias: 30
    acao: lixeira
```

```bash
zelador listar --config regras.yml            # mostra o que cada regra faz e aponta problemas
zelador rodar --config regras.yml --dry-run   # simula, sem alterar nada
zelador rodar --config regras.yml             # executa as regras, na ordem do arquivo
```

Sem `--config`, o Zelador procura `regras.yml` na pasta `Zelador` dentro de `%APPDATA%`. Há um
modelo comentado em [`exemplos/regras.yml`](exemplos/regras.yml).

**Condições** (todas as de uma regra precisam ser verdadeiras):

| Condição | Valor | Exemplo |
|---|---|---|
| `idade_dias` | inteiro, mínimo 1: sem atividade há mais de N dias | `7` |
| `extensoes` | lista de extensões, sem diferenciar maiúsculas | `[pdf, docx]` |
| `tamanho_min`, `tamanho_max` | bytes, ou texto com unidade (KB, MB, GB, TB; base 1024) | `50MB` |
| `nome` | expressão regular sobre o nome, sem diferenciar maiúsculas | `"^relat"` |

**Ações:** `lixeira` (recomendada: dá para restaurar), `apagar` (de vez, sem volta), ou
`mover: <pasta de destino>`. Mover nunca sobrescreve: se o nome já existe
no destino, o arquivo novo vira `nome (1).ext`.

**Caminhos** devem ser absolutos. Use `{downloads}`, `{documentos}` ou `{imagens}` (as pastas
**reais** do usuário: elas podem estar no OneDrive e ter outro nome, como "Documentos", e por isso
`~/Documents` pode apontar para outra pasta), `~` (pasta pessoal) ou `${VARIAVEL}` (variável de
ambiente).

**Códigos de saída de `rodar` e `listar`:** `0` tudo certo, `1` alguma regra foi ignorada por ser
inválida ou houve erro ao executar, `2` o arquivo não existe ou o YAML está inválido. O `listar`
serve também para validar o arquivo antes de agendar.

Cuidado com uma pegadinha do YAML: `no`, `yes`, `on` e `off` sem aspas viram falso/verdadeiro. O
Zelador avisa quando isso acontece numa lista de extensões; para a extensão `no`, escreva `["no"]`.

## Sobre a Lixeira

Por padrão, o que o Zelador tira de uma pasta vai para a **Lixeira do Windows**, e dá para
restaurar pelo Explorer. Três coisas que vale saber:

- **A Lixeira não libera espaço.** Os itens continuam ocupando disco até você esvaziá-la. Num teste
  real, enviar um arquivo de 300 MB deixou o espaço livre do disco igual. Por isso, ao terminar, o
  `limpar` avisa: "O espaço em disco só volta quando você esvaziar a Lixeira".
- **O Windows apaga de vez** o que for maior que o limite da Lixeira, ou estiver num disco que não
  tem Lixeira (pendrive, unidade de rede). Isso não é controlado pelo Zelador.
- **Atalhos (junction/symlink) nunca vão para a Lixeira:** só o atalho é removido, e o conteúdo
  para onde ele aponta fica intacto.

Para apagar de vez use `--permanente` (no `limpar`) ou `acao: apagar` (no YAML).

## Decisões técnicas

- **Reversível por padrão:** o que sai vai para a Lixeira; apagar de vez é uma escolha explícita
  (`--permanente` ou `acao: apagar`). Os testes nunca usam a Lixeira de verdade: uma Lixeira falsa é
  aplicada automaticamente a todos eles.
- **Segurança primeiro:** o modo `--dry-run` mostra exatamente o que seria feito, e
  `--dias 0` ou negativo é rejeitado (apagaria tudo).
- **Pastas protegidas:** nenhuma regra pode atuar na raiz de um disco, na pasta pessoal inteira,
  na pasta que contém os usuários nem em pastas do sistema (Windows, Arquivos de Programas). A
  checagem resolve atalhos, então um atalho para `C:\Windows` também é recusado. Subpastas da
  pasta pessoal, como Downloads, são permitidas.
- **O arquivo de regras é entrada não confiável:** é lido como YAML "seguro" (nunca executa
  código), chaves repetidas e campos desconhecidos são erros (um erro de digitação em
  `idade_dias` deixaria a regra mais ampla do que o autor quis), e caminhos relativos são
  recusados. Uma regra inválida é ignorada com um aviso e as demais continuam.
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
- **Arquivos somente leitura** são tratados normalmente (inclusive pela Lixeira).
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
  cli.py             comandos e opções comuns (ponto de entrada)
  limpeza.py         comando `limpar` (uma regra do motor: tirar o que é antigo, via Lixeira)
  comando_regras.py  comandos `rodar` e `listar`
  comando_modelos.py comandos `modelos` e `iniciar`
  modelos.py         os modelos prontos de regras e o gerador do arquivo
  discos.py          leitura dos discos (psutil)
  painel/recomendacoes.py  recomendações explicáveis: disco cheio, Downloads parados, PDFs soltos
  opcoes.py          opções de linha de comando compartilhadas (`--dry-run`)
  configuracao.py    leitura e validação do arquivo YAML de regras
  regras.py          motor de regras: condições (idade, extensão, tamanho, nome) e ações
  seguranca.py       pastas em que nenhuma regra pode atuar
  arquivos.py        operações sobre arquivos: medir, apagar, enviar à Lixeira, mover
  caminhos.py        Downloads real do usuário e local do arquivo de regras
  logs.py            configuração e rotação dos logs
  __main__.py        permite `python -m zelador`
exemplos/regras.yml  modelo comentado de arquivo de regras
tests/               testes de cada módulo (pytest)
.github/workflows/pipeline.yml
```

## Licença

[MIT](LICENSE)
