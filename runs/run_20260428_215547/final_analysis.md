Here is an overview of my capabilities and the current contents of the Knowledge Vault.

### 🛠️ Available Tools

I am equipped with a set of specialized tools, divided into data scraping, knowledge management, and agent simulation.

**Data Fetching & Scrapers:**
*   `fetch_yahoo_finance`: Retrieves stock data, price history, key financials, news headlines, and analyst recommendations for global tickers.
*   `fetch_fred`: Accesses the Federal Reserve Economic Data (FRED) database for macroeconomic indicators (e.g., interest rates, GDP, CPI, gold prices).
*   `fetch_ceo_ca`: Scrapes discussion posts and retail sentiment from CEO.CA forum channels (e.g., 'tsla', 'gold', 'nfg').
*   `fetch_web_news`: Extracts full-text news articles from websites like mining.com, kitco.com, seekingalpha.com, etc., using URLs and optional search queries.
*   `fetch_nfg_news`: Dedicated scraper for fetching official press releases from NewFoundGold Corp.
*   `list_available_scrapers`: Returns the active roster of data scrapers currently loaded into the system.

**Vault & Knowledge Management:**
*   `list_vault_contents`: Explores directories and lists files within the knowledge vault to discover stored intelligence.
*   `read_vault_file`: Reads specific markdown files from the vault to retrieve historical analysis, facts, and tracked contradictions.
*   `persist_learning`: Appends newly discovered facts, patterns, or insights into the vault at the asset, sector, or generic agent level.

**Agent Simulation:**
*   `read_agent_knowledge`: Loads the specialized prompts and accumulated expertise of specific personas (e.g., `trader`, `executive`, `auditor/geologist`, `accountant/mining`) to adopt their analytical frameworks.

---

### 🗄️ Contents of the Knowledge Vault

The Knowledge Vault is a hierarchical directory storing accumulated intelligence from past analyses. 

**Root Directory (`vault/`):**
*   📄 `_index.json` *(Master index mapping sectors, sub-sectors, assets, and tickers)*
*   📁 `_reports/` *(Saved analytical reports)*
*   📁 `equities/` *(General equity-specific knowledge)*
*   📁 `mining/` *(Broader mining industry knowledge)*
*   📁 `commodities/` *(Sector-specific knowledge for commodities)*

**Inside the `commodities/` sector:**
*   📄 `commodities.md` *(High-level commodities knowledge)*
*   📄 `commodities_macro.md` *(Macro factors affecting commodities)*
*   📄 `copper.md` *(Copper-specific intelligence)*
*   📁 `gold/` *(Specific structure for the Gold sub-sector, mapped in the index)*
    *   📄 `gold_sector.md` *(Precious metals & gold sector heuristics)*
    *   📁 `assets/`
        *   📄 `nfgc.md` *(Specific vault file for New Found Gold Corp. - Tickers: NFG, NFGC.US)*

If you have a specific asset, sector, or macroeconomic trend you would like me to analyze using these tools and knowledge bases, just let me know!