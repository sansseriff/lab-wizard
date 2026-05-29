---
icon: lucide/play
---

# Getting started

## Install

Run the setup script from the repository root:

```bash
bash setup.sh
```

This will:

1. Install [uv](https://docs.astral.sh/uv/) (the Python package manager) if not present.
2. Create a `.venv` and install all Python dependencies via `uv sync`.
3. Install [Bun](https://bun.sh/) (the JavaScript runtime) if not present.
4. Install frontend dependencies and build the static SvelteKit assets the GUI serves.

The project requires **Python ≥ 3.14**. The repo is a uv workspace: the root
package (`lab-wizard-repo`) depends on the `lab_wizard` member package, installed
editable.

## Launch the GUI

After setup:

```bash
wizard
```

(or `uv run wizard` if the virtualenv is not activated)

`wizard` is a console script ([`lab_wizard/wizard/cli.py`](../lab_wizard/wizard/cli.py))
that launches the FastAPI backend ([`lab_wizard/wizard/backend/main.py`](../lab_wizard/wizard/backend/main.py))
on port `8884` and opens a desktop window via `pywebview`.

Useful flags (passed through `cli.py`):

| Flag | Effect |
|---|---|
| `--no-ui` | Run headless; print reachable URLs instead of opening a window. Useful over SSH. |
| `--port N` | Bind a different port (default `8884`). |
| `--debug` | Enable debug logging. |
| `--projects PATH` | Point the projects root somewhere other than the current directory. |

On a headless/SSH host the backend prints all reachable `http://host:8884/`
URLs and an `ssh -L` tunnel hint, so you can drive the GUI from a browser on your
laptop.

## The workflow at a glance

The GUI home page ([`+page.svelte`](../lab_wizard/wizard/frontend/src/routes/+page.svelte))
groups tasks by the three roles a workstation plays:

1. **Build & run measurements** — pick a measurement, assign it compatible
   instruments/savers/plotters, and generate a project folder.
2. **This workstation (host)** — configure the instruments this machine drives,
   and (optionally) run a server exposing them with safety rules.
3. **Remote** — register other machines whose instruments your measurements can use.

A typical first session:

1. **Manage Instruments** → add the instruments physically attached to this
   machine and edit their parameters (ports, slots, GPIB addresses) to match the
   hardware. See [Managing instruments](wizard/managing-instruments.md).
2. **Create Measurement** → choose a measurement template (e.g. `iv_curve`),
   assign each required resource a configured instrument, saver, and plotter, and
   generate the project. See [Creating measurements](wizard/creating-measurements.md).
3. Run the generated project:
   ```bash
   cd projects/<your_project_folder>
   python <measurement>_setup.py
   ```

Each generated project is a timestamped folder under `projects/` containing:

- a `*.yaml` file with the selected subset of instrument/saver/plotter config, and
- a generated `*_setup.py` that initializes and wires those resources for the
  measurement.

## Repository layout

```text
lab_wizard_repo/
├── lab_wizard/
│   ├── lib/                 # the instrument library (importable, no GUI)
│   │   ├── instruments/     #   instrument models (general/ + per-vendor dirs)
│   │   ├── measurements/    #   measurement classes + setup templates
│   │   ├── savers/          #   data persistence (DatabaseSaver, schema)
│   │   ├── plotters/        #   plotting (scaffolding — see Roadmap)
│   │   ├── server/          #   remote-control server (ZMQ + JSON-RPC)
│   │   ├── client/          #   remote-control client (RemoteResources + proxies)
│   │   └── utilities/       #   config I/O, discovery, model tree
│   ├── wizard/
│   │   ├── backend/         #   FastAPI app + project generation
│   │   └── frontend/        #   SvelteKit GUI
│   └── config/              # the on-disk config tree (instruments/, savers/, …)
├── projects/                # generated measurement projects land here
└── docs/                    # this documentation
```
