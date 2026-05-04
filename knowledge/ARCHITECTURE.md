# Knowledge System Architecture

> **This document is the authoritative specification for the MarketSage knowledge
> file system.** All runtime code (agent composition, knowledge resolution,
> learning persistence) MUST follow the rules defined here.

---

## 1. Directory Structure

The knowledge tree is a hierarchical file system where **depth equals specificity**.
The deeper a directory, the more specialized its knowledge.

```
knowledge/
├── ARCHITECTURE.md          ← This file (system specification)
├── sector.md                ← Universal market knowledge
├── agents/                  ← Universal agent frameworks
│   ├── {role}/
│   │   ├── prompt.md        ← Agent persona & instructions (static)
│   │   └── knowledge.md     ← Accumulated learnings (dynamic)
│   └── ...
│
├── {sector}/                ← Top-level sector (e.g., commodities, equities)
│   ├── sector.md            ← Sector-level domain knowledge
│   ├── agents/              ← Sector-specialized agents
│   │   └── {role}/
│   │       ├── prompt.md
│   │       └── knowledge.md
│   ├── assets/              ← Sector-level asset files
│   │   └── {ticker}.md
│   │
│   └── {sub-sector}/        ← Sub-sector (e.g., precious_metals, tech)
│       ├── sector.md
│       ├── agents/
│       ├── assets/
│       └── {sub-sub-sector}/   ← Can nest arbitrarily deep
│           ├── sector.md
│           ├── agents/
│           └── assets/
```

### Rules

1. **Every directory node** SHOULD have a `sector.md` file.
2. **Every directory node** SHOULD have an `agents/` directory (may be empty).
3. **Agent directories** contain exactly `prompt.md` and optionally `knowledge.md`.
4. **Asset files** live in `assets/` directories at any level.
5. The tree can nest arbitrarily deep (e.g., `commodities/precious_metals/gold/juniors/`).

---

## 2. File Types & Schemas

### 2.1 `sector.md` — Domain Knowledge

**Purpose**: Non-agent-specific knowledge about a sector or domain. Contains facts,
heuristics, macro context, and structural information. Every directory node should
have one.

**Frontmatter schema**:
```yaml
---
type: knowledge
revision: <int>
last_modified: <ISO 8601 timestamp>
summary: "<brief description>"
inherits:                          # OPTIONAL — cross-sector inheritance
  - <relative_path_to_sector_1>
  - <relative_path_to_sector_2>
---
```

