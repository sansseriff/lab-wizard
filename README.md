# Lab Wizard

<img src="icon.png" alt="Lab Wizard icon" width="72" />

Lab Wizard is an experiment setup toolkit for SNSPD measurement workflows.  
It combines typed instrument models, a YAML-backed instrument configuration tree, and a web wizard that creates timestamped measurement project folders with generated setup code.

The main flow is:
1. Configure available lab instruments in `lab_wizard/config/instruments`.
2. Use the wizard UI to choose a measurement type and compatible resources.
3. Generate a project folder containing a subset YAML and a measurement setup file.

This repository is designed for local lab automation and iterative experiment development, with strong typing around parent/child instrument relationships and channel-based resource selection.
