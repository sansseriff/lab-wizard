# Releasing

This repository publishes two PyPI packages:

- `lab-procedure`, from `procedure_framework/`
- `lab-wizard`, from `lab_wizard/`

The GitHub Actions workflows publish when release tags are pushed:

- `lab-procedure-v*`
- `lab-wizard-v*`

## Recommended Flow

Publish `lab-procedure` before `lab-wizard` when both changed, because `lab-wizard`
depends on `lab-procedure`.

Preview the next version:

```bash
python scripts/release.py lab-procedure patch
python scripts/release.py lab-wizard patch
```

Bump files without committing:

```bash
python scripts/release.py lab-procedure patch --write
```

Full release:

```bash
python scripts/release.py lab-procedure patch --commit --tag --push
python scripts/release.py lab-wizard patch --commit --tag --push
```

Use `minor` or `major` instead of `patch` when the release warrants it.

PyPI versions are immutable. If a publish succeeds, that exact version cannot be
uploaded again; bump to a new version before retrying.
