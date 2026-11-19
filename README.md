# SettleLite

SettleLite currently validates event envelopes and preserves exact integer-cent
values. Run from this repository with Python 3.12:

```sh
python -m settlelite validate
python -m unittest discover -s tests -v
```

`validate` reads one JSON object per input line and emits one JSON result per
line. See CONTRACT.md for its public interface. The product has no external
runtime or test dependencies. Its repository-owned test entrypoint above is
the complete initial suite.

Build the native environment with `docker build --target goal-run .`. The
Dockerfile also declares a `task-base` stage for the same product runtime.
