# Using Reposition

From a checkout, `./reposition` runs directly with Python 3.10+ and SQLite FTS5.
The default needs no installation or third-party Python packages. On Windows,
use `py reposition` in place of `./reposition`.

## Start with the demo

```sh
./reposition demo
```

This searches synthetic issues and pull requests and follows a literal reference.
Pass your own search text as a single argument, for example
`./reposition demo 'advisory metadata'`. Each run uses a fresh in-memory database.

For synthetic immutable triage-o-mator evidence:

```sh
./reposition demo --cache
```

This builds a disposable index across all nine components, shows ranked,
source-verified fragments and reads the first result with a larger source window.
It selects the synthetic frozen corpus explicitly and carries the query's exact
checkpoint into retrieval. All files live in a temporary directory and are
removed when the demo finishes. You can also pass search text, such as
`./reposition demo --cache 'src/terminal.py'`.

## Search your saved data

From this checkout, with a saved GitHub JSON cache:

```sh
./reposition index my-cache.json --format github --repo owner/repository
./reposition search 'migration runs an older helper'
```

The default database is `.reposition/index.sqlite` in your current directory.
Use one database per repository. If you work with multiple repositories in the
same directory, give each index and subsequent command its own `--db` path.
The repository name stays explicit to prevent mixing evidence across repositories.

`github` input accepts a JSON array from `gh issue list --json
number,title,body,url,updatedAt,comments` or `gh pr list` with corresponding
fields. REST lists can also be used. PR changed-file patches must be included
in the cache to search them. Lists are bounded snapshots, not proof of complete
repository coverage. See [input formats](input-formats.md) for the native record
format and component-cache adapter. Immutable triage-o-mator caches have their
own [workflow](cache-integration.md).

Once indexed, explore the same database:

```sh
./reposition search 'archive digest' --component comments
./reposition related 40
./reposition info
```

Use `--json` on search to get structured rankings and evidence. Use `--chars`
to set the complete evidence-text budget. These options are optional; the default
is FTS5 search with an 8,000-character evidence budget.

Replacing a snapshot requires an explicit flag:

```sh
./reposition index refreshed-cache.json --format github --repo owner/repository --replace
./reposition export > normalized-cache.json
```

## Install the command or Python API

Installation is useful when calling Reposition from other directories, importing
its Python API or using the optional comparison methods. From this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
```

Then use `reposition` in place of `./reposition`. On Windows, create the environment
with `py -m venv .venv`, install with `.venv\Scripts\python -m pip install -e .`,
and activate with `.venv\Scripts\Activate.ps1` in PowerShell.

Install from this checkout or a supplied wheel. The unrelated package with the
same PyPI name is not this project.

Optional dependencies are described in the [README](../README.md#optional-comparisons-and-token-budgets).
Use `./reposition --help` and command-specific help for the full set of options.
