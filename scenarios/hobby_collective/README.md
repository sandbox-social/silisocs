# Scenario: hobby_collective

Nine amateur astrophotographers respond to a rare naked-eye comet passage. All agents
share a single `hobbyist` role — there is no formal role structure or expertise
hierarchy. This is the cleanest test of whether **persona depth alone drives stylistic
diversity** without any role or generational signal.

## Setting

DeepSky Social — an online amateur astrophotography community where members share
images, equipment notes, processing tips, and personal reflections. A Hale-class
comet (first naked-eye comet in 20+ years) triggers a surge of posts over three
optimal viewing nights.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `hobbyist` | 9 | `silisocs.agents.entity` |

All agents are the same role. Differentiation comes entirely from individual backstory,
motivation, and voice — not from assigned function.

## Agent archetypes

| Name | Voice |
|---|---|
| Eleanor Marsh | Retired teacher; pedagogical, long captions, explains everything |
| Kai Petersen | Teen; technical specs, competitive, lists ISO/gain/exposure |
| Ananya Krishnan | Immigrant software engineer; lyrical, posts as letters to her late father |
| Robert Finch | Divorced, meditative; sparse posts, images carry the weight |
| Linda Torres | Expert imager; methodological, corrects technique politely but clearly |
| Yusuf Al-Rashidi | Science communicator; inviting, addresses non-experts directly |
| Dr. Nadia Volkov | Professional planetary scientist; rare but authoritative, mixes rigor and awe |
| Marco Bianchi | Italian club organiser; community-first, mixes Italian, "we" not "I" |
| Priya Desai | Complete beginner; tentative, grateful, asks lots of questions |

## Key dynamics

- Persona depth → voice distinctiveness: nine different reasons to care about the
  same comet produce nine different posting registers
- No role signal to lean on: the only differentiator is individual backstory
- Warm platform culture: agents are inclined to respond to each other, raising the
  risk of voice homogenisation through social imitation

## Stylistic diversity axis tested

**Individual persona richness** — the independent variable is the depth and specificity
of the individual backstory, with role held constant across all agents.

## Run

```bash
uv run silisocs --config-path scenarios/hobby_collective/conf num_steps=10
```

## Studies using this scenario

- `experiments/studies/style_diversity/` — h1 (model size), h2 (temperature),
  h3 (persona richness), h6 (CTA phrasing)
