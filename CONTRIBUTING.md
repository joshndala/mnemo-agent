# Contributing to mnemo

Thanks for wanting to improve mnemo! Here's everything you need to get started.

---

## Running Tests

```bash
# Install with dev extras
pip install -e ".[dev]"

# Run the full suite
pytest tests/ -v

# Run a single test class
pytest tests/test_cli.py::TestRecall -v

# Run with coverage
pytest tests/ --cov=mnemo --cov-report=term-missing
```

All 23 tests should pass. If you add a new command or adapter, add matching tests to `tests/test_cli.py`.

---

## Adding a New Provider Adapter

Provider adapters live in `src/mnemo/adapters/`. Each adapter is a self-contained module with two functions:

### 1. Create the adapter file

```
src/mnemo/adapters/myprovider_adapter.py
```

Implement two functions:

```python
from mnemo.models import AgentDump, Fact

def dump_from_myprovider(/* provider-specific args */) -> AgentDump:
    """Fetch memories from MyProvider and return a normalized AgentDump."""
    # 1. Guard import — only fail if the user actually calls this function
    try:
        import myprovider_sdk  # type: ignore
    except ImportError:
        raise ImportError("Install with: pip install 'mnemo[myprovider]'")

    # 2. Fetch raw memories from the provider API
    raw = myprovider_sdk.Client().get_all(...)

    # 3. Normalize to Fact objects
    facts = [
        Fact(
            entity=...,
            attribute=...,
            value=...,
            source="myprovider",
            confidence=...,
        )
        for item in raw
    ]
    return AgentDump(agent=agent_name, source="myprovider", facts=facts)


def load_to_myprovider(dump: AgentDump, /* provider-specific args */) -> int:
    """Push facts to MyProvider. Returns count of facts pushed."""
    ...
```

**Key rules:**
- Always guard `import` so the tool is usable without the SDK installed.
- Map provider fields to the normalized `{entity, attribute, value}` triple.
- Return `int` (facts pushed) from `load_to_*`.

### 2. Wire it into the CLI

In `src/mnemo/cli.py` find the `cmd_dump` and `cmd_load` commands. Add a new branch:

```python
elif source == "myprovider":
    cfg = load_config(agent, base)
    from mnemo.adapters.myprovider_adapter import dump_from_myprovider
    dump = dump_from_myprovider(api_key=cfg.myprovider_api_key, agent=agent)
```

### 3. Add the optional dep to pyproject.toml

```toml
[project.optional-dependencies]
myprovider = ["myprovider-sdk>=1.0"]
all = ["mnemo[mem0,letta,myprovider,parquet,graph]"]
```

### 4. Add a config field to MnemoConfig

In `src/mnemo/models.py`:

```python
class MnemoConfig(BaseModel):
    ...
    myprovider_api_key: str | None = None
```

### 5. Write tests

Add a `TestMyProviderAdapter` class in `tests/test_cli.py` with at minimum:
- A test that the adapter raises `ImportError` when the SDK is missing (mock the import)
- A test that normalized facts have the expected schema

---

## Code Style

- Python 3.12+, type-annotated throughout
- 88-char line length (Black compatible)
- Docstrings on all public functions and classes

---

## Submitting a PR

1. Fork → branch → implement → test
2. Run `pytest tests/ -v` — all tests must pass
3. Add an entry to `CHANGELOG.md` under `[Unreleased]`
4. Open a PR with a clear description of your adapter and any new CLI flags
