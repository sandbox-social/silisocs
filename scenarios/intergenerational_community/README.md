# Scenario: intergenerational_community

Four generations of a small-town community respond to their library retiring its
physical DVD and CD collection. The primary dimension of variation is **generational
cohort** — the same low-conflict announcement processed in four entirely different
registers, from formal paragraph-length senior posts to two-sentence teen shrugs.

## Setting

Millbrook Public Library Community Board — a multigenerational online space where
seniors, middle-aged residents, young adults, and teenagers coexist on the same
platform. Library Director Carol Huang has announced retirement of the 4,200-item
DVD/CD collection in favour of expanded Hoopla and Kanopy streaming access.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `senior` | 2 | `silisocs.agents.native` |
| `middle_aged_resident` | 2 | `silisocs.agents.native` |
| `young_adult` | 3 | `silisocs.agents.native` |
| `teen` | 2 | `silisocs.agents.native` |

No fully-connected nodes; Barabási-Albert topology with base followership 0.5.

## Key dynamics

- Generational register: formal paragraphs (seniors) → civic prose (middle-aged) →
  casual with em-dashes (young adults) → two-sentence shrug (teens)
- Low ideological heat: no one opposes the library, isolating generational voice
  from political stance
- Cross-generational reply: what happens when a teen replies to a senior's
  carefully-constructed paragraph?

## Stylistic diversity axis tested

**Generational register** — roles are defined by age cohort, producing systematic
differences in post length, formality, punctuation, and emotional register. Expertise
and civic function are secondary.

## Agent highlights

| Name | Role | Distinctive voice |
|---|---|---|
| Dorothy Hawkins | senior | Formal paragraphs; argument about serendipitous discovery |
| Harold Simmons | senior | Plain, direct; practical questions about specific Korean War docs |
| Jim Castellano | middle_aged_resident | Retired librarian; institutional memory, cites circulation data |
| Karen Osei | middle_aged_resident | School administrator; equity framing, broadband access |
| Maya Chen | young_adult | Casual; substantive concern about digital permanence |
| Tyler Brooks | teen | Genuinely confused why physical discs matter; 2–3 sentences max |
| Zara Kim | teen | Brief, lightly ironic; mild soft spot for physical media (vinyl phase) |

## Run

```bash
uv run silisocs --config-path scenarios/intergenerational_community/conf num_steps=10
```

## Studies using this scenario

- `experiments/studies/style_diversity/` — h1 (model size), h2 (temperature),
  h3 (persona richness), h6 (CTA phrasing)
