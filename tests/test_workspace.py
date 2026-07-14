from pathlib import Path

import pytest

from lab_wizard.wizard import cli
from lab_wizard.wizard.workspace import (
    MANIFEST_NAME,
    WORKSPACE_ENV,
    clean_workspace,
    find_workspace,
    initialize_workspace,
    load_workspace,
    require_workspace,
)


def test_init_creates_empty_workspace_and_is_idempotent(tmp_path: Path) -> None:
    workspace, created = initialize_workspace(tmp_path)
    assert created is True
    assert workspace.manifest == tmp_path / MANIFEST_NAME
    assert workspace.projects_dir.is_dir()
    assert workspace.logs_dir.is_dir()
    assert {path.name for path in workspace.config_dir.iterdir()} == {
        "instruments",
        "measurements",
        "plotters",
        "savers",
        "server",
        "remote",
    }
    assert not list(workspace.config_dir.rglob("*.y*ml"))

    original = workspace.manifest.read_text(encoding="utf-8")
    again, created_again = initialize_workspace(tmp_path)
    assert created_again is False
    assert again == workspace
    assert workspace.manifest.read_text(encoding="utf-8") == original


def test_find_walks_parents_and_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = initialize_workspace(tmp_path / "first")
    second, _ = initialize_workspace(tmp_path / "second")
    nested = first.root / "a" / "b"
    nested.mkdir(parents=True)
    assert find_workspace(nested) == first

    monkeypatch.setenv(WORKSPACE_ENV, str(second.root))
    assert find_workspace(nested) == second


def test_load_supports_manifest_path(tmp_path: Path) -> None:
    workspace, _ = initialize_workspace(tmp_path)
    assert load_workspace(workspace.manifest) == workspace


def test_require_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    with pytest.raises(FileNotFoundError, match="wizard init"):
        require_workspace(tmp_path)


def test_bare_wizard_reports_uninitialized_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code == 2
    assert "wizard init ." in capsys.readouterr().err


def test_bare_wizard_launches_from_initialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _ = initialize_workspace(tmp_path)
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    launched: dict[str, object] = {}

    def fake_call(command: list[str], *, env: dict[str, str]) -> int:
        launched["command"] = command
        launched["env"] = env
        return 0

    monkeypatch.setattr(cli.subprocess, "call", fake_call)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--no-ui"])

    assert exc_info.value.code == 0
    assert launched["command"] == [
        cli.sys.executable,
        "-m",
        "lab_wizard.wizard.backend.main",
        "--port",
        "8884",
        "--no-ui",
    ]
    assert launched["env"][WORKSPACE_ENV] == str(workspace.root)  # type: ignore[index]


def test_clean_workspace_removes_only_managed_state(tmp_path: Path) -> None:
    workspace, _ = initialize_workspace(tmp_path)
    keep = tmp_path / "source.py"
    keep.write_text("keep me", encoding="utf-8")
    (workspace.config_dir / "instruments" / "local.yaml").write_text(
        "type: local\n", encoding="utf-8"
    )

    removed = clean_workspace(workspace)

    assert set(removed) == {
        workspace.config_dir,
        workspace.projects_dir,
        workspace.logs_dir,
        workspace.manifest,
    }
    assert keep.read_text(encoding="utf-8") == "keep me"
    assert not workspace.manifest.exists()
    assert not workspace.config_dir.exists()
    assert not workspace.projects_dir.exists()
    assert not workspace.logs_dir.exists()


def test_clean_refuses_external_manifest_paths(tmp_path: Path) -> None:
    workspace, _ = initialize_workspace(tmp_path / "workspace")
    external = tmp_path / "external-config"
    external.mkdir()
    workspace.manifest.write_text(
        "[workspace]\n"
        f'config_dir = "{external}"\n'
        'projects_dir = "projects"\n'
        'logs_dir = "logs"\n'
        "\n[lab_wizard]\nconfig_schema = 1\n",
        encoding="utf-8",
    )
    unsafe = load_workspace(workspace.root)

    with pytest.raises(ValueError, match="outside the workspace root"):
        clean_workspace(unsafe)

    assert external.exists()
    assert unsafe.manifest.exists()


def test_cli_clean_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _ = initialize_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    cli.main(["clean"])

    assert workspace.manifest.exists()
    assert workspace.config_dir.exists()


def test_cli_clean_yes_returns_to_uninitialized_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _ = initialize_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["clean", "--yes"])

    assert not workspace.manifest.exists()
    assert not workspace.config_dir.exists()


def test_cli_clean_ignores_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current, _ = initialize_workspace(tmp_path / "current")
    other, _ = initialize_workspace(tmp_path / "other")
    monkeypatch.chdir(current.root)
    monkeypatch.setenv(WORKSPACE_ENV, str(other.root))

    cli.main(["clean", "--yes"])

    assert not current.manifest.exists()
    assert other.manifest.exists()
