---
icon: lucide/play
---

# Getting started

## Editable repository install

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
editable. Initialize the repository root after setup so instrument-code edits
and the local workspace share the same checkout:

```bash
uv run wizard init .
uv run wizard
```

The generated workspace files are ignored by this repository and are not part
of a fresh clone.

To return a development checkout to that fresh-clone state, run `wizard clean`.
The command lists `config/`, `projects/`, `logs/`, and `lab-wizard.toml` and asks
for confirmation before deleting them. `wizard clean --yes` skips the prompt.
It never deletes `data/`, which holds the recorded measurements.

## PyPI install

Create a directory for this computer's Lab Wizard state, install the package,
and initialize the workspace:

```bash
mkdir my-lab
cd my-lab
python -m venv .venv
source .venv/bin/activate
python -m pip install lab-wizard
wizard init .
```

`wizard init` creates `lab-wizard.toml`, `config/`, `projects/`, `logs/`, and `data/`.
These are user-owned runtime state; they are never stored in `site-packages`.

## Launch the GUI

After setup:

```bash
wizard
```

(or `uv run wizard` in the repository if the virtualenv is not activated)

`wizard` is a console command ([`lab_wizard/wizard/cli.py`](../lab_wizard/wizard/cli.py))
that launches the FastAPI backend ([`lab_wizard/wizard/backend/main.py`](../lab_wizard/wizard/backend/main.py))
on port `8884` and opens a desktop window via `pywebview`.

Useful flags (passed through `cli.py`):

| Flag | Effect |
|---|---|
| `--no-ui` | Run headless; print reachable URLs instead of opening a window. Useful over SSH. |
| `--port N` | Bind a different port (default `8884`). |
| `--debug` | Enable debug logging. |
| `--workspace PATH` | Use a specific workspace instead of searching the current directory and parents. |

On a headless/SSH host the backend prints all reachable `http://host:8884/`
URLs and an `ssh -L` tunnel hint, so you can drive the GUI from a browser on your
laptop.

## A first measurement, with no hardware

The fastest way to see the whole loop is to run it against the
[simulated bench](concepts/simulated-instruments.md) — real drivers, real
generated code, a simulated detector at the end of the wire. Nothing needs to be
plugged in.

1. Start the bench. It runs in the background until `wizard sim stop`:

    ```bash
    uv run wizard sim
    ```

    It prints the address of each simulated instrument.
2. **Instruments** → add a `prologix_gpib` controller whose port is the path
   `wizard sim` printed, a `sim900` mainframe under it at GPIB address 5, and a
   `sim928` source in slot 1 and a `sim970` voltmeter in slot 2. See
   [Instruments](wizard/instruments.md).
3. **Measurements → Create** → pick `iv_curve`, bind each role to one of those
   instruments, and generate the project.
4. Run it:

    ```bash
    cd projects/<your_project_folder>
    uv run iv_curve_setup.py
    ```

You should get a curve that is flat until the detector switches and rises after
— because the simulation solves the detector's actual IV relation, not a
lookup table.

Then try `mcr_curve`. Add the bench's `keysight53220A` counter and its
`yokogawa_aq2212` with the `yoko_attenuator` module first; the curve it
measures is the count rate falling as the attenuator dims the light. Both are
[procedures](wizard/procedures.md), so you can open either in the composer and
see the step tree it ran.

## The sections, briefly

Everything in the GUI lives under seven sections. What each is for:

| Section | Use it to | Page |
|---|---|---|
| **Overview** | see what this workstation is doing: is a server running, who owns the hardware | — |
| **Measurements** | pick something to run, bind instruments to it, generate a project; then set it up, run it and watch it live | [Measurements](wizard/measurements.md) |
| **Procedures** | write a measurement as roles, parameters and a step tree instead of Python | [Procedures](wizard/procedures.md) |
| **Instruments** | configure the hardware this workspace drives; build standalone resource files | [Instruments](wizard/instruments.md) |
| **Servers** | share this machine's instruments, set safety rules, see who holds what, reach other machines | [Servers](wizard/servers.md) |
| **Data** | browse and plot what runs recorded | [Data](wizard/data.md) |
| **Settings** | set how runs are saved as files, for every project; see where the workspace keeps things | [Saving files](wizard/data.md#saving-files) |

A first session with real hardware is steps 1 and 2 above with your own
instruments: add them, match their ports, slots and GPIB addresses to the
bench, then create a measurement against them.

## What a generated project contains

A timestamped folder under `projects/`:

| File | Is |
|---|---|
| `<project>.yaml` | the measurement's parameters, what its runs produce (`outputs:`), and **which instruments by name** — not a copy of their settings |
| `<measurement>_setup.py` | the generated file you run: it resolves those names against the config tree, claims what it needs, and hands the run its resources |
| `<measurement>.py` | the procedure itself — editable Python |

Instrument settings deliberately stay in `config/instruments`, so readdressing a
rack or fixing a bench setting reaches every project without regenerating
anything. A project that must be self-contained — to run on a machine with no
workspace — is generated in the
[embedded style](wizard/measurements.md#generation-styles) instead.

## Repository and generated workspace layout

A fresh clone contains the source directories. Running `wizard init .` adds the
ignored workspace entries shown below:

```text
lab_wizard_repo/
├── lab-wizard.toml           # generated; workspace paths + schema version
├── config/                   # generated; user-owned YAML state
├── projects/                 # generated measurement projects
├── logs/                     # generated runtime logs
├── data/                     # generated; lab.db, where every run is recorded
├── measurements/             # generated; this lab's custom measurements, with two examples
├── lab_wizard/
│   ├── lib/                 # the instrument library (importable, no GUI)
│   │   ├── instruments/     #   instrument models (general/ + per-vendor dirs)
│   │   ├── procedures/      #   procedure definitions, step schemas, codegen
│   │   ├── task_adapters/   #   steps, run lifecycle, the run entry point, sinks
│   │   ├── data/            #   the lab database: schema, recorder, facets
│   │   ├── savers/          #   the file saver: runs as folders of files
│   │   ├── plotters/        #   live plots (placeholders — see Roadmap)
│   │   ├── server/          #   remote-control server (ZMQ + JSON-RPC)
│   │   ├── client/          #   remote-control client (RemoteResources + proxies)
│   │   └── utilities/       #   config I/O, discovery, model tree
│   ├── wizard/
│   │   ├── backend/         #   FastAPI app + project generation
│   │   └── frontend/        #   SvelteKit GUI
└── docs/                    # this documentation
```
