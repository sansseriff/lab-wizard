from pathlib import Path

from pydantic_yaml import parse_yaml_file_as

from lab_wizard.lib.utilities.model_tree import Exp
from lab_wizard.lib.utilities.config_io import load_merge_save_instruments


def main() -> None:
    this_file = Path(__file__).resolve()
    project_yaml = this_file.with_suffix(".yaml")

    # Repo root is two levels up: <repo_root>/projects/<project_name>/
    repo_root = this_file.parents[2]
    config_dir = repo_root / "lab_wizard" / "config"

    print(f"Loading project-specific setup from: {project_yaml}")
    exp = parse_yaml_file_as(Exp, project_yaml)

    # Push instruments subtree into the config/ instruments tree
    instruments = exp.instruments
    merged = load_merge_save_instruments(config_dir, instruments)
    print(f"Pushed {len(instruments)} instrument(s) into config; merged total: {len(merged)}")


if __name__ == "__main__":
    main()
