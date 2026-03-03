# Lab Wizard

<img src="icon.png" alt="Lab Wizard icon" width="72" />

Lab Wizard is an experiment setup toolkit for SNSPD measurement workflows.
It combines:
- typed Python instrument models (including parent/child and channel-based instruments),
- a YAML-backed instrument configuration tree under `lab_wizard/config/instruments`,
- and a GUI workflow for generating runnable measurement project folders from templates.

After installing the library and setting up your Python environment, start the GUI from a terminal:

`wizard`

The wizard guides the normal lab workflow:
1. Add (initialize) instruments into the config tree.
2. Edit instrument parameters so they match your local hardware setup.
3. Create a new measurement by selecting a template and assigning compatible instrument resources.

Each created measurement gets its own timestamped project folder in `projects/`, including:
- a YAML file with the selected subset of instrument configuration,
- a generated `*_setup.py` file that initializes and wires resources for the measurement template.

The goal is to make experiment setup repeatable and explicit while keeping configuration and generated code easy to inspect and modify.
