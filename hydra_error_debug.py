#!/usr/bin/env python
"""
hydra_error_debug.py
Show the ACTUAL error Hydra is encountering
"""

import sys
from pathlib import Path

# Add src to path
if (Path.cwd() / "src").exists():
    sys.path.insert(0, str(Path.cwd() / "src"))

print("=" * 70)
print("HYDRA ERROR DIAGNOSTICS")
print("=" * 70)

# Test 1: Load each YAML file individually
print("\n1. Testing individual YAML files for syntax errors...")

from omegaconf import OmegaConf

files_to_test = [
    "src/conf/config.yaml",
    "src/conf/sim/base.yaml",
    "src/conf/social_media/mastodon.yaml",
    "src/scenarios/election/config.yaml",
    "src/scenarios/election/scenario.yaml",
    "src/scenarios/election/candidates.yaml",
    "src/scenarios/election/probes.yaml",
]

all_valid = True
for filepath in files_to_test:
    if Path(filepath).exists():
        try:
            cfg = OmegaConf.load(filepath)
            print(f"  ✓ {filepath}")
        except Exception as e:
            print(f"  ❌ {filepath}")
            print(f"     ERROR: {e}")
            all_valid = False
    else:
        print(f"  ❌ {filepath} - FILE NOT FOUND")
        all_valid = False

if not all_valid:
    print("\n⚠ Fix YAML syntax errors above before proceeding")
    sys.exit(1)

# Test 2: Check defaults list in config.yaml
print("\n2. Checking defaults list in config.yaml...")
config_yaml = Path("src/conf/config.yaml")
cfg = OmegaConf.load(config_yaml)
print(f"  Config keys: {list(cfg.keys())}")
if "defaults" in cfg:
    print("  Defaults list:")
    for item in cfg.defaults:
        print(f"    - {item}")
else:
    print("  ⚠ No 'defaults' key found!")

# Test 3: Try Hydra composition with error catching
print("\n3. Testing Hydra composition...")

try:
    from hydra import compose, initialize_config_dir

    conf_dir = (Path.cwd() / "src" / "conf").absolute()
    print(f"  Config dir: {conf_dir}")

    # Initialize Hydra
    print("\n  3a. Initializing Hydra...")
    with initialize_config_dir(config_dir=str(conf_dir), version_base=None):
        print("  ✓ Hydra initialized")

        # Try to compose
        print("\n  3b. Composing config...")
        try:
            cfg = compose(config_name="config")
            print("  ✓ Config composed!")
            print(f"  Config type: {type(cfg)}")
            print(f"  Config keys: {list(cfg.keys())}")

            # Show what's in it
            print("\n  Config contents:")
            for key in cfg.keys():
                if key == "defaults":
                    continue
                val = cfg[key]
                if isinstance(val, dict):
                    print(f"    {key}: dict with {len(val)} keys")
                else:
                    print(f"    {key}: {type(val).__name__}")

        except Exception as e:
            print("  ❌ Composition FAILED!")
            print(f"  Error type: {type(e).__name__}")
            print(f"  Error message: {e}")
            print("\n  Full traceback:")
            import traceback

            traceback.print_exc()

except Exception as e:
    print(f"❌ Failed to initialize Hydra: {e}")
    import traceback

    traceback.print_exc()

# Test 4: Check if scenario defaults resolve
print("\n4. Checking scenario defaults resolution...")
scenario_config = Path("src/scenarios/election/config.yaml")
if scenario_config.exists():
    scenario_cfg = OmegaConf.load(scenario_config)
    print(f"  Scenario config keys: {list(scenario_cfg.keys())}")
    if "defaults" in scenario_cfg:
        print("  Scenario defaults:")
        for item in scenario_cfg.defaults:
            print(f"    - {item}")
            # Check if these files exist
            if isinstance(item, str):
                file_to_check = Path(f"src/scenarios/election/{item}.yaml")
                exists = file_to_check.exists()
                status = "✓" if exists else "❌"
                print(f"      {status} {file_to_check}")

# Test 5: Try composing with overrides
print("\n5. Testing composition with overrides...")
try:
    from hydra import compose, initialize_config_dir

    conf_dir = (Path.cwd() / "src" / "conf").absolute()

    with initialize_config_dir(config_dir=str(conf_dir), version_base=None):
        try:
            # Try with override
            cfg = compose(config_name="config", overrides=["sim.num_steps=2"])
            print("  ✓ Composition with overrides works!")
            print(f"  num_steps value: {cfg.sim.num_steps if 'sim' in cfg else 'N/A'}")
        except Exception as e:
            print(f"  ❌ Override failed: {e}")

except Exception as e:
    print(f"  ❌ Test failed: {e}")

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
print("\nIf composition failed above, the error message shows what's wrong.")
print("Common issues:")
print("  - YAML syntax error in one of the files")
print("  - Missing file referenced in defaults")
print("  - Circular dependency in defaults")
print("  - Invalid interpolation (${...})")
