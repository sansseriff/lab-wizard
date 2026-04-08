# Lab Wizard

<img src="icon.png" alt="Lab Wizard icon" width="72" />

Lab Wizard is an experiment setup toolkit for SNSPD measurement workflows.
It combines:

- typed Python instrument models (including parent/child and channel-based instruments),
- a YAML-backed instrument configuration tree under `lab_wizard/config/instruments`,
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

After setup, start the GUI from a terminal:

`wizard`

(or `uv run wizard` if the virtual environment is not activated)

The wizard guides the normal lab workflow:

1. Add (initialize) instruments into the config tree.
2. Edit instrument parameters so they match your local hardware setup.
3. Create a new measurement by selecting a template and assigning compatible instrument resources.

Each created measurement gets its own timestamped project folder in `projects/`, including:

- a YAML file with the selected subset of instrument configuration,
- a generated `*_setup.py` file that initializes and wires resources for the measurement template.

The goal is to make experiment setup repeatable and explicit while keeping configuration and generated code easy to inspect and modify.
