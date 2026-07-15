# Declarative Worlds — a language for generating environment backends from config

> Status: full language plan / design specification. Nothing here is implemented.
> Goal: express **virtually any scenario** as data — YAML, a visual builder, a
> Python builder API, or an LLM emitting the spec — interpreted by one generic
> backend, with zero changes to the silisocs core.

---

## Part I — Feasibility and grounding

### 1) Why this works in silisocs specifically

The backend contract is narrow and data-shaped. A backend is, in full:

| Contract | Where it lives today | Data-shaped already? |
|---|---|---|
| Named actions with typed params | `ActionDescriptor` / `Parameter` (`backends/base.py`) | ✅ pure dataclasses; schemas, prompts, catalogs, aliases, filters all derive from them |
| Invocation + commit signal | `invoke_action_detailed(name, kwargs) -> (committed, msg)` | ✅ generic dispatch |
| Committed-event log | `_log_action_event` + committed-events mirror | ✅ append a dict |
| State snapshot | `get_state()` / `set_state()` (`provides_checkpoint_state`) | ✅ one JSON dict |
| Observation | GM `observe` component reads the backend | ✅ pluggable slot |
| Per-step world dynamics | GM `update` component | ✅ pluggable slot |

Everything downstream — tool-calling schemas, resolve validation, action
filters/aliases, probes, checkpoints, interventions (`inject_action` speaks the
catalog), `count_committed_events`, the analysis dashboard — consumes those
seams, not the backend class. One `DeclarativeWorldApp(SocialBackendApp)` that
*interprets a world spec* inherits the entire runtime for free.

Verified enabling detail: action discovery is **instance-level**
(`_raw_actions` does `inspect.getmembers(self, ...)`, `base.py:392-395`), so a
backend that synthesizes bound methods (with crafted `__signature__`,
annotations, docstring, `__app_action__` marker) at `__init__` needs **no core
changes** — descriptors, tool schemas, generic prompts, aliases,
enabled/excluded filters, and `invoke_action_*` all just work.

### 2) Prior art stolen from

- **PDDL** (planning): action = params + preconditions + effects.
- **Inform 7 / MUDs / MOO**: rooms, containment, verbs on objects
  ("affordances"), scoped message broadcast.
- **TextWorld**: procedurally *generates* text games from grammars — proof that
  world-as-data scales and composes.
- **Generative Agents (Smallville)**: areas + stateful objects; agents observe
  co-located state.
- **ECS (entity-component-system)**: entities are bags of typed state; behavior
  is generic systems over the data. This is the interpreter's internal shape.
- **Tabletop RPG rulebooks**: stats, skills, status conditions, dice — the
  actor-model vocabulary below is deliberately rulebook-shaped because
  rulebooks are the best-tested "worlds as prose-data" format in existence.
- **Small-core language design**: Lisp (a handful of special forms, everything
  else is macros), GHC Core, RDF/OWL (everything is triples + property
  axioms), Datalog (derived relations are stratified rules). These motivate
  the kernel/library split in Part II: a tiny domain-free calculus, with the
  domain vocabulary defined *in* the language rather than *by* the interpreter.

### 3) Design principles (the cleanliness rules)

1. **Everything is an entity.** Agents, places, objects, and user-defined kinds
   share one model: typed state + tags + containment. No special cases at the
   data layer; "agent" and "place" are just kinds with extra built-in fields.
2. **All bindings are parameters.** `target`, `object`, `destination` are not
   special constructs — they are entity-valued parameters with domains.
   Conventional names get sugar, nothing more.
3. **Closed vocabulary, open seams.** Predicates, effects, domains, and
   templates come from a fixed op set — safe (no `eval`), statically
   typecheckable against the state schema, deterministic, and
   **GUI-isomorphic** (every construct maps to a form widget, which is what
   makes "design a backend visually" real). Escape hatches are explicit and
   ranked (§17).
4. **Deliberately not Turing-complete.** No recursion, no unbounded loops;
   `for_each` iterates only over bounded query results. Every action terminates
   by construction. Anything algorithmic climbs the escape-hatch ladder.
5. **Transactional by construction.** Validate all preconditions → apply all
   effects → log exactly one committed event row. Failures produce a message
   and no row — the committed-only `action_events.jsonl` contract is satisfied
   automatically.
6. **Replay-stable randomness.** All stochastic constructs draw from a RNG
   seeded by `(run_seed, step, actor, action, call_index)` — the same recipe as
   participation policies and probe sampling. Same seed → same world, always.
7. **The world models information access, not beliefs.** Field visibility,
   scoped messages, and `reveal` control *what reaches an agent*; what the
   agent believes lives in its memory stream (agent layer). Mind/body split:
   persona + memory = mind (agent layer); attributes, inventory, position,
   roles = body (world layer). Observations are the interface between them.
8. **Mechanisms, not scenarios.** The language contains zero scenario nouns.
   Every construct is an abstract structure — scopes, containment, edges,
   gates, timers, statuses, visibility predicates — that scenarios instantiate
   purely by naming and configuring. The acceptance test for any proposed
   feature: if it can't be stated without mentioning a concrete scenario
   ("werewolf needs...", "the market should..."), generalize it until the
   scenario disappears or push it to a pack/extension. Packs hold reusable
   *content*; the core language holds only *structure*.
9. **A small kernel, a large library.** Principle 8 applied to the language's
   own vocabulary: even "place", "holds", "visible", "status", and "role" are
   not primitives. The interpreter implements only the kernel calculus of
   Part II — entities/properties, relations-with-axioms, derived rules,
   guards, rewrites, deliveries, time, randomness, FFI — and every domain word
   is *defined in the language* as traits + relations + derived rules,
   shipped as replaceable profiles. The acceptance test for the kernel: if a
   surface construct can't be desugared to it, either the construct is wrong
   or the kernel is missing a primitive — fix whichever is broken, never
   special-case the interpreter.

---

## Part II — The kernel calculus

The surface constructs of Part III (places, holders, statuses, visibility)
read as specific. They are **not primitive**. The architecture is two-level:

- **Kernel** — what the interpreter implements: nine primitives, domain-free
  to the point of knowing nothing about space, sight, or possession.
- **Standard profiles (the stdlib)** — `spatial`, `containment`, `perception`,
  `social`, `temporal`: written *in the language* (traits + relations +
  derived rules + guards), shipped like action packs, replaceable per world.
- **Sugar** — the friendly syntax of Part III (`places:`, `holds:`,
  `contents_visible:`). The loader desugars it into kernel constructs; power
  users write kernel-level directly to redefine semantics.

This is the small-core pattern (Lisp/GHC Core/OWL/Datalog, §2): the kernel
guarantees you never hit a wall; the sugar and profiles are the product
surface most authors — and the GUI — actually touch.

### §K1 The nine primitives

1. **Entities** — id + kinds/traits + typed **properties**. That's the whole
   object model; tags are just boolean properties.
2. **Relations** — typed, directed edges that may carry their own properties
   (edge state), declared with **axioms** that the kernel enforces:
   `functional` (≤1 out-edge per source), `symmetric`, `acyclic`,
   `inverse: <name>`, `exclusive_with: [...]`. Position, possession,
   membership, sociality, affliction, ownership are ALL relations —
   `located_in`, `contained_in`, `member_of`, `holds_role`, `afflicted_by`,
   `follows`, `owns` — distinguished only by name and axioms.
3. **Derived definitions** — named predicates/relations/values computed by
   stratified rules over entities+relations. No general recursion; the one
   recursive form allowed is transitive closure over `acyclic` relations
   (written `contained_in+`), so evaluation is guaranteed total.
   `co_located`, `perceives`, `reachable` are derived — never built in.
4. **Guards** — declarative interceptors: `deny`/`allow` rules attached to
   traits, kinds, or action tags, evaluated with every matching action's
   preconditions. Locks, bans, permissions, capacity limits, and terminal
   states are all guards; none needs a bespoke mechanism.
5. **Actions** — guarded atomic **graph rewrites**: parameter binding (domains
   are derived-relation queries) + guards + a bounded list of rewrite ops.
   The effect vocabulary of Part III is exactly the edit language of the
   entity-relation graph, nothing more.
