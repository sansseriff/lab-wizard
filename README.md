# Lab Wizard

<img src="icon.png" alt="Lab Wizard icon" width="72" />

Lab Wizard is an experiment setup toolkit for SNSPD measurement workflows.
It combines:

- typed Python instrument models (including parent/child and channel-based instruments),
- a user-owned YAML configuration tree in a Lab Wizard workspace,
- and a GUI workflow for generating runnable measurement project folders from templates.

## Install

Run this in a terminal, from the folder that should hold the `lab-wizard`
checkout (macOS or Linux; needs git):

```bash
curl -fsSL https://raw.githubusercontent.com/sansseriff/lab-wizard/master/install.sh | bash
```

or with wget:

```bash
wget -qO- https://raw.githubusercontent.com/sansseriff/lab-wizard/master/install.sh | bash
```

Only pipe a script into bash if you trust its source. To read it first:

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/sansseriff/lab-wizard/master/install.sh
less install.sh   # it clones the repo, then runs setup.sh from the checkout
bash install.sh
```

The installer clones the repository into `./lab-wizard` (or pulls the latest,
if run inside an existing checkout) and runs `setup.sh`, which:

1. Installs [uv](https://docs.astral.sh/uv/) (the Python package manager), if not already present.
2. Creates a `.venv` and installs all Python dependencies via `uv sync`.
3. Installs [Bun](https://bun.sh/) (the JavaScript runtime), if not already present.
4. Installs the frontend dependencies and builds the wizard GUI.
5. On macOS, builds `Lab Wizard.app` so the window gets its Dock icon.
6. Initializes the checkout as a Lab Wizard workspace (`wizard init .`).

Then start the GUI:

```bash
cd lab-wizard
uv run wizard
```

In a checkout you already have, `bash setup.sh` runs the same setup.

When installing `lab-wizard` from PyPI in a new directory, initialize that
directory first:

```bash
wizard init .
wizard
```

The wizard guides the normal lab workflow:

1. Add (initialize) instruments into the config tree.
2. Edit instrument parameters so they match your local hardware setup.
3. Create a new measurement by selecting a template and assigning compatible instrument resources.

For development, `wizard --build` rebuilds the GUI from the frontend sources
before starting, so a change to the frontend shows up in the window. `wizard clean` removes the initialized workspace state after
confirmation, returning the checkout to its fresh-clone layout. Use
`wizard clean --yes` for non-interactive cleanup.

Each created measurement gets its own timestamped project folder in `projects/`, including:

- a YAML file with the selected subset of instrument configuration,
- a generated `*_setup.py` file that initializes and wires resources for the measurement template.

The goal is to make experiment setup repeatable and explicit while keeping configuration and generated code easy to inspect and modify.
