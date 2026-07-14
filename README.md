# Lab Wizard

<img src="icon.png" alt="Lab Wizard icon" width="72" />

Lab Wizard is an experiment setup toolkit for SNSPD measurement workflows.
It combines:

- typed Python instrument models (including parent/child and channel-based instruments),
- a user-owned YAML configuration tree in a Lab Wizard workspace,
- and a GUI workflow for generating runnable measurement project folders from templates.

## Setup

Run the setup script from the root of the repository:

```bash
bash setup.sh
```

This will:

1. Install [uv](https://docs.astral.sh/uv/) (the Python package manager), if not already present.
2. Create a `.venv` and install all Python dependencies via `uv sync`.
3. Install [Bun](https://bun.sh/) (the JavaScript runtime), if not already present.
4. Install frontend dependencies and build the static frontend assets (used to display the wizard GUI).

After setup, initialize the repository root as a local workspace and start the
GUI:

```bash
wizard init .
wizard
```

(Use `uv run wizard init .` and `uv run wizard` if the virtual environment
is not activated.)

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

For development, `wizard clean` removes the initialized workspace state after
confirmation, returning the checkout to its fresh-clone layout. Use
`wizard clean --yes` for non-interactive cleanup.

Each created measurement gets its own timestamped project folder in `projects/`, including:

- a YAML file with the selected subset of instrument configuration,
- a generated `*_setup.py` file that initializes and wires resources for the measurement template.

The goal is to make experiment setup repeatable and explicit while keeping configuration and generated code easy to inspect and modify.