6. **Deliveries** — the single agent-facing output: text routed to a recipient
   set given by a query. Scopes (`place`, `channel:x`, `witnesses`) are named
   recipient queries; the exposure log is the delivery ledger.
7. **Time** — the step counter, timer queues (delayed rewrites), triggers
   (event-conditioned rewrites), and TTL decay on properties/edges.
8. **Randomness** — seeded draws (§8.4).
9. **FFI** — the typed extension points of §13, the boundary to code.

Everything in Part III is these nine primitives plus names.

### §K2 Traits and inheritance

Two composition mechanisms, both closed under the kernel:

- **Kind hierarchy** — `kind: chest extends furniture`: single-parent is-a
  taxonomy inheriting property schema, traits, affordances, and guard
  applicability.
- **Traits (mixins)** — the workhorse: a named bundle of
  `{properties, relation participations, derived-rule contributions, guards,
  affordances}` composed onto any kind (`traits: [container, lockable,
  portable, flammable]`). Composition is order-independent; conflicting
  property declarations are load errors. A trait is the ECS "component +
  its systems" in declarative form.

```yaml
traits:
  lockable:
    properties: {locked: {type: bool, default: false}}
    relations: {unlocked_by: {to: entity}}          # what counts as a key
    guards:
      - deny:
          action_tags: [open, search, take_from, traverse]
          when: {prop: [self, locked]}
          unless: {exists: {related: [actor, holds+, self.unlocked_by]}}
          message: "It's locked."
```

Anything composed with `lockable` — a chest, a door (connection), a diary, a
*channel* — gets locking semantics identically. This is the fully generic form
of "rules at a location": **rules attach to traits; entities compose traits;
locations are just entities.** The `locked_when` fields on connections and
holders in Part III are sugar for exactly this guard.

### §K3 Desugaring — watching the specific terms dissolve

| Surface construct (Part III) | Kernel definition |
|---|---|
| `place` | a kind with trait `spatial_scope`; `connects` = `connected_to` relation (symmetric unless `one_way`) whose edge properties are the §6.2 table |
| agent position | `located_in` relation, `functional` |
| movement | an action rewriting `located_in`, guarded by `connected_to` + that edge's guards |
| `holds:` block / "holders" | trait `container`: participation in `contained_in` (`acyclic`, `functional`) + properties `capacity`, `opaque`, `surface` |
| inventory | `contained_in` where the container is an agent |
| `contents_visible` | a rule contribution to the derived relation `perceives` (below) |
| channels | a kind with trait `scope`; `member_of` relation, non-functional |
| statuses | a `status` kind + `afflicted_by` edge carrying a TTL property + an expiry trigger |
| roles | `holds_role` relation to role entities; `available.roles` is a guard |
| tags | boolean properties |
| field `visibility:` | per-property rules on derived `perceives_property(observer, entity, prop)` |

The centerpiece — **perception is library code, not interpreter code**. The
default `perception` profile defines one derived relation that every
observation renderer and visibility-aware query consults:

```yaml
derived:
  perceives:                 # observer -> entity
    base:                    # ways of coming to perceive
      - {when: {co_located: [observer, x]}}
      - {when: {related: [x, contained_in+, observer]}}      # you see what you carry
    blockers:                # ways perception is defeated
      - {when: {exists: {via: contained_in+, above: x,
                where: {and: [{prop: [node, opaque]}, {not: {prop: [node, surface]}}]}}}}
    overrides:               # ways blockers are pierced
      - {when: {has_role: [observer, security]}}
```

A world wanting different information physics — sound as a separate channel
(`perceives_audio` with different blockers), darkness, telepathy ranges,
x-ray roles, smell — **edits these rules**, not the interpreter. Likewise a
world can add wholly new actor-attached constructs (reputation ledgers,
licenses, debts) as trait + relation + derived-rule bundles with zero new
mechanism.

Practical stance: the kernel is the wrong authoring level for most users and
the wrong rendering level for the GUI — nobody should write `located_in`
axioms to make three rooms. Part III's sugar and profiles are the product;
the kernel is the guarantee behind them. The interpreter implements the
kernel once and may fast-path hot profile queries (`co_located`) as pure
optimization, never as semantics.

---

## Part III — The surface language (standard profiles + sugar)

A world spec is one document (`world_def`) with these top-level sections:

```yaml
world:
  name: ...
  version: 1                 # spec version stamped into checkpoints
  profiles: [spatial, containment, perception, social]   # stdlib defaults (§K3); omit = all
  traits: {...}              # kernel level (§K2) — optional; profiles ship the common ones
  derived: {...}             # kernel level (§K3) — optional; e.g. redefine `perceives`
  guards: [...]              # kernel level (§K1.4) — world-wide deny/allow rules
  state: {...}               # Layer 0: world-global typed fields
  kinds: {...}               # Layer 0: user-defined entity kinds (optional)
  agent_state: {...}         # Layer 0: per-actor typed fields
  roles: {...}               # actor roles / permissions
  statuses: {...}            # timed condition definitions
  relationships: {...}       # typed edge definitions
  places: {...}              # Layer 1: spatial scopes
  channels: {...}            # Layer 1: non-spatial scopes
  object_types: {...}        # Layer 3: object kinds + affordances
  objects: [...]             # Layer 3: instances
  generate: {...}            # Layer 3.5: parametric instantiation at scale (§12.4)
  action_packs: [...]        # Layer 4: composition
  actions: {...}             # Layer 2: world-level actions
  extensions: {...}          # the code FFI table (§13)
  dynamics: [...]            # Layer 5: ticks, triggers, delayed effects
  observation: {...}         # Layer 6: what agents see
  setup: {...}               # initialization (placement, graphs, roles)
  metrics: {...}             # derived values logged per step
  end_conditions: [...]      # terminal-state detection
```

### 4) Layer 0 — Entities and state

#### 4.1 Field schema (used everywhere state is declared)

```yaml
<field_name>:
  type: int | float | str | bool | enum | list | dict | entity | duration
  default: <value>                # required unless optional: true
  # constraints (all optional, type-appropriate):
  min: 0            # int/float
  max: 100
  values: [a, b]    # enum
  max_len: 280      # str / list
  pattern: "^[a-z]+$"             # str (regex)
  of: {type: ...}   # list element / dict value schema
  kind: object      # entity refs: which kind they may point at
  # information access (see §11):
  visibility: public | owner | role:<name> | hidden   # default public
  # documentation (feeds GUI + LLM authoring):
  description: "..."
```

`entity` fields hold references (by id) to other entities — this is how
ownership, assignment, "the key that opens this door", etc. are expressed.
`duration` is an int counted down by the engine tick (used by statuses/timers).

#### 4.2 Built-in kinds and their built-in fields

Every entity has: `id`, `kind`, `name`, `description`, `tags` (set of strings),
`in` (containment: the place/agent/object that holds it), plus its declared
fields.

| Kind | Extra built-ins | Notes |
|---|---|---|
| `agent` | `place` (alias of `in`), `roles`, `statuses`, `inventory` (entities contained in the agent) | one per simulation actor; created by the runtime, never by effects |
| `place` | `connects` (edges, see §6), `capacity`, `members` (derived) | spatial scope |
| `object` | `holder` (derived from `in`) | interactible things |
| `channel` | `members` | non-spatial scope (§6.3) |
| custom kinds | none | declared under `kinds:` — e.g. `faction`, `contract`, `proposal`, `task`. A custom kind is a full entity: state, tags, lifecycle via `create_entity`/`destroy_entity`, referencable by `entity` fields, targetable by actions via domains. This is the catch-all that keeps the language generic: anything that isn't a person or a location is *some* kind with state |

```yaml
kinds:
  contract:
    state:
      buyer:  {type: entity, kind: agent}
      seller: {type: entity, kind: agent}
      price:  {type: int, min: 0}
      status: {type: enum, values: [open, fulfilled, void], default: open}
```

#### 4.3 Tags

Free boolean markers on any entity (`alive`, `flammable`, `vip`, `locked`).
Checkable (`has_tag`), grantable/revocable by effects, usable as availability
filters and domain filters. Tags are the cheap half of the type system: use a
field when the value matters, a tag when only presence matters.

### 5) The actor model — what agents can have in the world