**The `inherits` field** is the mechanism for cross-sector knowledge composition.
See [Section 3: Inheritance](#3-inheritance--resolution).

**Body**: Markdown content — learnings, heuristics, macro analysis, etc.

---

### 2.2 `prompt.md` — Agent Persona

**Purpose**: Defines HOW an agent thinks — its analytical framework, output format,
and domain-specific instructions. This is the agent's identity.

**Frontmatter schema**:
```yaml
---
type: prompt
revision: <int>
last_modified: <ISO 8601 timestamp>
summary: "<agent role description>"
---
```

**Semantics**: **Override** — a more-specific prompt REPLACES its parent, it does
not append. A mining accountant prompt replaces the generic accountant prompt
entirely (the generic framework is assumed to be incorporated into the
specialization).

---

### 2.3 `knowledge.md` — Agent Learnings

**Purpose**: What an agent has LEARNED over time — accumulated insights, patterns,
and heuristics discovered during analysis runs. This is dynamic, system-updated
content.

**Frontmatter schema**:
```yaml
---
type: knowledge
revision: <int>
last_modified: <ISO 8601 timestamp>
summary: "<brief description>"
---
```

**Body**: Timestamped bullet-point learnings:
```markdown
# Learnings

- [2026-04-27] The Lassonde Curve orphan period is characterized by...
- [2026-04-28] Gold prices easing with a falling USD is a divergence...
```

**Semantics**: **Merge/Accumulate** — knowledge from ALL levels in the resolution
chain is combined. A mining accountant receives both cross-sector accounting
learnings AND mining-specific accounting learnings.

---

### 2.4 `{ticker}.md` — Asset Files

**Purpose**: Asset-specific intelligence — company facts, ticker aliases, sector
memberships, and accumulated learnings about a particular asset.

**Frontmatter schema**:
```yaml
---
type: asset
revision: <int>
last_modified: <ISO 8601 timestamp>
summary: "<asset description>"
name: "<full company name>"
tickers: [<TICKER1>, <TICKER2>]
sectors:                            # Multi-sector assets
  - <sector_path_1>
  - <sector_path_2>
---
```

**The `sectors` field** lists all sector paths this asset belongs to. When
analyzing a multi-sector asset, the system loads context from ALL listed sectors.

**Semantics**: **Union** — assets from all levels in the resolution chain are
available to agents.

---

## 3. Inheritance & Resolution

### 3.1 The Two Inheritance Mechanisms

#### A. Natural Inheritance (Directory Walk-Up)

Every directory automatically inherits from its parent directories up to the root.
This is implicit — no declaration needed.

```
commodities/precious_metals/gold/  inherits from:
  → commodities/precious_metals/
  → commodities/
  → knowledge/ (root)
```

#### B. Cross-Sector Inheritance (Declared via `inherits:`)

Some sectors belong to multiple domains. For example, mining equities need both
equity analysis frameworks AND commodity domain knowledge. This is declared in
the sector's `sector.md` frontmatter:

```yaml
# knowledge/equities/mining/sector.md
---
inherits:
  - commodities/precious_metals
---
```

This means `equities/mining/` receives knowledge from `commodities/precious_metals/`
(and its ancestors) IN ADDITION to its natural parent chain (`equities/` → root).

### 3.2 Resolution Algorithm

When composing knowledge for a sector (e.g., `equities/mining`), the system
builds an **ordered resolution chain**:

```
resolve_sector_chain("equities/mining") →
  1. equities/mining          (the sector itself)
  2. equities                 (natural parent)
  3. commodities/precious_metals  (declared inherit)
  4. commodities              (parent of inherited sector)
  5. (root)                   (always included)
```

**Rules**:
- **Most-specific first**: The sector's own files have highest priority.
- **Natural parents before inherited**: Walk up the directory tree before
  following `inherits:` links.
- **Inherited sectors include THEIR parents**: When inheriting from
  `commodities/precious_metals`, also include `commodities/`.
- **Deduplication**: If the root (or any path) is reachable from multiple
  routes, include it only ONCE, at its first occurrence.
- **`inherits:` order matters**: If multiple sectors are inherited, they are
  processed in the order declared.

### 3.3 The Diamond Problem

```
           knowledge/ (root)
            /         \
      equities/    commodities/
            \         /
        equities/mining/
```

The root is reachable from both `equities/` and `commodities/`. The resolution
algorithm ensures root-level knowledge is loaded exactly once.

### 3.4 Recursive Inheritance

Inherited sectors may themselves have `inherits:` declarations. The system
follows these recursively, always deduplicating. Example:

```
equities/mining inherits commodities/precious_metals
commodities/precious_metals inherits (nothing)

Resolution: mining → equities → precious_metals → commodities → root
```

If precious_metals itself inherited from somewhere, that would be included too,
always deduplicated.

---

## 4. Agent Composition

When an agent is loaded (e.g., `load_agent(role='accountant', sector_path='equities/mining')`),
the system composes its full context from the resolution chain.

### 4.1 Composition Rules

| File Type       | Semantics     | Behavior                                                        |
|-----------------|---------------|-----------------------------------------------------------------|
| `prompt.md`     | **Override**  | Use the MOST SPECIFIC prompt found. Walk chain from most-specific to least; use the first one found. |
| `knowledge.md`  | **Merge**     | Concatenate ALL knowledge files found across the chain. Walk from least-specific to most-specific so newer/specific learnings appear last. |
| `sector.md`     | **Merge**     | Concatenate ALL sector context files from the chain. Provides the full domain picture. |
| `assets/`       | **Union**     | All asset files from all levels in the chain are available.      |

### 4.2 Prompt Composition Example

For `Agent(role='accountant', sector_path='equities/mining')`:

**Resolution chain**: `equities/mining` → `equities` → `commodities/precious_metals` → `commodities` → root

**Prompt resolution** (override — first found wins):
1. Check `equities/mining/agents/accountant/prompt.md` → ✅ EXISTS → **use this**
2. (stop searching for prompt)

**Knowledge resolution** (merge — collect all):
1. `agents/accountant/knowledge.md` → generic accounting learnings
2. `commodities/agents/accountant/knowledge.md` → commodity accounting (if exists)
3. `commodities/precious_metals/agents/accountant/knowledge.md` → precious metals accounting (if exists)
4. `equities/agents/accountant/knowledge.md` → equity accounting (if exists)
5. `equities/mining/agents/accountant/knowledge.md` → mining accounting learnings
6. → ALL merged into one knowledge block

**Sector context resolution** (merge — collect all):
1. `sector.md` (root) → universal market facts
2. `commodities/sector.md` → commodity macro
3. `commodities/precious_metals/sector.md` → precious metals context
4. `equities/sector.md` → equity market context
5. `equities/mining/sector.md` → mining sector context
6. → ALL merged into one sector context block

### 4.3 Final Agent Prompt Structure

The composed agent prompt sent to the LLM follows this structure:

```markdown
# {Role} — {Sector} Specialist

{prompt.md content — from most-specific level}

===

# Current Sector Intelligence

{merged sector.md content — all levels, least to most specific}

===

# Accumulated Knowledge

### Cross-Sector Learnings
{root-level knowledge.md}

### {Inherited Sector} Learnings
{inherited sector knowledge.md}

### {Parent Sector} Learnings
{parent sector knowledge.md}

### Sector-Specific Learnings
{own sector knowledge.md}
```

---

## 5. Learning Persistence

When the system discovers new knowledge during analysis, it persists it to the
correct location in the tree.

### 5.1 Target Path Rules

| Learning Type          | Target Path                                           |
|------------------------|-------------------------------------------------------|
| Asset-specific         | `{sector}/assets/{ticker}.md`                         |
| Sector-level           | `{sector}/sector.md`                                  |
| Agent role + sector    | `{sector}/agents/{role}/knowledge.md`                 |
| Cross-sector (generic) | `agents/{role}/knowledge.md`                          |
| Universal market       | `sector.md` (root)                                    |

### 5.2 Persistence Format

Learnings are appended as timestamped bullets:

```markdown
- [2026-04-28] {learning text}
```

Frontmatter `revision` is bumped and `last_modified` is updated on each write.

---

## 6. Multi-Sector Assets

Some assets span multiple sectors (e.g., Barrick mines gold AND copper).

### 6.1 Declaration

Assets declare sector membership in frontmatter:

```yaml
sectors:
  - commodities/precious_metals/gold
  - commodities/base_metals/copper
```

### 6.2 Analysis Behavior

When analyzing a multi-sector asset:
1. Load context from ALL declared sectors
2. Load agents from the PRIMARY sector (first in the list)
3. Merge knowledge from all sectors into the agent's context
4. The agent should address cross-sector dynamics in its analysis

---

## 7. Discovery

### 7.1 Sector Discovery

To discover all sectors with specialized agents, walk the tree looking for
`agents/` directories that contain at least one `{role}/prompt.md`.

### 7.2 Agent Discovery

To discover available roles for a sector:
1. List `{sector}/agents/` — these are the sector-specialized roles
2. List `agents/` (root) — these are always available as base roles
3. The union is the full set of available roles for that sector

---

## 8. Summary of Conventions

| Convention                  | Rule                                                    |
|-----------------------------|---------------------------------------------------------|
| Sector knowledge file       | Always named `sector.md`                                |
| Agent persona file          | Always named `prompt.md`                                |
| Agent learnings file        | Always named `knowledge.md`                             |
| Asset files                 | Named `{ticker}.md` inside `assets/` directories        |
| Cross-sector inheritance    | Declared in `sector.md` frontmatter as `inherits: [...]`|
| Prompt semantics            | Override (most-specific wins)                           |
| Knowledge semantics         | Merge (all levels accumulated)                          |
| Asset semantics             | Union (all levels combined)                             |
| Resolution order            | Own → parents → inherited → root (deduplicated)         |
