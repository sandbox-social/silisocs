# First impressions

## Readme

1) The "Read the full documentation" link at the top of the readme does not work. It is the very first thing I look for and click on when looking at the repo. There is no explanation on how to get the local mkdocs as an alternative.
2) What's hydra? Link to some external docs + maybe a 1 sentence explanation of what it does
3) Config layout unclear: needs a few sentences explaining what are scenarios, what are they useful for, and then only how to use them/make new ones.
4) "Probe responses" maybe add some explanation?
5) Why on earth would you have two separate dashboards. These need to be merged.
6) Install process failed (uv sync failed to build `pandas==2.1.0`. Due to an incompatibility with numpy>=2, need 2.2<=pandas<=3.0)
7) uv run poe test raised an error.
8) Having to clone a repo and setup an uv env is not horrible but not great either. Having just a pip install would be great, although probably a fair bit of work.

## Docs

### Home
1) "Scalable to 5000+ agents": is this up to date? Also if we use the oasis tricks (cheap leaf agents and 1% of network actually active at each step) we can probably make that number reach the million they claim.
2) "Quick links" in the home page aren't formatted properly. It's also redundant of the 3 sections right above.
3) Fairly good user guide, but no api references. We don't have any info about the code (detailed description of functions, modules and objects, e.g. sphinx docs).
4) The diagram isn't very insightful, it's more confusing than anything.

### Quickstart
1) The default "uv run mastodon-sim" does not work 
```
$ uv run mastodon-sim
Traceback (most recent call last):
  File "/home/aurelienbk/mastodon-sim/.venv/bin/mastodon-sim", line 4, in <module>
    from mastodon_sim.runtime.runner import main
  File "/home/aurelienbk/mastodon-sim/src/mastodon_sim/runtime/runner.py", line 55, in <module>
    from mastodon_sim.runtime.simulation import Simulation
  File "/home/aurelienbk/mastodon-sim/src/mastodon_sim/runtime/simulation.py", line 33, in <module>
    from concordia.utils import html as html_lib
ImportError: cannot import name 'html' from 'concordia.utils' (/home/aurelienbk/mastodon-sim/.venv/lib/python3.12/site-packages/concordia/utils/__init__.py)
```
It looks like the latest version of concordia (2.4.0) is installed, which seems incompatible. 
2) Which llm is the default `uv run mastodon-sim` running? What kind of hardware do I need?
3) Preset configuration link in the oasis preset gives 404 error
4) Should link towards the oasis paper
5) Should link towards a list of all available cli arguments and the valid parameters they support.
6) "try a different llm" which ones are available? how do i put in my api keys? how do i run locally? Either explain it here or link to someplace that explains it.
7) Give the analysis dashboard command with a dummy file provided by the repo as --output-dir so that we don't need to make a run ourselves to see what it looks like/check that it works.

### Usage overview

1) That diagram seems completely detached from the text and not clear at all. Where are the four phases? how am I supposed to read this (left-right? top-bottom?).
2) unexplained jargon (game master, memories, probes)
3) "the game master executes it against the social media backend" what does that mean
4) CLI should link to the quickstart
5) Hydra CLI overrides needs to link towards a list of possible cli arguments
6) how to define per-agent llm isn't clear
7) social network: "each step, agents" rather than "each step, an agent".


## Dashboards
### Launch dashboard
`uv run streamlit run src/mastodon_sim/dashboard/launch_app.py`
1) contrast of the left panel is messed up in light mode, can barely read what's written.
2) Quick links don't seem to work.
3) When I press "create", visually it looks like nothing happened. There should be a popup saying that the scenario was created succesfully and give it's location.
4) When i load a scenario the values in the dashboard don't change.
5) Why does "export as yaml" create a download button that i then have to click? why not just download the file immediately?
6) "Run simulation" button doesn't work
```
Traceback (most recent call last):
  File "/home/aurelienbk/mastodon-sim/src/mastodon_sim/runtime/runner.py", line 24, in <module>
    from dataclasses import asdict
  File "/home/aurelienbk/mastodon-sim/src/mastodon_sim/runtime/dataclasses.py", line 3, in <module>
    from dataclasses import dataclass, field
ImportError: cannot import name 'dataclass' from partially initialized module 'dataclasses' (most likely due to a circular import) (/home/aurelienbk/mastodon-sim/src/mastodon_sim/runtime/dataclasses.py)
```
7) "Run simulation" button is kind of hidden in a small "launch" sub-page. It should be easy to spot immediately. Either put the "Save scenario", "export as yaml" and "run simulation" buttons at the top of the left pannel and make it open a popup to review settings before confirming, or add a very visible "launch" button at the top of the left panel that brings you to the currently existing launch page.
8) Doesn't let me put more than 500 steps.

### Analysis dashboard

1) Give a dummy completed run so that people don't need to have ran an actual sim themselves to look at it.
2) Merge into one dashboard with the launch one.
