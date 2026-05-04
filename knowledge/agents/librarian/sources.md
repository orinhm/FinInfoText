---
type: knowledge
revision: 2
last_modified: 2026-04-27T21:51:00+03:00
summary: "Comprehensive registry of data sources by industry — available scrapers and recommended external sources for future development"
---
# Available Data Sources

## Currently Implemented Scrapers

### CEO.CA Spiels
- **Scraper**: `ceo_ca`
- **Reliability**: HIGH — official API, structured JSON
- **Data**: Discussion posts from CEO.CA channels (spiels)
- **Parameters**: `channel` (ticker slug, e.g. "nfg", "tsla"), `days_back`
- **Fields**: spiel text, author, timestamp, votes, bot flag, channel
- **Coverage**: Any ticker with a CEO.CA board — strongest for Canadian junior miners
- **API**: `https://new-api.ceo.ca/api/get_spiels?channel=<slug>`

### NewFoundGold News Releases
- **Scraper**: `nfg_news`
- **Reliability**: HIGH — official company website
- **Data**: Press releases and news from newfoundgold.ca
- **Parameters**: `days_back`
- **Fields**: title, body, date, URL
- **Coverage**: NFGC-specific corporate releases

### Generic Web/News Scraper
- **Scraper**: `web_news`
- **Reliability**: MEDIUM — heuristic article extraction via BeautifulSoup
- **Data**: Articles from any news website
- **Parameters**: `url` (website URL), `query` (search term), `days_back`, `max_articles`
- **Fields**: title, body, date, URL, source domain
- **Coverage**: Works with most news sites — Mining.com, Kitco, Rigzone, MarketWatch, etc.
- **Known sites**: Mining.com, Kitco, SeekingAlpha, MarketWatch, NorthernMiner, Rigzone, OilPrice

### Yahoo Finance
- **Scraper**: `yahoo_finance`
- **Reliability**: HIGH — yfinance library, structured data
- **Data**: Company info, price history, financials, news, analyst recommendations
- **Parameters**: `ticker` (e.g. "NFGC.V", "TSLA", "AAPL"), `days_back`
- **Fields**: company profile, OHLCV prices, P/E, market cap, revenue, margins, 52w range, short ratio, etc.
- **Coverage**: Any ticker on Yahoo Finance globally

### FRED (Federal Reserve Economic Data)
- **Scraper**: `fred`
- **Reliability**: HIGH — official Fed API
- **Data**: 800,000+ macroeconomic data series (GDP, CPI, rates, employment, etc.)
- **Parameters**: `series` (FRED ID or common name), `query` (search), `days_back`
- **Common names**: `cpi`, `unemployment`, `fed_funds`, `gold_price`, `oil_wti`, `10y_treasury`, `vix`, etc.
- **Coverage**: US and global economic indicators
- **Requires**: Free API key from https://fred.stlouisfed.org/docs/api/api_key.html

---

## External Data Sources by Industry

> **Note**: Sources below are NOT yet implemented as scrapers.
> Use the `web_news` scraper to fetch articles from most of these sites.
> For structured data, a dedicated scraper may be needed.

---

### 🏗️ Mining & Resources

#### General Mining
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| Mining.com | https://mining.com | News, analysis, commodity prices | Largest mining news site globally |
| Mining Weekly | https://miningweekly.com | News, project updates | South Africa focus but global coverage |
| Northern Miner | https://northernminer.com | News, technical articles | Canadian mining focus, premium content |
| Kitco Mining | https://kitco.com/mining | News, company profiles | Gold/silver mining focus |
| InfoMine | https://infomine.com | Commodity data, company profiles | Technical mining database |
| MiningFeeds | https://miningfeeds.com | Aggregated mining news | Multi-source feed |

#### Precious Metals (Gold / Silver / PGMs)
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| Kitco | https://kitco.com | Spot prices, charts, news | Industry standard for precious metals prices |
| GoldHub (World Gold Council) | https://gold.org/goldhub | Supply/demand data, ETF flows | Authoritative gold market data |
| SilverInstitute | https://silverinstitute.org | Silver supply/demand reports | Annual world silver surveys |
| BullionVault | https://bullionvault.com | Retail investor sentiment, prices | Physical bullion market prices |
| LBMA | https://lbma.org.uk | London fix prices, refiner list | Official London bullion pricing |

#### Battery Metals (Lithium / Nickel / Cobalt / Copper)
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| Benchmark Minerals | https://benchmarkminerals.com | Lithium, nickel, cobalt, graphite prices | Leading battery supply chain intelligence |
| Fastmarkets | https://fastmarkets.com | Commodity pricing | Base metals and battery materials |
| CRU Group | https://crugroup.com | Analysis & commodity pricing | Enterprise-grade commodity intelligence |
| Cobalt Institute | https://cobaltinstitute.org | Cobalt market data | Industry association data |

#### Uranium
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| UxC (Ux Consulting) | https://uxc.com | Spot/term prices, supply data | Industry standard uranium pricing |
| Numerco | https://numerco.com/nSet/sSpoturanium.html | Spot uranium price | Real-time spot indicator |
| World Nuclear Association | https://world-nuclear.org | Reactor database, industry stats | Demand-side data |

---

### 📈 Equities & Market Data

