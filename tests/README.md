# Test layout

Tests are grouped into domain subpackages that mirror `src/silisocs/`, so you can run one
domain quickly (`uv run pytest tests/engines -q`) and new tests have an obvious home.

| Directory | What belongs here | Mirrors |
|-----------|-------------------|---------|
| `tests/runtime/` | Config composition/validation, runtime projection, construction & initialization, language-model providers, checkpoint save/restore/resume, session & runner orchestration | `silisocs/runtime`, `silisocs/initialization` |
| `tests/agents/` | Native and fixed agents, agent memory policies, harness agents/adapters/proxy, Concordia adapter boundary | `silisocs/agents`, `silisocs/adapters` |
| `tests/environments/` | Backends, game-master components, action catalogs, action parsing/aliases/filters, structured action logging | `silisocs/environments` |
| `tests/engines/` | Loop/step/turn/participation policies, scheduling and concurrency, flow & branch routing, run control, interventions | `silisocs/simulation_engines` |
| `tests/evaluations/` | Probes, evaluators, run artifacts, run manifest, vocabulary | `silisocs/evaluations` |
| `tests/analysis/` | Panels, views, exploration, reports, and the shared design/visual system | `silisocs/analysis`, `silisocs/design` |
| `tests/studies/` | Study schema, planning, resume markers, and the study runner | `silisocs/studies` |
| `tests/studio/` | Studio HTTP surface, launch/jobs control plane, forms, workspace, platform viewers | `silisocs/studio` |
| `tests/scenarios/` | Bundled-scenario composition, scenario library resolution, scenario generation | `scenarios/`, `silisocs/scenario_gen`, `silisocs/scenario_library.py` |
| `tests/e2e/` | Cross-layer end-to-end runs (runner/CLI/subprocess/browser) plus packaging, wheel, and repo-wide API guards | (spans layers) |

Conventions:

- Every subdirectory is a package (`__init__.py`); test modules import as
  `tests.<domain>.test_<name>`, which is the form `class_path` strings in tests must use.
- `tests/conftest.py` stays at the root — its autouse fixtures apply suite-wide.
- Paths to the repo root inside a test are `Path(__file__).resolve().parents[2]`.
- A test that exercises one layer's API belongs in that layer's directory; only tests that
  drive the whole stack (or the distribution) belong in `tests/e2e/`.
