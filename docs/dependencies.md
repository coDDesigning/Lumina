# Dependency Management

Lumina supports Python 3.12. Direct dependencies live in `requirements.in` and
`requirements-dev.in`; the corresponding `.txt` files are generated locks with
exact transitive versions and package hashes.

## File Roles

- `requirements.in` lists packages imported by the application or required to
  run database migrations.
- `requirements.txt` is the generated production lock.
- `requirements-dev.in` includes the runtime inputs and adds test/quality tools.
- `requirements-dev.txt` is the generated development and CI lock.

Production installs `requirements.txt`. Contributors and CI install
`requirements-dev.txt`, which already includes every runtime dependency.

## Regenerating Locks

Use the pinned resolver and target Python 3.12. `--universal` preserves markers
needed by Linux CI, Windows contributors, and later container builds.

```powershell
python -m pip install "uv==0.12.1"

uv pip compile requirements.in `
  --python-version 3.12 `
  --universal `
  --generate-hashes `
  --custom-compile-command "uv pip compile requirements.in --python-version 3.12 --universal --generate-hashes --output-file requirements.txt" `
  --output-file requirements.txt

uv pip compile requirements-dev.in `
  --python-version 3.12 `
  --universal `
  --generate-hashes `
  --custom-compile-command "uv pip compile requirements-dev.in --python-version 3.12 --universal --generate-hashes --output-file requirements-dev.txt" `
  --output-file requirements-dev.txt
```

Run each compile command twice. The second run must produce no Git diff.

## Installing

```powershell
py -3.12 -m venv .venv
& ".venv\Scripts\python.exe" -m pip install `
  --require-hashes `
  --only-binary=:all: `
  --requirement requirements-dev.txt
```

Hashes ensure the downloaded distributions match the reviewed lock. Exact pins
make clean installations repeatable; neither mechanism replaces vulnerability
scanning or deliberate dependency updates.

## Updating One Package

Change its direct version in the appropriate `.in` file, regenerate both locks,
then run the complete backend and frontend checks. Never use `pip freeze` to
replace these files: a freeze includes unrelated local packages and loses the
distinction between direct and transitive dependencies.
