# MarketSage — Architecture

## System Overview

MarketSage is a multi-agent investment intelligence system that maintains
a self-evolving **Hierarchical Markdown Knowledge Vault**.

```mermaid
flowchart TB
    subgraph OBSERVE["1. OBSERVE"]
        DS[("Data Sources<br/>CEO.CA · NFG News")]
        LIB["🔧 Librarian<br/><i>Tools Registry</i>"]
        DS --> LIB
    end

    subgraph ANALYSE["2. ANALYSE — Expert Committee"]
        AUD["🔬 Scientific Auditor<br/><i>Grade validation</i>"]
        EXEC["💼 Industry Executive<br/><i>Lassonde Curve</i>"]
        TRAD["📈 Market Strategist<br/><i>Sentiment scoring</i>"]
        ACCT["📊 Mining Accountant<br/><i>AISC · NPV · IRR</i>"]
    end

    subgraph SYNTHESISE["3. SYNTHESISE"]
        AGG["⚙️ Aggregator<br/><i>Weighted composite</i>"]
    end

    subgraph CURATE["4. CURATE"]
        CUR["🧠 Knowledge Curator<br/><i>Dedup · Conflict · Bubble-up</i>"]
    end

    subgraph PERSIST["5. PERSIST — Knowledge Vault"]
        L3["📁 commodities_macro.md<br/><i>Level 3: Industry</i>"]
        L2["📁 gold_sector.md<br/><i>Level 2: Sector</i>"]
        L1["📁 nfgc.md<br/><i>Level 1: Asset</i>"]
    end

    LIB --> AUD & EXEC & TRAD & ACCT
    AUD & EXEC & TRAD & ACCT --> AGG
    AGG --> CUR
    CUR -->|"bubble up"| L3
    CUR -->|"bubble up"| L2
    CUR -->|"insert"| L1

    style OBSERVE fill:#1a1a2e,color:#e0e0ff
    style ANALYSE fill:#16213e,color:#e0e0ff
    style SYNTHESISE fill:#0f3460,color:#e0e0ff
    style CURATE fill:#533483,color:#e0e0ff
    style PERSIST fill:#2b2d42,color:#e0e0ff
```

## Vault File Hierarchy

```
vault/
├── _index.json
├── _reports/                          ← JSON audit trail
│   └── NFGC_20260424_160000.json
└── commodities/
    ├── commodities_macro.md           ← Level 3: Industry
    └── precious_metals/
        └── gold/
            ├── gold_sector.md         ← Level 2: Sector
            └── assets/
                └── nfgc.md            ← Level 1: Asset
```

## Learning Loop (Update Workflow)

| Step | Agent | Action |
|------|-------|--------|
| 1. Observe | Librarian | Loads scraped spiels + articles |
| 2. Analyse | Expert Committee | Generates `ExpertOpinion` objects |
| 3. Synthesise | Aggregator | Merges into `StateReport` |
| 4. Curate | Knowledge Curator | Dedup → Conflict check → Bubble-up → Write |
| 5. Persist | Curator | Rewrites Markdown vault files |
