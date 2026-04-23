#!/usr/bin/env python3
"""Launch election_recsys_engagement runs for seeds 16-25 in a continuous queue."""

import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunConfig:
    seed: int
    timeline_type: str
    gpu_id: int

    @property
    def name(self) -> str:
        return f"seed{self.seed}_{self.timeline_type}"

    def get_command(self) -> list[str]:
        mode_name = {
            "chronological": "chronological",
            "twitter": "recsys_twitter",
            "twhin": "recsys_twhin",
        }[self.timeline_type]

        run_label = f"clean50x10_seed{self.seed}_{mode_name}_single_act"
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "mastodon_sim.runtime.runner",
            "--config-path",
            "scenarios/election_recsys_engagement/conf",
            "scenario=election_recsys_engagement",
            "sim.num_agents=50",
            "sim.num_steps=50",
            "sim.engine.action_loop.built_in=single_action",
            "sim.tool_calling.mode=single",
            f"sim.seed={self.seed}",
            f"sim.run_name={run_label}",
            f"experiment_name={run_label}",
            # Keep effective total entities at 50: 47 voters + 2 candidates + 1 fixed_news.
            "scenario.persona_pipeline.classes.voter.count=47",
        ]

        if self.timeline_type == "chronological":
            cmd.append("sim.timeline_mode=follower_chronological")
        elif self.timeline_type == "twitter":
            cmd.extend(
                [
                    "sim.timeline_mode=pure_recsys",
                    "sim.gm.components.recommend.params.default_recsys_type=twitter",
                ]
            )
        elif self.timeline_type == "twhin":
            cmd.extend(
                [
                    "sim.timeline_mode=pure_recsys",
                    "sim.gm.components.recommend.params.default_recsys_type=twhin",
                ]
            )
        else:
            raise ValueError(f"Unknown timeline type: {self.timeline_type}")

        return cmd

    def get_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(self.gpu_id)
        return env


def build_all_runs(seeds: list[int], num_gpus: int = 4) -> list[RunConfig]:
    runs: list[RunConfig] = []
    # 1. Build all the runs without assigning a GPU yet
    for seed in seeds:
        for timeline in ["chronological", "twitter", "twhin"]:
            runs.append(RunConfig(seed=seed, timeline_type=timeline, gpu_id=0))

    # 2. (Optional but recommended) Shuffle the run order
    # so you don't end up processing all 'twitter' runs at the same time.
    random.shuffle(runs)

    # 3. Deal out the GPUs round-robin style
    for i, run in enumerate(runs):
        run.gpu_id = i % num_gpus

    return runs


def main() -> None:
    base_dir = Path("/scratch/ss14247/mastodon-sim")
    if not base_dir.exists():
        print(f"Missing repo path: {base_dir}")
        sys.exit(1)

    # Create a logs directory if it doesn't exist
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    seeds = list(range(11, 21))

    # 16 concurrent runs (equivalent to your old 8-seed * 2-timeline batch)
    max_concurrent = 16
    timeout_per_run_s = 3 * 3600

    pending_runs = build_all_runs(seeds)
    total_runs = len(pending_runs)

    print("=" * 80)
    print("Experiment plan")
    print(f"Seeds: {seeds[0]}-{seeds[-1]} ({len(seeds)} total)")
    print("Modes per seed: twitter, twhin")
    print(f"Concurrency: Maintaining {max_concurrent} parallel simulations")
    print("Logging: Direct to disk (OS Pipe buffering disabled)")
    print("=" * 80)

    all_failed: list[str] = []
    all_ok: list[str] = []

    # Dictionary to keep track of running processes
    # Format: {run_name: (Popen_obj, log_file_handle, log_path, start_time)}
    running_procs = {}

    # Main event loop: keep going until both the queue and the running list are empty
    while pending_runs or running_procs:
        # 1. Fill the active pool up to the max_concurrent limit
        while pending_runs and len(running_procs) < max_concurrent:
            run = pending_runs.pop(0)
            print(f"LAUNCH {run.name} GPU={run.gpu_id} (Pending in queue: {len(pending_runs)})")

            log_path = log_dir / f"{run.name}.log"
            log_file = open(log_path, "w")

            proc = subprocess.Popen(
                run.get_command(),
                cwd=str(base_dir),
                env=run.get_env(),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            running_procs[run.name] = (proc, log_file, log_path, time.time())
            time.sleep(0.5)  # Slight stagger to prevent DB/filesystem locking spikes

        # 2. Check the status of all currently running processes
        finished_this_tick = []
        for name, (proc, log_file, log_path, start_time) in running_procs.items():
            retcode = proc.poll()

            if retcode is not None:
                # Process completed
                if retcode == 0:
                    print(f"  OK {name}")
                    all_ok.append(name)
                else:
                    print(f"  FAIL {name} (rc={retcode})")
                    if log_path.exists():
                        with open(log_path) as f:
                            lines = f.readlines()
                            if lines:
                                print("\n--- Last log output ---")
                                print("".join(lines[-15:]))
                                print("-----------------------\n")
                    all_failed.append(name)

                log_file.close()
                finished_this_tick.append(name)

            elif time.time() - start_time > timeout_per_run_s:
                # Process timed out
                proc.kill()
                proc.wait()  # Ensure OS cleanup
                print(f"  TIMEOUT {name} (Exceeded {timeout_per_run_s}s limit)")
                all_failed.append(name)
                log_file.close()
                finished_this_tick.append(name)

        # 3. Clean up finished processes to make room in the pool
        for name in finished_this_tick:
            del running_procs[name]

        # 4. Sleep briefly so we aren't spinning the CPU constantly
        if running_procs:
            time.sleep(1)

    print("\n" + "=" * 80)
    print("SUITE COMPLETE")
    print(f"Total processed: {total_runs}")
    print(f"Succeeded: {len(all_ok)}")
    print(f"Failed: {len(all_failed)}")

    if all_failed:
        print("\nFailed runs:")
        for name in all_failed:
            print(f"  - {name}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