#### Stock Data & Financials
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| Yahoo Finance | https://finance.yahoo.com | Prices, fundamentals, filings | Free API available, broad coverage |
| Google Finance | https://google.com/finance | Prices, charts | Basic equity data |
| Finviz | https://finviz.com | Screener, heatmaps, charts | Excellent for screening US equities |
| TradingView | https://tradingview.com | Charts, community ideas | Technical analysis & social sentiment |
| MarketWatch | https://marketwatch.com | News, prices, earnings | Broad market news |
| Seeking Alpha | https://seekingalpha.com | Analysis, earnings transcripts | Crowd-sourced equity research |
| Barchart | https://barchart.com | Prices, options data, screeners | Good for options flow |

#### SEC / Regulatory Filings
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| SEC EDGAR | https://sec.gov/cgi-bin/browse-edgar | 10-K, 10-Q, insider filings | Official US filings, free API |
| SEDAR+ | https://sedarplus.ca | Canadian filings (NI 43-101, etc.) | Official Canadian disclosure system |
| OpenInsider | https://openinsider.com | Insider buy/sell filings | Parsed SEC Form 4 data |

#### Canadian Markets
| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| TMX Money | https://money.tmx.com | TSX/TSXV quotes, filings | Official TMX data |
| Canadian Insider | https://canadianinsider.com | Insider transactions (SEDI) | Canadian insider trading data |
| Stockwatch | https://stockwatch.com | Canadian market news, data | Popular with Canadian investors |

---

### 🏦 Macro & Economic

| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| FRED (St. Louis Fed) | https://fred.stlouisfed.org | Economic indicators, rates | Free API, 800K+ data series |
| Trading Economics | https://tradingeconomics.com | Global macro indicators | Easy to scrape, wide coverage |
| World Bank Data | https://data.worldbank.org | Development indicators | Free API, country-level data |
| IMF Data | https://data.imf.org | Balance of payments, reserves | International monetary data |
| BLS | https://bls.gov | US jobs, CPI, employment | Official US labor statistics |

---

### ⛽ Energy

| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| EIA | https://eia.gov | US energy data, oil/gas inventory | Free API, official US energy stats |
| OPEC | https://opec.org | Production data, monthly reports | Official OPEC reports |
| Rigzone | https://rigzone.com | Oil & gas news, rig counts | Industry news and data |
| OilPrice.com | https://oilprice.com | Energy news, oil prices | Accessible energy news |
| Baker Hughes Rig Count | https://bakerhughes.com/rig-count | Weekly rig count data | Key supply indicator |

---

### 🌾 Agriculture & Soft Commodities

| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| USDA | https://usda.gov/nass | Crop reports, supply/demand | Official US agriculture data |
| FAO | https://fao.org/faostat | Global agriculture data | UN food & agriculture stats |
| Barchart Agriculture | https://barchart.com/futures/grains | Grain futures, seasonal charts | Ag commodity prices |
| DTN/Progressive Farmer | https://dtn.com | Weather, crop progress | Agricultural weather data |

---

### 🏠 Real Estate

| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| Zillow Research | https://zillow.com/research/data | Home values, rent indices | US housing data, CSV downloads |
| Redfin Data Center | https://redfin.com/news/data-center | Housing market metrics | Detailed US housing stats |
| FRED Housing | https://fred.stlouisfed.org (housing series) | Case-Shiller, permits, starts | Official US housing indicators |
| Statistics Canada | https://statcan.gc.ca | Canadian housing data | Official Canadian stats |

---

### 💻 Technology & Crypto

| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| CoinGecko | https://coingecko.com | Crypto prices, market cap | Free API, 10K+ coins |
| CoinMarketCap | https://coinmarketcap.com | Crypto prices, rankings | Popular crypto data |
| Glassnode | https://glassnode.com | On-chain analytics | Bitcoin/Ethereum on-chain data |
| DefiLlama | https://defillama.com | DeFi TVL, protocol data | Open DeFi analytics |
| Crunchbase | https://crunchbase.com | Startup funding, company data | Tech company database |

---

### 📊 Sentiment & Alternative Data

| Source | URL | Data Type | Notes |
|--------|-----|-----------|-------|
| StockTwits | https://stocktwits.com | Retail sentiment by ticker | Social sentiment API |
| Reddit (via API) | https://reddit.com | r/wallstreetbets, r/stocks, etc. | Retail sentiment |
| Google Trends | https://trends.google.com | Search interest trends | Proxy for retail attention |
| CNN Fear & Greed | https://money.cnn.com/data/fear-and-greed | Market sentiment index | Composite sentiment indicator |
| AAII Sentiment | https://aaii.com/sentimentsurvey | Bull/bear survey | Weekly retail investor sentiment |
| Short Interest | https://shortinterest.com | Short selling data | US equities short interest |

---

## Adding New Scrapers

When a new data source is needed:
1. Create a new Python file in `marketsage/scrapers/` implementing `fetch(**params) → list[dict]`
2. Document the new source in this file with the same format as above
3. The data service will auto-discover the new scraper module
4. Run a test to verify: `python -c "from marketsage.scrapers import list_scrapers; print(list_scrapers())"`

### Scraper Priority Queue

Sources the system should build scrapers for next (based on frequency of need):
1. **Yahoo Finance** — universal equity data (prices, fundamentals)
2. **SEDAR+** — Canadian mining filings (NI 43-101 technical reports)
3. **Kitco** — precious metals spot prices
4. **FRED** — macroeconomic indicators
5. **SEC EDGAR** — US regulatory filings
6. **Mining.com** — mining industry news
