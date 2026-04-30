```text
simsandbox/
  ├── src/
  │   └── EASE/              # Main package
  │       ├── agents/
  │       │   ├── components/        # Agent action/reasoning components
  │       │   └── initialization/    # Agent setup
  │       ├── environments/
  │       │   ├── backends/          # Mastodon/Twitter-like app backend
  │       │   └── gm/                # Game master (observe, recommend, etc.)
  │       ├── evaluations/
  │       │   ├── probes/            # Probe types and deployment
  │       │   └── analysis/          # Post-hoc analysis utilities
  │       ├── simulation/            # Simulation runner and telemetry
  │       |   ├── runner.py          # Probe types and deployment
  │       |   ├── simulators/        # wrapper object
  │       │   └── engines/           # simulation engine object
  │       └── conf/                  # Base Hydra config (sim, scenario, social_media)
  │
  ├── scenarios/                     # Per-scenario configs and inputs
  │   └── {scenario_name}
  │
  ├── studies/                       # Study orchestration
  │   ├── scripts/
  │   │   ├── run_study.py           # Orchestrator (simulate → eval → register → organize)
  │   │   ├── organize_experiments.py # Builds experiments/ tree from study.yaml
  │   │   └── study_io.py            # Shared IO utilities
  |   ├── study_schema.md
  │   └── {study_name}/              # Study data (study.yaml, eval.py, results)
  │
  └── outputs/                       # Raw simulation outputs (gitignored)
      ├── {scenario}_experiment/{timestamp}/
      └── eval_{study}/
```