The user-facing answer to "can actors have features/properties?": yes, seven
distinct kinds of actor-attached state, each with its own construct because
each behaves differently:

| Construct | What it is | Declared in | Changed by | Checked by |
|---|---|---|---|---|
| **Attributes** | typed per-actor fields (coins, hp, reputation, suspicion) | `agent_state` | `set/inc/dec` effects | any predicate/formula |
| **Tags** | boolean markers (alive, informed, vip) | granted at setup or by effects | `add_tag/remove_tag` | `has_tag` |
| **Roles** | named permission bundles gating actions (§5.1) | `roles:` + `setup.roles` | `grant_role/revoke_role` | `has_role`, action `available.roles` |
| **Statuses** | *timed* conditions with optional stat modifiers (§5.2) | `statuses:` | `apply_status/remove_status`, auto-expiry | `has_status` |
| **Inventory** | objects contained in the actor | containment | `transfer/move_entity` | `holds`, `held_objects` domain |
| **Relationships** | typed stateful edges to other agents/entities (§5.3) | `relationships:` | `set_relation/adjust_relation/remove_relation` | `related`, relation-valued domains |
| **Position & membership** | current place, channel memberships | containment / `members` | `move_entity`, `join_channel/leave_channel` | `same_place`, `in_channel`, scoped domains |

Kernel view (§K3): none of these seven is primitive — attributes are
properties, tags boolean properties, roles a `holds_role` relation, statuses
TTL-bearing `afflicted_by` edges, inventory containment, relationships plain
typed edges. The table is the standard profile's *curated vocabulary*, chosen
because these seven shapes recur across scenario families; a world needing an
eighth (reputation ledgers, licenses, debts, callings) defines it as a
trait + relation + derived-rule bundle with zero interpreter changes.

#### 5.1 Roles

```yaml
roles:
  merchant: {description: "May run a stall and set prices."}
  mayor:    {description: "May call votes and enact results.", max_holders: 1}
```

Roles gate action availability (`available.roles: [mayor]`) and field
visibility (`visibility: role:doctor`). They bridge to the persona pipeline:
`setup.roles.by_class` maps `persona_pipeline` classes / `sim_role` names to
world roles, so casting is config-only.

#### 5.2 Statuses (timed conditions)

```yaml
statuses:
  infected:
    duration: 4                 # steps; omit for indefinite
    modifiers: {charisma: -2}   # applied on apply, reverted on expiry/removal
    on_expire:                  # effects fired when it runs out
      - {add_tag: {entity: actor, tag: recovered}}
    messages: {actor: "You feel feverish.", place: "{actor.name} looks unwell."}
```

Statuses are the construct for "temporarily different": injuries, buffs,
infections, jail time, being on-shift. Auto-expiry runs in the engine tick;
`on_expire` effects make state machines out of them.

#### 5.3 Relationships (typed stateful edges)

First-class edges — the construct social scenarios live on:

```yaml
relationships:
  friendship: {directed: false, state: {strength: {type: int, default: 0, min: -10, max: 10}}}
  employment: {directed: true,  state: {wage: {type: int, default: 5}}}   # employer -> employee
  owns:       {directed: true, to_kind: object}
```

Edges carry their own typed state, support predicates
(`{related: {from: actor, to: target, type: friendship, where: {gte: [edge.strength, 3]}}}`),
domains (`related_agents(type=friendship)`), and effects
(`adjust_relation` for "this interaction moved trust by +1"). `setup` can
generate initial graphs with the same generators the follow-graph already uses
(`barabasi_albert`, `random`, `full`, explicit list).

### 6) Layer 1 — Space and scopes

#### 6.1 Places

```yaml
places:
  town_square: {description: "The busy heart of town.", connects: [tavern, market], tags: [outdoor]}
  tavern:      {description: "Dim, loud, smells of stew.", connects: [town_square], capacity: 6}
  vault:
    description: "A reinforced room."
    connects:
      - {to: bank_lobby, locked_when: {not: {holds: [actor, vault_key]}},
         locked_message: "The vault door doesn't budge."}
```

#### 6.2 Connections

Edges are bidirectional by default; each side may declare:

| Property | Meaning |
|---|---|
| `to` | destination place |
| `one_way: true` | no implicit reverse edge |
| `locked_when: <predicate>` | movement gate, evaluated per actor (keys, roles, time-of-day) |
| `locked_message` | shown on a refused move |
| `cost: {path, amount}` | resource cost of traversal (stamina, fare) |
| `travel_steps: N` | multi-step travel: actor gets `in_transit` status for N steps (built on statuses — no new machinery) |
| `hidden_when: <predicate>` | edge not shown in observations until condition met (secret passages) |

Connections are addressable in effects via `connection(a, b).field`, so
"the earthquake blocks the road" is `{set: {path: connection(town, mine).blocked, value: true}}`
with `locked_when: {path: connection.blocked}`.

#### 6.3 Channels — non-spatial scopes

The generalization that frees the language from physical space. A channel is
membership + message scope with no geometry: a group chat, a radio frequency,
an org-wide mailing list, a faction's war council, "the press".

```yaml
channels:
  wolf_den:  {description: "Secret channel for werewolves.", joinable: false}
  town_crier: {description: "Announcements everyone hears.", broadcast_only_roles: [mayor]}
```

Members receive `channel`-scoped messages; `in_channel` predicates and
`agents_in_channel` domains work like their place equivalents. Purely social
scenarios (an online community, a distributed org) can be all channels and no
places; purely spatial ones the reverse. Places and channels are the same
mechanism (scopes) with one difference: place membership is exclusive
(you are in one place), channel membership is not.

#### 6.4 Fine-grained position — holders, surfaces, spots

Position *within* a place ("in the basket", "on the shelf", "under the bed")
is modeled by **recursive containment**: any object type may declare a
`holds:` block, making its instances holders that other entities can be placed
in or on. "Locations inside a room" need no new construct — a hiding spot is a
`fixed: true` holder object, a full entity with state, tags, and affordances,
so **per-location rules are ordinary predicates on the holder**.

```yaml
object_types:
  basket:
    fixed: true                  # cannot be picked up or relocated
    state: {lid: {type: enum, values: [open, closed], default: closed}}
    holds:
      mode: inside               # inside | surface
      capacity: 4
      contents_visible: {eq: [holder.lid, open]}
      # a bool for simple cases, or a per-observer predicate — bindings
      # `holder` and `observer` are in scope, so one basket can be
      # transparent to a security role and opaque to everyone else:
      # contents_visible: {any: [{eq: [holder.lid, open]},
      #                          {has_role: [observer, security]}]}
      searchable: true           # a `search` affordance reveals contents to the searcher
      locked_when: {not: {holds: [actor, basket_key]}}   # gates put/take/search
```

Semantics:

- `mode: surface` → contents render in place observations whenever the holder
  is visible; `mode: inside` → contents render only when `contents_visible`
  passes for that observer.
- Moving objects between holders is the ordinary `transfer` effect; the
  `inventory` pack's `put`/`take`/`search` actions are holder-aware sugar.
- Containment is recursive (marble → box → chest → room) and visibility
  **composes down the chain**: a visible holder inside a concealed holder is
  still concealed. Depth is bounded by the spec, so evaluation stays cheap.
- Because holders are entities, location rules can be arbitrarily rich:
  visible only when the room is lit (`{eq: [place.lit, true]}`), only from an
  adjacent spot, only to the agent who hid it (via a `hidden_by` entity field),
  reachable only while standing on the chair (a `require` on the affordance).

Kernel view (§K3): "holder" is not a primitive term — the `holds:` block
desugars to the `container` trait (participation in the `contained_in`
relation + `opaque`/`surface`/`capacity` properties), `locked_when` to a
`lockable`-style guard, and `contents_visible` to a rule contribution on the
derived `perceives` relation. The sugar exists because this bundle recurs
constantly, not because the interpreter knows what a basket is.

### 7) Layer 2 — The action specification (full field reference)

Every action — world-level, pack-provided, or object affordance — is one
record. Complete field list:

