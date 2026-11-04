# Zelador Files

![Pipeline](../../actions/workflows/pipeline.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

A rule-based file organizer and cleaner for Windows, written in Python. *Zelador* is Portuguese for
"caretaker" or "janitor". You describe what to look at, which items to pick and what to do with
them in a YAML file (move PDFs out of Downloads, send old installers to the Recycle Bin...), and
Zelador applies those **rules**, always with a dry run first. Everything it removes goes to the
Recycle Bin by default, everything it moves can be undone, and a set of protected folders keeps a
bad rule from touching the system. It was born from a real need: Downloads folders fill up and
nobody notices. Dependencies: PyYAML (rules file), psutil (disk reading) and send2trash
(Recycle Bin).

> **Language note:** the command names (`limpar`, `rodar`, `listar`...), the YAML keys
> (`regras`, `condicoes`, `idade_dias`...) and the program messages are in Brazilian Portuguese.
> This README documents them exactly as they are, translating only the explanations.

## Usage

The program is organized in **commands**: `limpar` (clean), `rodar` (run rules), `listar` (list
rules), `modelos` (templates), `iniciar` (start from templates), `agendar` (schedule),
`historico` (history) and `desfazer` (undo).

```bash
# Dry run: lists what would be sent to the Recycle Bin, changing nothing (always start here)
python -m zelador limpar --dry-run

# Sends to the Recycle Bin whatever has had no activity for more than 7 days (the default)
python -m zelador limpar

# Changes the limit and the folder
python -m zelador limpar --dias 14 --pasta "D:\Temporarios"

# Deletes for good, skipping the Recycle Bin (there is no way back)
python -m zelador limpar --permanente

# General help, and help for one command
python -m zelador --help
python -m zelador limpar --help
```

Calls with only options (`python -m zelador --dry-run`) still work and are equivalent to `limpar`,
so scheduled tasks created before the commands existed don't break. They inherit the current
default: what leaves Downloads goes to the **Recycle Bin** and is no longer deleted for good.
Calling the program with **nothing at all** (for example, by double-clicking the `.exe`) only
shows the help: it never cleans anything.

**Option shared by all commands**

| Option | Description | Default |
|---|---|---|
| `--log-dir PATH` | Folder where logs are written | `%LOCALAPPDATA%\Zelador\logs` |

**`limpar` command** (`rodar` and `desfazer` also accept `--dry-run`; read-only commands don't)

| Option | Description | Default |
|---|---|---|
| `--dry-run` | Only shows what would be sent to the Recycle Bin, changing nothing | off |
| `--dias N` | Days without activity for an item to count as old (minimum 1) | `7` |
| `--pasta PATH` | Folder to clean | the user's Downloads |
| `--permanente` | Deletes for good, skipping the Recycle Bin (no way back) | off |

**Exit codes:** `0` when everything went well, `1` if there was an error or the folder doesn't
exist, and `2` for invalid arguments or a protected folder. This makes the result usable in
schedulers and pipelines.

To install it as a command: `pip install .`, then `zelador limpar --dry-run`.

## Getting started with ready-made templates

You don't have to write the YAML from scratch. `iniciar` creates the rules file from ready-made
templates, with comments explaining each rule, for you to edit afterwards:

```bash
zelador modelos                                  # lists the available templates
zelador iniciar                                  # creates the file with the recommended templates
zelador iniciar --modelo organizar-pdfs --modelo organizar-imagens
zelador iniciar --imprimir                       # only prints it, writes nothing
```

| Template | What it does |
|---|---|
| `organizar-pdfs` (recommended) | moves PDFs from Downloads to `Documents/PDFs` |
| `organizar-imagens` | moves photos and images to `Pictures/Downloads` |
| `organizar-documentos` | moves Word, Excel and PowerPoint files to `Documents/Baixados` |
| `limpar-instaladores` (recommended) | sends large `.exe`/`.msi` files older than 3 days to the Recycle Bin |
| `limpar-downloads-parados` (recommended) | sends whatever has sat there for over 30 days to the Recycle Bin |

The file is written to `%APPDATA%\Zelador\regras.yml`, which is where `rodar` and `listar` look
when `--config` isn't given. Then: `zelador listar`, `zelador rodar --dry-run` and, once you're
happy, `zelador rodar`.

Built-in safeguards:

- **Never overwrites** an existing file. With `--forcar`, it first keeps a dated copy
  (`regras.yml.20261104-153012.bak`, never replacing an earlier copy).
- **Writes atomically**: if anything fails halfway, the original file stays intact.
- **No template deletes for good**: they only move or send to the Recycle Bin, both of which can
  be reversed. This is guaranteed by a test.
- **The order is decided by the program**: rules that organize run before rules that clean, so a
  generic cleanup never catches what was just organized (a 40-day-old PDF is moved, not
  discarded).
- Destinations use the **real** Windows folders (`{documentos}`, `{imagens}`), even on OneDrive.

## Rules in a YAML file

Each rule says *where* to look, *which items* to pick and *what to do*:

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
zelador listar --config regras.yml            # shows what each rule does and flags problems
zelador rodar --config regras.yml --dry-run   # simulates, changing nothing
zelador rodar --config regras.yml             # runs the rules, in file order
```

Without `--config`, Zelador looks for `regras.yml` in the `Zelador` folder inside `%APPDATA%`.
There is a commented template in [`exemplos/regras.yml`](exemplos/regras.yml).

**Conditions** (all of a rule's conditions must hold):

| Condition | Value | Example |
|---|---|---|
| `idade_dias` | integer, minimum 1: no activity for more than N days | `7` |
| `extensoes` | list of extensions, case-insensitive | `[pdf, docx]` |
| `tamanho_min`, `tamanho_max` | bytes, or text with a unit (KB, MB, GB, TB; base 1024) | `50MB` |
| `nome` | regular expression on the name, case-insensitive | `"^relat"` |

**Actions:** `lixeira` (recommended: can be restored), `apagar` (for good, no way back), or
`mover: <destination folder>`. Moving never overwrites: if the name already exists at the
destination, the new file becomes `name (1).ext`.

**Paths** must be absolute. Use `{downloads}`, `{documentos}` or `{imagens}` (the user's **real**
folders: they may live on OneDrive and have a different name, so `~/Documents` can point to the
wrong folder), `~` (home folder) or `${VARIABLE}` (environment variable).

**Exit codes of `rodar` and `listar`:** `0` all good, `1` some rule was ignored for being invalid
or there was an error while running, `2` the file doesn't exist or the YAML is invalid. `listar`
also works as a way to validate the file before scheduling.

A YAML gotcha to watch for: unquoted `no`, `yes`, `on` and `off` become booleans. Zelador warns
you when that happens in an extensions list; for the extension `no`, write `["no"]`.

## About the Recycle Bin

By default, whatever Zelador takes out of a folder goes to the **Windows Recycle Bin**, and can be
restored from Explorer. Three things worth knowing:

- **The Recycle Bin doesn't free space.** Items keep taking up disk space until you empty it. In a
  real test, sending a 300 MB file left the free disk space unchanged. That's why `limpar` ends
  with a notice (in Portuguese): "disk space only comes back when you empty the Recycle Bin".
- **Windows deletes for good** whatever is larger than the Recycle Bin limit, or sits on a drive
  with no Recycle Bin (USB stick, network drive). Zelador doesn't control that.
- **Shortcuts (junctions/symlinks) never go to the Recycle Bin:** only the shortcut itself is
  removed, and whatever it points to stays intact.

To delete for good, use `--permanente` (in `limpar`) or `acao: apagar` (in the YAML).

## History and undo

Every time `rodar` or `limpar` handles an item (for real, not in a dry run), Zelador writes down
what it did in `%LOCALAPPDATA%\Zelador\historico.jsonl`. You can inspect it and, for what was
**moved**, undo it:

```bash
zelador historico                    # the latest runs, newest first
zelador historico --lote ID          # item by item, what one run did
zelador desfazer --dry-run           # shows what would go back, changing nothing
zelador desfazer                     # puts back what the last run moved
zelador desfazer --lote ID           # ... or what a specific run moved
```

What can (and cannot) be undone:

| Action | Undo |
|------|----------|
| `mover` (move) | Yes: it goes back to its place **and name** (an `a (1).pdf` becomes `a.pdf` again). |
| Recycle Bin | Not through Zelador: restore it from Explorer. |
| `apagar` (delete) | No: it was deleted for good. |

Built-in safeguards: undo **never overwrites** (if the original place was taken in the meantime,
the file comes back as `name (1)`), goes from the last action to the first, and a problem with one
item doesn't stop the others. Since the history is a text file anyone can edit, nothing in it is
obeyed blindly: before moving, Zelador checks that the item is still where the history says, that
it is still the same kind (file or folder), and that neither it nor the place it returns to is in
a protected system folder.

The file is append-only (if the computer shuts down midway, at most the last line is left
incomplete and is ignored) and drops entries older than 180 days by itself once it passes 2 MB.
If the disk won't allow writing, the cleanup carries on and Zelador warns that this run can't be
undone. Use `--historico FILE` to use another file.

## Design decisions

- **Reversible by default:** what leaves goes to the Recycle Bin; deleting for good is an explicit
  choice (`--permanente` or `acao: apagar`). The tests never touch the real Recycle Bin: a fake one
  is applied automatically to all of them.
- **Safety first:** `--dry-run` shows exactly what would be done, and `--dias 0` or a negative
  value is rejected (it would delete everything). Running the program with no arguments never
  cleans anything.
- **Protected folders:** no rule can act on a drive root, on the whole home folder, on the folder
  that contains the users, or on system folders (Windows, Program Files). The check resolves
  shortcuts, so a shortcut to `C:\Windows` is refused too. Subfolders of the home folder, such as
  Downloads, are allowed. A rule that moves items into its own folder is refused as well.
- **The rules file is untrusted input:** it is read as "safe" YAML (it never runs code), repeated
  keys and unknown fields are errors (a typo in `idade_dias` would leave the rule broader than the
  author meant), and relative paths are refused. An invalid rule is skipped with a warning and the
  others carry on.
- **Doesn't follow shortcuts:** symlinks and Windows junctions are treated as the shortcut itself.
  Deleting a shortcut never deletes what it points to.
- **Moving across drives is safe:** on the same drive a move is just a rename. Across drives it
  copies and then deletes the source, and if the source can only be partly deleted (a file in use),
  the complete copy is kept and nothing is lost.
- **Real age of folders:** a folder's `mtime` doesn't change when a file inside a subfolder is
  modified. So a folder's age is the most recent activity of *everything* inside it.
- **Creation + modification date:** downloaded or extracted files often keep their original
  modification date. The program uses the later of creation and modification, which reflects when
  the file arrived on the machine.
- **Correct Downloads folder:** it is resolved through the Windows Known Folders API, so it works
  even when the folder was redirected (OneDrive, group policy).
- **Read-only files** are handled normally (including by the Recycle Bin).
- **Daily logs** (`<logs>/YYYY-MM-DD/log.txt`) with automatic rotation: logs older than 30 days
  are removed.

## Running on its own: scheduling

`agendar` creates a task in the **Windows Task Scheduler** that runs `zelador rodar` with your
rules file:

```bash
zelador agendar criar                                    # every day at 09:00
zelador agendar criar --hora 18:30
zelador agendar criar --frequencia semanal --dias seg,qua,sex --hora 08:00
zelador agendar criar --frequencia aoentrar              # 2 minutes after logging in to Windows
zelador agendar criar --simulacao                        # schedules only the dry run (--dry-run)
zelador agendar mostrar                                  # what is scheduled
zelador agendar remover
```

Other `criar` options: `--config` (the rules file; default: Zelador's default file), `--nome`
(the task name; default: `Zelador`) and `--log-dir`. Creating again with the same name
**updates** the task without duplicating it.

A safe way to start: `zelador iniciar`, then `zelador listar`, then `zelador rodar --dry-run`,
then `zelador agendar criar --simulacao` (the task only logs what it would do) and, once you're
happy, `zelador agendar criar`. To test the task right away, without waiting for the time:
`schtasks /Run /TN "Zelador"`. Logs are in `%LOCALAPPDATA%\Zelador\logs`.

What it checks and guarantees:

- **Validates the rules file before scheduling.** An invalid rule (a typo, for example) blocks the
  scheduling, instead of the task failing by itself at 9 a.m.
- **Refuses to schedule a rule that deletes for good** (`acao: apagar`) unless you ask: a cleanup
  with no Recycle Bin running on its own only works with `--permitir-apagar-de-vez` (or with
  `--simulacao`, which deletes nothing). Prefer `acao: lixeira`.
- **Runs as you, without administrator rights**, and only while you are logged in.
- **If the PC was off at the scheduled time**, it runs as soon as possible, and it never runs two
  executions at once (one stuck for over 1 hour is ended).
- **No flashing console window** when run from Python (it uses `pythonw.exe`). The packaged `.exe`
  is a console program, so a console window blinks briefly when the task runs.
- The task is defined in XML rather than through the `schtasks` command line, because the `/TR`
  option accepts at most 261 characters and a long path in AppData overflows it.

## Building the .exe

A single file, `zelador.exe`, that works without Python installed (use it in place of `zelador` in
all the commands above, including `agendar`, which then schedules the `.exe` itself):

```powershell
pip install -e . pyinstaller
powershell -File scripts\gerar-exe.ps1     # builds dist\zelador.exe and prints the SHA256
dist\zelador.exe --version
```

The icon (`assets/zelador.ico`) is an original drawing, generated by `scripts/gerar-icone.py`
(requires `pillow`). The `.exe` is not digitally signed, so Windows SmartScreen may show a warning
on first run ("More info" > "Run anyway").

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

The pipeline in [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml) runs on every
push and pull request: syntax check, static analysis (`ruff`), tests (`pytest`) and a dependency
vulnerability audit (`pip-audit`).

## Project structure

```
zelador/
  cli.py                entry point: commands and shared options
  limpeza.py            `limpar` command (one engine rule: remove what is old, via the Recycle Bin)
  comando_regras.py     `rodar` and `listar` commands
  comando_modelos.py    `modelos` and `iniciar` commands
  comando_historico.py  `historico` and `desfazer` commands
  historico.py          the history file (append-only) and its pruning
  desfazer.py           puts back what was moved, with the safety checks
  comando_agenda.py     `agendar` command (create, remove and show the Windows task)
  agenda.py             builds the Task Scheduler definition (XML), runs nothing
  modelos.py            the ready-made rule templates and the file generator
  discos.py             disk reading (psutil)
  painel/recomendacoes.py  explainable recommendations: full disk, idle Downloads, loose PDFs
  opcoes.py             shared command-line options (`--dry-run`, `--historico`)
  configuracao.py       reading and validation of the YAML rules file
  regras.py             rule engine: conditions (age, extension, size, name) and actions
  seguranca.py          folders no rule may act on
  arquivos.py           file operations: measure, delete, send to the Recycle Bin, move, restore
  caminhos.py           the user's real Downloads and where the rules and history files live
  logs.py               log setup and rotation
  __main__.py           allows `python -m zelador`
scripts/                gerar-exe.ps1 (PyInstaller build), gerar-icone.py, zelador_exe.py
assets/                 the program icon
exemplos/regras.yml     commented template of a rules file
tests/                  tests for every module (pytest)
.github/workflows/pipeline.yml
```

## License

[MIT](LICENSE)
