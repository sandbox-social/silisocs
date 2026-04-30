#!/usr/bin/env python
"""
diagnose_config.py
Check what's wrong with Hydra configuration
"""

import os
from pathlib import Path

print("=" * 70)
print("HYDRA CONFIGURATION DIAGNOSTICS")
print("=" * 70)

# 1. Check current directory
print("\n1. Current directory:")
print(f"   {os.getcwd()}")

# 2. Check if src/ exists
print("\n2. Checking for src/ directory...")
if Path("src").exists():
    print("   ✓ src/ exists")
else:
    print("   ❌ src/ NOT FOUND - you must be in project root!")
    exit(1)

# 3. Check if src/conf/ exists
print("\n3. Checking for src/conf/...")
conf_dir = Path("src/conf")
if conf_dir.exists():
    print("   ✓ src/conf/ exists")
    print("   Contents:")
    for item in conf_dir.iterdir():
        print(f"     - {item.name}")
else:
    print("   ❌ src/conf/ does NOT exist")
    print("   THIS IS THE PROBLEM - You need to create this directory!")

# 4. Check for config.yaml
print("\n4. Checking for src/conf/config.yaml...")
config_file = Path("src/conf/config.yaml")
if config_file.exists():
    print("   ✓ src/conf/config.yaml exists")
    print("   First 10 lines:")
    with open(config_file) as f:
        for i, line in enumerate(f):
            if i >= 10:
                break
            print(f"     {line.rstrip()}")
else:
    print("   ❌ src/conf/config.yaml NOT FOUND")
    print("   This is why Hydra returns {}")

# 5. Check for sim config
print("\n5. Checking for src/conf/sim/base.yaml...")
sim_config = Path("src/conf/sim/base.yaml")
if sim_config.exists():
    print("   ✓ src/conf/sim/base.yaml exists")
else:
    print("   ❌ src/conf/sim/base.yaml NOT FOUND")

# 6. Check for social media config
print("\n6. Checking for src/conf/social_media/mastodon.yaml...")
sm_config = Path("src/conf/social_media/mastodon.yaml")
if sm_config.exists():
    print("   ✓ src/conf/social_media/mastodon.yaml exists")
else:
    print("   ❌ src/conf/social_media/mastodon.yaml NOT FOUND")

# 7. Check main.py
print("\n7. Checking src/sim/main.py...")
main_file = Path("src/sim/main.py")
if main_file.exists():
    print("   ✓ src/sim/main.py exists")
    print("   Checking @hydra.main decorator...")
    with open(main_file) as f:
        for i, line in enumerate(f, 1):
            if "config_path" in line:
                print(f"   Line {i}: {line.strip()}")
                if "../conf" in line:
                    print("   ✓ config_path looks correct")
                elif "../../conf" in line:
                    print("   ❌ config_path is WRONG (should be ../conf)")
                break
else:
    print("   ❌ src/sim/main.py NOT FOUND")

# Summary
print("\n" + "=" * 70)
print("DIAGNOSIS SUMMARY")
print("=" * 70)

issues = []
if not Path("src/conf").exists():
    issues.append("src/conf/ directory missing")
if not config_file.exists():
    issues.append("src/conf/config.yaml missing")
if not sim_config.exists():
    issues.append("src/conf/sim/base.yaml missing")
if not sm_config.exists():
    issues.append("src/conf/social_media/mastodon.yaml missing")

if issues:
    print("\n❌ ISSUES FOUND:")
    for issue in issues:
        print(f"   - {issue}")
    print("\n📝 TO FIX:")
    print("   1. You need to install the YAML config files")
    print("   2. They should go in src/conf/ directory")
    print("   3. Use the files I provided:")
    print("      - conf_config.yaml → src/conf/config.yaml")
    print("      - conf_sim_base.yaml → src/conf/sim/base.yaml")
    print("      - conf_social_media_mastodon.yaml → src/conf/social_media/mastodon.yaml")
    print("      - etc.")
else:
    print("\n✓ All config files present!")
    print("   The empty {} might be a different issue.")
    print("   Try running: python src/sim/main.py --cfg job")