```yaml
actions:
  <action_name>:
    # ---- identity ----
    kind: movement | agent_interaction | object_interaction |
          object_creation | communication | read | generic | custom-tag
    description: "One or two sentences the model sees in the tool schema."
    aliases: [alt_name]              # feeds the existing action-alias machinery
    extends: <abstract_action>       # inherit + override fields (packs use this)
    abstract: true                   # template only; not instantiated

    # ---- availability (who / where / when this action EXISTS for an actor) ----
    available:
      in: [tavern, tag:outdoor]      # place ids or tags; omit = anywhere
      channels: [wolf_den]           # only for members of these channels
      roles: [merchant]              # actor must hold one of these roles
      when: {eq: [world.day_phase, day]}   # arbitrary predicate gate
      cooldown: 3                    # per-actor steps between uses
      max_uses: 1                    # per-actor per-run budget
      per_step: 2                    # per-actor per-step budget
    # Availability controls the actor's action *surface* (catalog/tool schemas);
    # `require` (below) controls whether an attempted call commits. Rule of
    # thumb: availability for what the actor shouldn't even see; require for
    # what the actor may try and fail.

    # ---- parameters (all bindings are parameters; see principle 2) ----
    params:
      target:                        # conventional name; nothing special
        type: entity
        domain: agents_in_same_place # dynamic, resolved per actor per turn (§8.3)
        description: "Who to give coins to."
      amount:
        type: int
        min: 1
        description: "How many coins."
      note:
        type: str
        max_len: 200
        optional: true

    # ---- validation (checked atomically before any effect) ----
    require:
      - {gte: [actor.coins, params.amount]}
      - pred: {not: {has_status: [params.target, jailed]}}
        fail_message: "{params.target.name} is in jail and can't receive anything."
    cost: [{path: actor.coins, amount: params.amount}]
      # sugar for require(gte) + dec; keeps resource spends declarative & auditable

    # ---- resolution: EITHER flat effects (deterministic) ...
    effects:
      - {inc: {path: params.target.coins, by: params.amount}}
      - {adjust_relation: {type: friendship, from: params.target, to: actor, field: strength, by: 1}}

    # ---- ... OR branching outcomes (conditional / stochastic) ----
    outcomes:
      - when: {gte: [actor.skill_lockpicking, 5]}   # first matching branch wins
        effects: [...]
        messages: {actor: "The lock clicks open."}
      - chance: {mul: [0.1, actor.skill_lockpicking]}   # seeded roll (§8.4)
        effects: [...]
        messages: {actor: "Against the odds, it opens."}
      - default: true                # fallthrough
        committed: false             # a NON-committing outcome: no state change,
        messages: {actor: "The lock holds.", place: "{actor.name} fumbles at the lock."}
        # no event row — "tried and failed" (matches the committed-only contract;
        # omit `committed:false` to make failure itself a committed world event)

    # ---- communication (observation routing, §11) ----
    messages:
      actor:  "You give {params.amount} coins to {params.target.name}."
      target: "{actor.name} gives you {params.amount} coins."
      place:  "{actor.name} hands something to {params.target.name}."
      # other scopes: channel:<name>, adjacent, global,
      # witnesses: {when: <predicate over each observer>, text: "..."}
    observe: |                       # for kind:read — template returned to actor
      Stall prices today: {object.prices}

    # ---- logging & analytics ----
    event_label: gave_coins          # action_events.jsonl label (default: action name)
    record: [params.amount]          # extra fields copied into the event row's data
```

**Notes on the resolution model:**

- `effects` and `outcomes` are mutually exclusive; `effects` ≙ a single
  always-matching outcome.
- Outcome selection: evaluate branches top-down; `when` predicates and
  `chance` rolls; exactly one branch fires (`default` catches the rest; if no
  branch matches and there is no default, the action is refused with a message
  — attempted, not committed).
- Object affordances are these same records nested under
  `object_types.<type>.affordances`, with an implicit `object` binding (the
  instance) and an implicit `require: same_place(actor, object) or holds(actor, object)`.
- **Per-actor action surface** = actions whose `available` gate passes ∧ whose
  required entity-domains are non-empty. This keeps prompts small in rich
  worlds and is what actor-aware catalogs/schemas expose (§16).

### 8) The expression language (predicates, values, queries, randomness)

