# Scenario: neighborhood_forum

A local community forum debates a contentious mill-site development proposal. Agent
roles map directly onto civic function (elected official, longtime resident, renter,
business owner, newcomer), making this the cleanest test of whether **functional role
alone drives stylistic diversity** with no expertise or generational signal.

## Setting

Riverside Heights Community Forum — a mid-sized neighbourhood facing a zoning vote
on redeveloping the old Hendricks Mill site into mixed-use housing.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `council_member` | 1 | `silisocs.agents.native` |
| `longtime_resident` | 2 | `silisocs.agents.native` |
| `renter` | 2 | `silisocs.agents.native` |
| `business_owner` | 2 | `silisocs.agents.native` |
| `newcomer` | 2 | `silisocs.agents.native` |

The council member is fully-connected (followed by everyone) as a bridge/authority
node. Remaining agents use a Barabási-Albert topology.

## Key dynamics

- Civic role → posting register: does institutional position (council, business,
  tenant) produce distinct language without any expertise gradient?
- Stakes asymmetry: renters and newcomers stand to benefit; longtime residents fear
  character change; business owners weigh traffic and footfall
- Low ideological heat: disagreement is real but not partisan, keeping role signal clean

## Stylistic diversity axis tested

**Role diversity** — the primary independent variable is civic function, not expertise,
generation, or persona depth. All agents have comparably detailed backstories.

## Run

```bash
uv run silisocs --config-path scenarios/neighborhood_forum/conf num_steps=10
```

## Studies using this world

- `experiments/studies/style_diversity/` — h1 (model size), h2 (temperature),
  h3 (persona richness), h6 (CTA phrasing)