All expressions are structured YAML (no eval'd strings) so they are safe,
typecheckable at load time against the declared schemas, and form-renderable.

#### 8.1 Value expressions

```
<value> := literal (int/float/str/bool)
         | <path>                          # actor.coins, params.amount, world.step,
                                           # object.prices, edge.strength,
                                           # entity(<id>).field, relation(a,b,type).field,
                                           # connection(a,b).field
         | {add|sub|mul|div|mod|abs|min_of|max_of: [<value>, ...]}
         | {concat: [<value>...]}          # strings
         | {count: <domain>}
         | {sum|avg|min|max: {of: <domain>, value: <path-on-element>}}
         | {pick: {from: <domain>}}        # seeded random selection (§8.4)
         | {pick: {from: <domain>, weight: <path-on-element>}}
         | {fn: <declared extension>, args: {...}}   # code-defined value (§13)
```

Built-in paths: `world.step`, `world.<field>`, `actor.*`, `params.*`, and — in
the appropriate contexts — `target.*`, `object.*`, `place.*` (actor's place),
`edge.*` (inside relation predicates), `element.*` (inside `for_each`/queries).

#### 8.2 Predicates

```
<pred> := {eq|ne|gt|gte|lt|lte: [<value>, <value>]}
        | {in: [<value>, <list-value>]} | {contains: [<list-path>, <value>]}
        | {is_empty: <domain-or-list>} | {exists: <domain>}
        | {all|any: [<pred>...]} | {not: <pred>}
        # spatial / structural sugar:
        | {same_place: [<entity>, <entity>]}
        | {holds: [<entity>, <entity-or-object-type>]}
        | {has_tag: [<entity>, <tag>]} | {has_role: [<entity>, <role>]}
        | {has_status: [<entity>, <status>]}
        | {in_channel: [<entity>, <channel>]}
        | {related: {from: <e>, to: <e>, type: <rel>, where: <pred-over-edge>}}
        # escape hatches (§13):
        | {fn: <declared bool-returning extension>, args: {...}}
        | {class_path: mypkg.preds.is_solvent, params: {...}}   # inline shorthand; prefer the FFI table
```

#### 8.3 Domains (dynamic parameter/query ranges)

A domain names a set of entities, resolved per actor per turn:

| Domain | Yields |
|---|---|
| `agents_in_same_place` / `agents_in(<place>)` / `agents_in_channel(<ch>)` / `agents_anywhere` | actors |
| `adjacent_places` / `reachable_places` / `places_tagged(<tag>)` | places |
| `objects_here` / `held_objects` / `objects_of_type(<t>)` / `entities_of_kind(<k>)` | objects / custom entities |
| `related_agents(type=<rel>, where=<pred>)` | edge-connected actors |
| `contents_of(<holder>)` / `holders_here` | fine-grained position (§6.4); `contents_of` is visibility-aware when given an observer |
| `filter: {of: <domain>, where: <pred-over-element>}` | composable refinement |
| a declared domain extension by name, or `{class_path: ...}` | code-defined resolver (§13) — line-of-sight, pathfinding-reachable, "top 3 by wealth" |

Domains appear in three positions with one meaning: parameter ranges (what the
model may choose — enum-inlined into tool schemas when actor-aware schemas
land, resolve-time validated until then), query ranges (`count`/`sum`/`pick`),
and iteration ranges (`for_each`).

#### 8.4 Randomness (replay-stable)

`chance:` rolls and `pick:` draws use an RNG seeded by
`(run_seed, step, actor, action_name, draw_index)` — the exact recipe
participation policies and probe sampling already use. Same config + seed →
identical world trajectory; checkpoints don't need to serialize RNG state.

### 9) Effects — the full op vocabulary

| Group | Ops | Semantics |
|---|---|---|
| **State** | `set`, `inc`, `dec`, `append`, `remove`, `clear` | typed mutation of any addressable path; constraint-clamped (`min`/`max`) with a declared policy (`clamp` default, or `fail` to abort the action) |
| **Containment & space** | `move_entity {entity, to}`, `transfer {entity, from, to}` | placement, pickup/drop/give, put-into/take-from holders (§6.4); capacity and `locked_when` re-checked under the commit lock |
| **Lifecycle** | `create_entity {kind/type, id?, in, state}`, `destroy_entity {entity}` | runtime object/entity creation ("drop a note others can read", spawn a contract, mint a proposal). **Cannot create/destroy agents** — actors are runtime-owned; "death" is roles/tags/place (see werewolf, §14.1) |
| **Relationships** | `set_relation`, `adjust_relation`, `remove_relation` | typed-edge CRUD + numeric drift |
| **Actor constructs** | `add_tag`, `remove_tag`, `grant_role`, `revoke_role`, `apply_status {name, duration?}`, `remove_status` | §5 constructs |
| **Scopes** | `join_channel`, `leave_channel` | membership |
| **Control** | `when {pred, then: [ops], else: [ops]}`, `for_each {in: <domain>, as: element, do: [ops]}` | bounded branching/iteration only (principle 4) |
| **Time** | `schedule {in: N, effects: [...], messages: {...}}` | delayed effects on a world timer queue ("the letter arrives in 2 steps", "the bomb…"); fires in the engine tick; serialized in `get_state` |
| **Communication** | `emit_message {scope, text}`, `reveal {to: <scope/entity>, fields: [paths]}` | out-of-band observation routing; `reveal` grants one-shot visibility of otherwise-hidden fields (§11) |
| **Logging** | `emit_event {label, data}` | an extra committed event row (world-level bookkeeping) |
| **Escape** | `call {handler: <declared name>, args: {...}}` | invokes a registered effect handler (§13) — arbitrary Python, staged through the same op pipeline as declarative effects |
| **Adjudication (W4)** | `llm_resolve {prompt, allowed_ops}` | a narrator model returns effects **from the closed vocabulary**, which are then validated and applied normally — fuzzy actions ("try to persuade the guard") with a deterministic, logged footprint |

### 10) Dynamics — ticks, triggers, timers, invariants

The world acts on its own through four constructs (executed by a generic GM
`update` component and the timer queue):

```yaml
dynamics:
  # 1. Scheduled ticks — the world's heartbeat
  - every_n_steps: 2
    effects: [{set: {path: world.day_phase, value: night}}]
    messages: {global: "Night falls."}
  - at_step: 10
    effects: [{create_entity: {type: storm, in: town_square}}]

  # 2. Event triggers — reactions to committed actions
  - on_event: {label: gave_coins, where: {gte: [event.data.amount, 100]}}
    effects: [{add_tag: {entity: event.actor, tag: generous}}]
    # triggers fire AFTER the causing action commits, in the same step, at the
    # single-threaded step boundary — no reentrancy, no cascades beyond depth 1
    # (a trigger's effects do not fire further triggers; principle 4)

  # 3. Co-location / condition rules — "while X holds, do Y each step"
  - each_step_for: {domain: {filter: {of: agents_anywhere, where: {has_status: [element, infected]}}}}
    as: carrier
    effects:
      - for_each:
          in: {filter: {of: agents_in(carrier.place), where: {not: {has_status: [element, infected]}}}}
          as: contact
          do:
            - when:
                pred: {chance: 0.15}
                then: [{apply_status: {entity: contact, name: infected}}]

  # 4. Invariants — load- and commit-time assertions (catch spec bugs early)
invariants:
  - {gte: [{sum: {of: agents_anywhere, value: coins}}, 0]}
```

Plus the **timer queue** fed by `schedule` effects (§9). Note the deliberate
symmetry with the existing `interventions` schedule language — dynamics are
"interventions written by the world author," and the actual `interventions`
system still works on top for experimenter-side mid-run manipulation.

### 11) Observation and information access

Four mechanisms, composing:

1. **Field visibility** (`visibility:` in the schema, §4.1) — `public` fields
   render in observations of the entity; `owner` only to the entity itself
   (your own coins); `role:<r>` to holders of a role (the doctor sees
   `infected`); `hidden` never renders (werewolf's true role) until `reveal`ed.
2. **Holder visibility** (§6.4) — whether a container's contents render is a
   bool or a **per-observer predicate** (bindings `holder`, `observer`), and it
   composes down the containment chain. This is spatial information asymmetry:
   the same basket is opaque to most, transparent to the security role, open
   once the lid is open.
3. **Scoped messages** — every action/dynamic routes text to `actor`, `target`,
   `place`, `channel:<name>`, `adjacent`, `global`, or predicate-filtered
   `witnesses`. Messages land in per-agent queues.
4. **Observation template** — assembles each agent's per-turn observation:

```yaml
observation:
  sections:
    - "Step {world.step}, {world.day_phase}. You are in {place.name}. {place.description}"
    - {list: agents_in_same_place, header: "Also here:", item: "- {name}{roles_public}"}
    - {list: objects_here, header: "You notice:", item: "- {name}: {description}"}
    - {list: adjacent_places, header: "Exits:", item: "- {name}"}
    - {self: [coins, inventory, statuses]}      # owner-visible state
    - {queue: messages}                          # drained scoped messages
    - {when: {has_role: [actor, mayor]}, text: "Petitions pending: {count: entities_of_kind(petition)}"}
```

Kernel view (§K3): all four mechanisms are one construct — rule contributions
to the derived relations `perceives(observer, entity)` and
`perceives_property(observer, entity, prop)`, which every renderer and
visibility-aware query consults. The four-way split is the standard
`perception` profile's authorable packaging; a world may redefine the
information physics wholesale (separate sensory channels, darkness, ranges)
by editing the derived rules, with no interpreter involvement.

**Exposure ground truth.** Every observation delivery — scoped messages,
rendered sections, `reveal`s, `search` results — appends a row to a per-agent
exposure log (`exposure_events.jsonl`, riding the same logger family as
`action_events.jsonl`; the runtime already stamps an exposure logger alongside
the action logger). The world therefore knows **exactly what information
reached whom, and when**. That makes information-asymmetry measurement
mechanical: a probe answer can be scored against world truth *and* against
what the agent was actually shown — the difference is precisely a false
belief (§14.2).

What is **deliberately not modeled**: agent beliefs/knowledge-bases. The world
controls information *flow*; remembering and reasoning about it is the agent
layer's job (memory streams already do this). This keeps the world layer
mechanical and the cognitive layer where personas live. The exposure log is
the bridge: it records what the mind was given without presuming what it kept.

### 12) Setup, packs, composition, metrics, termination

#### 12.1 Setup (initialization)

```yaml
setup:
  placement: {strategy: fixed | random | by_role, assignments: {mayor: town_hall}, default: town_square}
  roles:
    by_class: {merchants: [merchant]}     # persona_pipeline class -> world roles
    by_count: {werewolf: 2}               # seeded random assignment
  relationships:
    - {type: friendship, generator: {built_in: barabasi_albert, params: {m: 2}}}
  agent_state_overrides:
    by_role: {merchant: {coins: 50}}
  objects: inherited from `objects:`; setup may add per-agent starting inventory:
  inventory: {by_role: {guard: [{type: key, id_prefix: guard_key}]}}
```

Graph generators reuse the follow-graph generator family (already
deterministic and sorted for cross-process stability).

#### 12.2 Action packs and inheritance

Packs are YAML fragments (state + actions + object types + statuses) merged
Hydra-style; `extends`/`abstract` let packs ship templates that worlds
specialize. Shipped packs: `movement` (move/look/where_am_i), `conversation`
(say/whisper/shout), `inventory` (take/drop/give/put/search — holder-aware,
§6.4), `trade` (offer/accept — built on a `contract` kind), `board` (post/read
— the proto-social-platform), `voting` (call_vote/cast_vote/tally — built on a
`proposal` kind). Load order: packs (in list order) → world sections →
validation of the merged whole.

#### 12.3 Metrics and end conditions

```yaml
metrics:                       # evaluated per step -> sim metrics / dashboard
  total_coins: {sum: {of: agents_anywhere, value: coins}}
  infected_count: {count: {filter: {of: agents_anywhere, where: {has_status: [element, infected]}}}}
end_conditions:
  - {when: {eq: [{count: {filter: {of: agents_anywhere, where: {has_tag: [element, alive]}}}}, 0]},
     label: extinction}
```

End conditions mark the world terminal (recorded as an event + metric; every
subsequent action is refused with a terminal message). Actually *halting the
run loop* is an engine integration point — the loop strategy can poll a
backend `is_terminal()` seam, or the run simply plays out its configured
steps in the terminal state. MVP: record + refuse; loop integration in W3.

#### 12.4 Generative instantiation (`generate`)

Rich worlds shouldn't require hand-writing hundreds of instances. `generate`
is **pure macro expansion at load time**: rules expand into ordinary
`places`/`objects`/entity instances *before* validation, so validators see the
concrete world and errors report the generating rule. Interpolation variables:
`{i}` (1-based index) and, in cross-products, the enclosing entity (`{place}`).

```yaml
generate:
  places:
    - id: "room_{i}"
      count: 10
      template: {description: "Room {i}.", tags: [room], connects: [hallway]}
  objects:
    - id: "{place}_spot_{i}"                 # cross-product: 10 spots × 10 rooms
      count: 10
      in: {each: places_tagged(room)}
      template:
        type: hiding_spot
        state: {concealment: {pick_from: [open_surface, opaque, opaque, locked]}}
    - id: "{place}_trinket_{i}"
      count: 10
      in: {each: places_tagged(room)}
      template: {type: trinket, state: {color: {pick_from: [red, blue, green, gold]}}}
  relationships:
    - {type: rivalry, count: 8, between: {pick_pairs: agents_anywhere}}
```

- `pick_from` / `pick_pairs` draw from the seeded RNG
  (`(run_seed, "setup", rule_index, i)`) — generated worlds are reproducible
  and sweep-able like any config (`world_def` overrides can change counts from
  the CLI or a study grid).
- Agent *count* stays runtime-owned (`num_agents`, persona classes); generators
  only distribute world-side agent state, roles, placement, and edges.
- Deliberate limit: `generate` does interpolation and cross-products, nothing
  else. Structured topology (mazes, terrain, org charts) is a **setup builder**
  — code that programs the same builder API the loader uses (§13).

### 13) The code extension system — the world FFI

Maximal expressivity requires code sometimes: a matching algorithm, a
line-of-sight computation, a bespoke bridge to something outside the world.
Those code chunks need structure of their own — otherwise "escape hatch"
degenerates into "scatter `class_path`s until the spec is unreadable." Three
decisions give them that structure:

1. **No inline code in YAML.** Code lives in real Python files — testable,
   lintable, diffable. A scenario ships its config plus an optional
   `scenarios/<name>/world_ext.py` (imported the same way custom agents,
   routers, and providers already are).
2. **One declaration table.** Every code chunk is registered once, by name,
   in the spec's `extensions:` section — the world's FFI table. The rest of
   the spec references extensions **by declared name** (`{fn: clearing_price}`,
   `{call: {handler: settle_market}}`, a domain slot naming
   `line_of_sight_agents`), so the YAML stays readable and the GUI can render
   "this action calls `clearing_price`" without parsing Python.
3. **One SDK.** All extensions program against two objects — `WorldView`
   (read) and `WorldTxn` (staged writes) — the stable API contract between
   the interpreter and user code.

#### 13.1 The declaration table

```yaml
extensions:
  functions:        # pure values, usable in ANY expression as {fn: ...}
    clearing_price:
      class_path: world_ext.clearing_price
      params: {method: double_auction}     # bound at load — the Router pattern
      returns: int
      deterministic: true
  predicates:       # bool functions, usable in require/when/where/gates
    is_solvent: {class_path: world_ext.is_solvent}
  domains:          # entity-set resolvers, usable anywhere a domain goes
    line_of_sight_agents: {class_path: world_ext.line_of_sight}
  handlers:         # effect handlers, invoked via {call: {handler: ...}}
    settle_market:
      class_path: world_ext.settle_market
      writes: [agent.coins, entity(order).status]   # optional declared footprint
      deterministic: true
  renderers:        # custom observation sections: {section: minimap}
    minimap: {class_path: world_ext.render_minimap}
  systems:          # per-step code hooks (ECS-style), scheduled with dynamics
    npc_logic: {class_path: world_ext.step_npcs, every_n_steps: 1}
  actions:          # whole actions in code (descriptor + execute)
    solve_cipher: {class_path: world_ext.SolveCipherAction}
  setup_builders:   # programmatic construction at load time (mazes, org charts)
    maze: {class_path: world_ext.build_maze, params: {width: 8, height: 8}}
  adjudicators:     # llm_resolve narrators (W4)
    narrator: {class_path: world_ext.Narrator}
```

Nine extension points — each a plain callable or small class (structural
protocol, no base-class requirement), mirroring the `Router` seam:

| Point | Signature | May mutate? | Typical use |
|---|---|---|---|
| function | `(world: WorldView, ctx: EvalCtx, **args) -> value` | no | prices, scores, distances, derived stats |
| predicate | `(world, ctx) -> bool` | no | solvency, eligibility, geometric checks |
| domain | `(world, actor: EntityView) -> Sequence[EntityRef]` | no | line-of-sight, pathfinding-reachable, top-K queries |
| handler | `(txn: WorldTxn, ctx, **args) -> None` | staged | market clearing, cascade resolution, external bridges |
| renderer | `(world, actor) -> str` | no | maps, tables, bespoke observation sections |
| system | `(txn: WorldTxn, step: int) -> None` | staged | NPC logic, physics-ish updates, batch maintenance |
| action | `.descriptor() -> ActionDescriptor` + `.execute(txn, ctx) -> str` | staged | algorithmic validation (puzzles, proofs) |
| setup builder | `(builder: WorldBuilder, **params) -> None` | build-time | generated topology beyond `generate`'s macros |
| adjudicator | `(world, ctx, prompt) -> list[Op]` | via ops | LLM-narrated fuzzy actions |

`EvalCtx` is a frozen context: actor, bound params/target/object, step, the
triggering event (in triggers), and a **seeded rng** — extension code never
touches `random`/time directly, preserving principle 6.

#### 13.2 The SDK — `WorldView` / `WorldTxn`

`WorldView` (read): `entity(id)`, `entities(kind=..., tag=..., where=...)`,
`agents_in(scope)`, `get(path)`, `holder_of(e)`,
`contents(e, observer=None)` (visibility-aware, §6.4/§11), `related(a, type)`,
`connections(place)`, `rng(ctx)`, `count_committed_events(...)` (passthrough
to the existing backend mirror).

`WorldTxn` (extends the view) — and this is the load-bearing design decision:
**code mutates by emitting the same ops the YAML language uses.**
`txn.apply({"inc": {"path": ..., "by": ...}})`, with typed sugar
(`txn.inc(path, by)`, `txn.move(entity, to)`, `txn.create(kind, **state)`,
`txn.message(scope, text)`, `txn.emit_event(label, data)`). Raising
`ActionRefused("msg")` aborts the whole action cleanly (attempted, not
committed). Nothing lands until the action's atomic commit.

Why op-emitting matters: there is **exactly one write path**. Code-driven
mutations flow through the same constraint clamping, visibility bookkeeping,
message routing, committed-event logging, and checkpoint state as declarative
effects — so replay, checkpoints, analytics, the exposure log, and the
dashboard's "explain this action" panel cannot tell the difference. Direct
attribute pokes are simply not offered by the API.

#### 13.3 Determinism annotations and validation

`deterministic: true` is the default contract (same world+ctx → same result).
The load-time validator **warns** when a nondeterministic extension sits in a
`require`, domain, or availability gate (replay hazard: re-evaluation could
diverge) and **allows** it in handlers/systems (their emitted ops are what's
logged and checkpointed, so restore stays exact regardless). Declared
`reads`/`writes` footprints are optional but enable static conflict checks
and let the GUI show what a handler touches.

#### 13.4 The hybrid pattern (the point of all this)

The intended shape is *declarative frame, code kernel* — rules of engagement
stay inspectable data; only the algorithm is code:

```yaml
kinds:
  order: {state: {owner: {type: entity, kind: agent}, side: {type: enum, values: [buy, sell]},
                  price: {type: int, min: 1}, status: {type: enum, values: [open, filled], default: open}}}
actions:
  submit_order:
    available: {roles: [trader]}
    params: {side: {type: enum, values: [buy, sell]}, price: {type: int, min: 1}}
    effects:
      - {create_entity: {kind: order, state: {owner: actor, side: params.side, price: params.price}}}
    messages: {place: "{actor.name} submits a {params.side} order."}
dynamics:
  - every_n_steps: 1
    effects: [{call: {handler: settle_market}}]   # the matching engine stays code
```

Who may order, what an order *is*, and what everyone sees are all data; the
double-auction crossing logic is forty lines of tested Python emitting
`set`/`inc`/`emit_event` ops. Same pattern for pathfinding NPCs, cellular
spread models, tournament brackets, external oracles.

---

## Part IV — Breadth check

### 14) Worked examples

Per principle 8, these are **instantiations, not features**: every mechanism
they use is scenario-agnostic, and the same handful of abstract structures —
scopes, containment, edges, gates, statuses, visibility predicates, timers —
recombine into wildly different experiments. Read each example as a proof of
recombination, not as a supported "mode."

#### 14.1 Hidden-role game (werewolf) — the mechanics stress test

Werewolf exercises nearly every construct: hidden state, roles, channels,
phases, voting, elimination — with **zero engine features**:

```yaml
world:
  name: werewolf_village
  state: {day_phase: {type: enum, values: [day, night], default: day}}
  agent_state:
    true_role: {type: enum, values: [villager, werewolf, seer], visibility: hidden}
  roles: {werewolf: {}, seer: {}, moderator: {max_holders: 0}}   # roles gate actions; true_role is info
  places:
    village: {description: "The village square."}
    graveyard: {description: "Quiet.", connects: []}      # elimination = relocation
  channels:
    wolf_den: {joinable: false}
  action_packs: [conversation, voting]
  actions:
    night_kill:
      kind: agent_interaction
      available: {roles: [werewolf], when: {eq: [world.day_phase, night]}, per_step: 1}
      params: {target: {type: entity,
               domain: {filter: {of: agents_in(village), where: {not: {has_role: [element, werewolf]}}}}}}
      effects:
        - {move_entity: {entity: params.target, to: graveyard}}
        - {remove_status: {entity: params.target, name: any}}
      messages:
        channel:wolf_den: "The pack has chosen {params.target.name}."
        global: "At dawn, {params.target.name} is found dead."
      event_label: night_kill
    inspect:
      kind: read
      available: {roles: [seer], when: {eq: [world.day_phase, night]}, per_step: 1}
      params: {target: {type: entity, domain: agents_in(village)}}
      effects: [{reveal: {to: actor, fields: [params.target.true_role]}}]
      observe: "Your vision shows {params.target.name}'s true nature."
  dynamics:
    - every_n_steps: 1
      effects: [{set: {path: world.day_phase,
                 value: {when: {pred: {eq: [world.day_phase, day]}, then: night, else: day}}}}]
      messages: {global: "The phase turns."}
    - on_event: {label: vote_passed}          # from the voting pack
      effects: [{move_entity: {entity: event.data.subject, to: graveyard}}]
      messages: {global: "{event.data.subject_name} is banished."}
  setup:
    roles: {by_count: {werewolf: 2, seer: 1}}
    # role assignment also sets true_role + wolf_den membership (assignment hooks)
  end_conditions:
    - {when: {is_empty: {filter: {of: agents_in(village), where: {has_role: [element, werewolf]}}}},
       label: village_wins}
    - {when: {gte: [{count: {filter: {of: agents_in(village), where: {has_role: [element, werewolf]}}}},
              {count: {filter: {of: agents_in(village), where: {not: {has_role: [element, werewolf]}}}}}]},
       label: wolves_win}
```

Elimination without engine hooks: the "dead" go to an unconnected `graveyard`
place — every place-scoped action's domain naturally excludes them (a
participation intervention can additionally silence them entirely).

#### 14.2 Information-asymmetry worlds at scale (Sally-Anne-style ToM)

The other direction of stress: not many mechanisms, but **one mechanism at
scale with fine-grained rules** — N rooms × M holders per room × K movable
objects, dozens of agents, per-holder visibility rules (at one location an
object is visible, at another it isn't). Constructs: generators (§12.4),
holders (§6.4), place-scoped witnessing (§11), the exposure log, probes.

```yaml
world:
  name: false_belief_xl
  object_types:
    hiding_spot:
      fixed: true
      state: {concealment: {type: enum, values: [open_surface, opaque, locked]}}
      holds:
        mode: inside
        capacity: 4
        contents_visible: {eq: [holder.concealment, open_surface]}
        searchable: true
        locked_when: {eq: [holder.concealment, locked]}
    trinket:
      state: {color: {type: enum, values: [red, blue, green, gold]}}
  action_packs: [movement, conversation, inventory]   # move/look/take/put/search
  generate:
    places:
      - {id: "room_{i}", count: 10, template: {tags: [room], connects: [hallway]}}
    objects:
      - {id: "{place}_spot_{i}", count: 10, in: {each: places_tagged(room)},
         template: {type: hiding_spot, tags: [spot],
                    state: {concealment: {pick_from: [open_surface, opaque, opaque, locked]}}}}
      - {id: "{place}_trinket_{i}", count: 10, in: {each: places_tagged(room)},
         template: {type: trinket}}
```

(`num_agents: 50` comes from the world config group like any run; the
inventory pack's `put` is the "hide" action — a scenario preferring the verb
can alias it or `extends` it with flavored messages.)

Why the false-belief measurement is end-to-end mechanical, with no bespoke
code:

1. **Witnessing is spatial and automatic** — `put`'s place-scoped message
   means agent A sees the trinket moved into the basket iff A is in the room
   at that moment. Leave the room, and the world stops telling you things.
2. **Per-location rules are holder predicates** — `open_surface` contents
   render for anyone entering; `opaque` contents don't (a `search` is a
   deliberate, logged read); `locked` gates search itself. Observer-conditioned
   predicates cover the richer variants (visible only when the room is lit,
   only to a role, only to whoever hid it).
3. **Ground truth is two logs** — `action_events.jsonl` records every actual
   move; `exposure_events.jsonl` records what each agent was shown. A probe at
   any anchor ("Where is the gold trinket?") scores against both: correct
   w.r.t. the world vs. correct w.r.t. the agent's own exposure — the gap *is*
   the false belief, and probes/anchors/sampling are existing eval machinery.
4. **Scale is config** — counts live in `generate`; a study sweeps room
   counts, concealment mixes, and agent density like any other config grid.

The same structures, renamed, are poker (hole cards = owner-visible fields),
insider trading (channel-scoped disclosures + exposure audit), fog-of-war
strategy (line-of-sight domain extension), or eyewitness-reliability
experiments (witnessed vs. reported events).

### 15) Scenario-family coverage map

| Scenario family | Load-bearing constructs |
|---|---|
| Markets / economies / commons dilemmas | attributes (resources), `cost`, trade pack (`contract` kind), metrics, invariants |
| Spatial exploration / evacuation / logistics | places, connection properties (locked/cost/travel_steps/hidden), movement pack |
| Social deduction / hidden info (werewolf, diplomacy) | hidden visibility, `reveal`, channels, roles, voting pack |
| Epidemics / diffusion / contagion | statuses (duration, on_expire), co-location dynamics, seeded chance, metrics |
| Organizations / institutions | roles, channels, custom kinds (`task`, `ticket`), employment relationships, delayed `schedule` effects |
| Governance / deliberation / voting | `proposal` kind, voting pack, `on_event` triggers enacting results |
| Negotiation / game theory (PD, ultimatum) | per_step budgets, simultaneous-ish resolution via phase dynamics, payoffs as effects |
| Relationship-driven drama / opinion dynamics | typed relationship edges with numeric state, `adjust_relation`, related-agent domains |
| Survival / resource management | object affordances (consume), statuses, world ticks (decay/regen), end conditions |
| ToM / false-belief / hidden information (Sally-Anne, poker, fog-of-war) | holders + per-observer visibility predicates, place-scoped witnessing, exposure log, probes, generators |
| Classic IF / puzzle worlds | objects, keys (`locked_when` + `holds`), `create_object`, hidden edges |
| Hybrid: spatial world + social platform | multi-GM composition — world GM + twitter GM in one run; a branch router can gate platform access on world state (e.g. only agents in `internet_cafe`) — **legal today** |

The honest gaps are in §17.

---

## Part V — Integration, limits, plan

### 16) Mapping onto existing seams (new vs. free)

| Piece | New work | Free from existing runtime |
|---|---|---|
| `DeclarativeWorldApp(SocialBackendApp)` | spec loader/validator, entity store, expression evaluator, transactional apply, message queues, `observe()` renderer | filters, aliases, catalogs, `invoke_action_detailed`, committed mirror, `count_committed_events` |
| Spec loading | `env.gm.backend.world_def: <path or inline dict>` through the backend-params path | Hydra composition, dashboard plumbing |
| Tool schemas | param-spec → `Parameter` objects (reuse `_param_to_json_schema`) | tool-calling resolve, action-prompt component |
| Checkpointing | `get_state()` = entity store + edges + timers + cooldown ledgers, one dict → `provides_checkpoint_state = True` | save strategies, restore validation; snapshot suffices (a generic replay mapper is trivial later — effects are data) |
| Observations | `BackendApp.observe(actor)` + `app_observation` component (MVP), or a `world_observation` component | observe slot, `episode_observation_flows` |
| Dynamics | one `update` component executing ticks/triggers/timers/status-expiry | `app_update` scheduling, `requires_full_roster`, `set_component_params` |
| Interventions | nothing | `inject_action` speaks the catalog; `broadcast_observation`, bans, retuning all work day one |
| Multi-GM | nothing | world GM composes with platform GMs via flow chains + branch routers today |
| Eval/analysis | `kind` taxonomy → `evaluations/vocabulary.py`; world metrics → sim metrics | probes, action metrics, dashboard trends |
| Visualizer | one generic world visualizer (place graph + live agent dots + entity inspector + event ticker), one `VISUALIZER_BACKENDS` entry | FastAPI `viz` pattern, dashboard one-click launch, auto-refresh |

Small additive base-layer improvements alongside (not blockers):
1. Bless `register_action(descriptor, handler)` as an instance API (today
   synthesized bound methods suffice).
2. Actor-aware `generate_tool_schemas(actor_name=None)` /
   `actions(actor_name=None)` so availability gates + non-empty domains shape
   each actor's schema (until then: actorless schemas, resolve-time refusal).
3. A `is_terminal()` loop-strategy poll seam for end conditions (W3).

### 17) The escape-hatch ladder and honest limits

When the closed language runs out, climb — each rung stays inside the runtime:

1. **Closed vocabulary** (everything above).
2. **The FFI table (§13)**: named functions, predicates, domains, handlers,
   renderers, systems, code actions, setup builders — plain callables against
   the `WorldView`/`WorldTxn` SDK, all writes staged through the one op
   pipeline.
3. **`llm_resolve`**: a narrator adjudicates fuzzy actions into closed-vocab
   effects (reproducible only as far as the model is).
4. **A hand-written backend** — the existing path, unchanged.

Stays Python forever (don't fight it): recommendation/ranking algorithms and
feeds (the heart of `twitter_like`), query-heavy analytics over large state,
market-clearing/matching algorithms, external I/O (Mastodon). Positioning:
**native backends for platforms, declarative worlds for everything else** —
composable in one run via multi-GM. Do *not* rewrite `twitter_like` as a spec;
do ship the `board` pack to show the continuum.

Also deliberately out of scope: agent belief modeling (§11), continuous
time/space (steps and place-graphs only), physics, and effect cascades deeper
than one trigger (principle 4).

### 18) Concurrency & determinism notes

- One mutex around the validate→apply→log critical section: `require`/`cost`/
  capacity re-checked under the lock, so concurrent turns can't double-spend or
  overfill a room. Cheap; per-GM concurrency caps already exist for throttling.
- Commit order = wall-clock lock-acquisition order under concurrent executors —
  same nondeterminism class the SQL backends already have; use `multi_gm_serial`
  /`sequential` step strategies + participation `all` for strict determinism.
- All randomness seeded per §8.4; the timer queue and status durations live in
  `get_state`, so resume is exact.

### 19) Payoffs

1. **Four front-ends, one spec**: hand YAML · `WorldBuilder` fluent Python API ·
   dashboard visual builder (every construct is a form: place-graph editor,
   action form with predicate/effect builders, object palette) · **LLM
   authoring** — "describe a world in prose → valid `world_def` → runnable
   backend in seconds." The demo money shot, and the real meaning of
   "generate backends programmatically."
2. **One generic world visualizer serves every world ever defined** — map panel
   with live agent dots, entity inspector, committed-event ticker,
   auto-refreshing, launched from the existing Results-tab buttons.
3. **Procedural generation & studies over structure**: worlds are data, so
   generators can emit them — parameter sweeps over topology, role mixes,
   rule variants become ordinary studies.
4. **Static validation & explainability**: load-time typecheck of every path,
   domain, template, and invariant against the schemas; the dashboard can
   render an action's contract in English ("requires coins ≥ amount; gives
   target the amount; witnesses see …").

### 20) Phasing

- **W1 — interpreter core (prove the loop).** Entity store + field schema,
  places/connections (incl. `locked_when`), recursive containment/holders with
  boolean `contents_visible`, movement + conversation + inventory packs,
  object affordances, flat `effects` (state/containment/lifecycle ops),
  `require`/`cost`, scoped messages, observation template, `generate` macros
  (load-time expansion is cheap and unlocks scale immediately), snapshot
  checkpointing, spec validator with typed load-time errors. One example world
  (`scenarios/village/`). Tests: validation errors, transactional commit-only
  logging, tool-schema generation, checkpoint round-trip, scripted E2E
  (pattern: `test_scripted_social_e2e.py`). ~2–3k lines with tests.
- **W2 — the full actor + resolution + information model.** Roles, tags,
  statuses (+expiry), relationships (+generators), channels, `outcomes`
  (when/chance/default), queries (`count/sum/filter/pick`), `for_each`/`when`,
  `available` gates (cooldowns/budgets), dynamics (ticks/triggers/timers),
  observer-conditioned visibility predicates + `reveal`, the exposure log
  (`exposure_events.jsonl`), metrics, invariants, the FFI table core
  (functions/predicates/domains/handlers) + `WorldView`/`WorldTxn` SDK,
  voting/trade/board packs, vocabulary integration.
- **W3 — surfaces & engine niceties.** Generic world visualizer; actor-aware
  catalogs/schemas; `is_terminal()` loop seam; remaining FFI points
  (renderers, systems, code actions, setup builders); dashboard visual builder
  (read/write the spec); `WorldBuilder` API; LLM-authoring guided workflow
  (`agent_docs/skills/new-world.md`); docs (`docs/declarative_worlds.md`).
- **W4 — frontier.** `llm_resolve` adjudicators; procedural world generators;
  hybrid world+platform scenario templates; spec-diff tooling (world versioning
  beyond the restore stamp).

### 21) Open design questions

1. Where the spec validator lives: in the backend vs. `runtime/configuration`
   (the latter surfaces errors at dashboard launch time, where users are).
2. Actor-aware schemas in W1 or W3 — resolve-time refusal is simpler but burns
   turns on invalid picks in object-rich worlds.
3. `params` enum-inlining size limits (a domain of 200 agents shouldn't inline
   200 enum values — cap + describe-in-text fallback).
4. Pack versioning/namespacing once user packs exist (collision rules on merge).
5. Whether `read`-kind actions should optionally not consume `fixed_count`
   turn budget (interacts with `count_committed`; likely a turn-policy param,
   not a world-spec concern).
6. Exposure-log volume: at 50 agents × long runs, logging every rendered
   observation is heavy — likely record structured *deliveries* (what fact,
   from which mechanism, at which step), not full rendered text, plus a
   sampling/rotation knob.
7. Observation size at scale: `list` sections need `limit`/`summarize: count`
   options so a 10-holder room doesn't produce a wall of text; decide defaults.
8. Whether `generate` should also run at *step* time (spawners) or stay
   load-time-only with runtime creation left to `create_entity`/systems
   (current stance: the latter — one instantiation semantics).
9. Naming bikeshed: `world_def` / `DeclarativeWorldApp` / `env=world` used
   throughout; final names later.
