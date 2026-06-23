# **Product Requirement Document (PRD): Serverless Automated Darvas Box Trading Platform**

## **1\. Document Control**

* Title: Serverless Automated Darvas Box Swing Trading Platform for Indian Markets  
* Version: 3.0 (Serverless, GTT-Driven & Structural R Constrained)  
* Date: 2026-06-19  
* Author: Product Owner / Lead System Architect  
* Target Engine: AI Code Generation / Cursor Vibe Coding (Claude 3.5 Sonnet / GPT-4o)

## **2\. Serverless Architectural Topology**

To minimize operational overhead, ensure 100% computational reliability, and eliminate state-drift, the platform is decoupled into a short-lived execution script (Lambda-style) and an on-demand optimization engine.  
\+---------------------------------------------------------------------------------+    
| DAILY POST-MARKET LAMBDA PIPELINE (16:30 IST)                                   |    
|                                                                                 |    
| \[Trigger\]      \+------------------+                  \+------------------+       |    
| Cron/Event \---\>|  Data Ingestion  |-----------------\>|  Darvas & Risk   |       |    
|                |   (Upstox API)   |                  |  Matrix Engine   |       |    
|                \+------------------+                  \+------------------+       |    
|                         |                                     |                 |    
|                         v                                     v                 |    
|                \+------------------+                  \+------------------+       |    
|                |   AWS S3 / EFS   |\<-----------------|   SQLite Sync    |\<------+    
|                | (Persistent DB)  |                  | State Management |       |    
|                \+------------------+                  \+------------------+       |    
|                         |                                                       |    
|                         v                                                       |    
|                \+------------------+                                             |    
|                |    Broker GTT    |                                             |    
|                |   Orchestrator   |                                             |    
|                \+------------------+                                             |    
|                         |                                                       |    
\+-------------------------|-------------------------------------------------------+    
                          v    
 \+-------------------------------------------+    
 |               BROKER CLOUD                |    
 |                 (Upstox)                  |    
 |         (Persistent GTT Orders)           |    
 \+-------------------------------------------+    
    
\+---------------------------------------------------------------------------------+    
| ON-DEMAND ADVISORY MODULE (MANUAL RUN)                                          |    
|                                                                                 |    
| \[Manual Run\] \---\> Reads DB State \---\> Analyzes Trade Ledger \---\> Returns JSON   |    
\+---------------------------------------------------------------------------------+  

### **2.1 Component Decomposition**

1. **Daily Core Execution Module (lambda\_function.py):**  
   * Scheduled via cron daily at 16:30 IST (post-market close when EOD adjustments settle).  
   * Ingests data directly via the broker's API, updates the SQLite state database, recalculates box rules, updates broker GTT orders, saves state logs, and terminates immediately.  
2. **Decoupled Self-Improvement Advisor Module (advisor.py):**  
   * Completely independent from the daily pipeline.  
   * Run manually on-demand via CLI or external trigger to analyze performance over a variable time window and generate configuration updates.  
3. **State Persistence:**  
   * Because AWS Lambda/Serverless instances are ephemeral, the darvas\_trading.db SQLite file must be pulled from a persistent volume (AWS EFS or an S3 bucket synchronized at runtime initialization) and uploaded back to persistent storage during the script’s exit sequence.

## **3\. System Configuration Schema (config.yaml)**

system:    
 mode: "discretionary" \# Options: \[discretionary, fully\_automated\]    
 execution\_segment: "CASH"    
storage:    
 type: "s3" \# Options: \[local, s3, efs\]    
 s3\_bucket\_name: "darvas-trading-state-storage"    
universe\_filters:    
 min\_daily\_volume\_shares: 500000    
 min\_daily\_turnover\_inr\_cr: 10.0    
 min\_stock\_price\_inr: 100.0    
 lookback\_years\_for\_doubling: 2    
 exclude\_asm\_gsm: true    
fundamental\_filters:    
 source: "broker\_api" \# Free developer APIs used (e.g., Upstox Developer API)    
 min\_revenue\_growth\_pct: 15.0    
 min\_eps\_growth\_pct: 15.0    
 min\_roe\_pct: 15.0    
 min\_roce\_pct: 15.0    
 max\_debt\_to\_equity: 0.5    
 min\_promoter\_holding\_pct: 40.0    
 avoid\_days\_before\_earnings: 5    
 enforce\_long\_term\_growth\_group: true    
darvas\_box:    
 min\_box\_duration\_days: 5    
 max\_box\_duration\_days: 30    
 min\_box\_height\_pct: 3.0    
 max\_box\_height\_pct: 20.0    
 breakout\_volume\_multiplier: 1.5    
market\_trend\_filter:    
 index: "NIFTY 50"    
 moving\_averages: \[50, 200\]    
risk\_management:    
 account\_risk\_pct: 1.0    
 max\_capital\_per\_trade\_pct: 10.0    
 max\_sector\_exposure\_pct: 30.0    
 max\_concurrent\_positions: 10    
 stop\_loss\_buffer\_fraction\_inr: 0.05    
 max\_portfolio\_loss\_per\_trade\_pct: 10.0    
 kill\_switch\_daily\_loss\_limit\_inr: 50000    
 min\_structural\_r\_ratio: 3.0 \# Enforces structural risk-to-reward minimum of 1:3  

## **4\. Execution Step Sequence (lambda\_function.py)**

When the daily container provisions at 16:30 IST, the code must execute this precise sequential pipeline synchronously:

### **Step 4.1: Database S3 Synchronization (Pull)**

If system.storage.type is "s3", download darvas\_trading.db from the configured S3 bucket to the local container /tmp/ directory. Initialize the SQLite connection pool.

### **Step 4.2: Corporate Restructuring Adjustments**

Fetch overnight split/bonus adjustments from the data provider registry. If a corporate action is detected for an active symbol, apply the transformation matrix across all active box thresholds and scale outstanding entries inside the tracking ledger before starting historical scans.

### **Step 4.3: EOD Market Ingestion & Box Scanning**

1. Query historical end-of-day (EOD) data via the broker's API (e.g., Upstox API) up to today's completed daily candle. For backtesting contexts, official NSE Bhavcopy archives are used exclusively to avoid lookahead bias.  
2. Filter through the fundamental parameters and run the Sector Momentum Analyzer.  
3. Pass data through the Darvas Box State Machine to determine if a box top (B\_top) and box bottom (B\_bottom) have formed or shifted.

### **Step 4.4: Active Portfolio Risk Check**

Query current open positions and account equity balance. For any active positions that have formed a new upper box tier, calculate the potential risk percentage (Risk\_pct). If Risk\_pct \<= 10%, flag the position as eligible for a trailing stop-loss modification. If Risk\_pct \> 10%, retain the existing stop-loss.

### **Step 4.5: Candidate Ranking & Capital Allocation**

When multiple concurrent breakouts trigger, candidates are strictly filtered and evaluated based on the structural risk-to-reward ratio profile established at entry:

1. Structural R Target Objective Calculation: To prevent trailing box actions from introducing variability, the initial target is fixed objectively at the precise moment of entry breakout: Target Price \= B\_top \+ (B\_top \- B\_bottom)  
2. The 1:3 Structural Filter: Filter out any setup where the structural risk-to-reward ratio is less than 1:3. Only high-conviction candidates meeting \>= 1:3 move forward.  
3. Descending Sorting Strategy: Candidates are sorted in descending order based on their structural risk-to-reward metrics.  
4. Ordered Tie-Breaker Resolution: If two or more candidates have an identical structural R metric, apply the following deterministic rules in sequence:  
   * Tie-Breaker 1: Sector Relative Strength percentile versus the NIFTY 50 calculated over a rolling 63-day frame (prioritize the asset in the stronger sector).  
   * Tie-Breaker 2: Highest Breakout Volume Ratio.  
5. Position Sizing & Portfolio Limits: For approved candidates, determine the final share quantity by evaluating three concurrent risk limits and applying the smallest calculated value:  
   * Risk Cap: Based on risking a fixed percentage of total account equity (e.g., 1%).  
   * Capital Cap: Maximum absolute capital allocation allowed per trade.  
   * Portfolio Loss Cap: Maximum structural portfolio-level risk parameters.  
6. Execution Rule: Iterate through the sorted list and verify available liquidity. If the account lacks sufficient settled cash to fully fund the calculated allocation quantity, the trade must be skipped entirely rather than running a partial or fractional order.

### **Step 4.6: Broker GTT Order Sync (The Core Orchestrator)**

The system must actively reconcile the database state with the broker's active GTT order book via API using an Idempotent Reconciliation Protocol:

* Case A: Stock is in consolidation, no position open, and no GTT exists. Place a new Single GTT Buy Trigger Order at B\_top \+ 0.05 INR.  
* Case B: Stock box boundaries changed or historic high changed. Cancel the existing entry GTT order and replace it with an updated GTT Buy Trigger Order mapping the new boundaries.  
* Case C: Position was filled today. The entry GTT is automatically consumed by the broker. Detect this change, verify the execution fill price via the trade log, and immediately submit a GTT OCO (One-Cancels-Other) Position Management Order consisting of a Stop Loss leg at B\_bottom \- 0.05 INR and a Target leg matching the calculated objective.  
* Case D: New upper box tier confirmed and passes the 10% risk decider. Cancel the old GTT OCO order and submit an updated GTT OCO order with the Stop Loss shifted up to B\_bottom\_New \- 0.05 INR.

### **Step 4.7: Log Commits & Database Upload (Push)**

1. Write all state changes, GTT trigger alterations, system errors, and calculations to system\_logs and trade\_ledger.  
2. Close the SQLite database connection.  
3. If system.storage.type is "s3", upload the updated darvas\_trading.db file back to the S3 bucket.  
4. Print summary logs to stdout and safely exit the container process.

## **5\. Persistent State Ledger (SQLite Schema)**

The database schema contains strict tracking indices to facilitate fast serverless lookups and reproducible states for the independent improvement engine.

### **Table: active\_state\_registry**

CREATE TABLE active\_state\_registry (    
 symbol TEXT PRIMARY KEY,    
 box\_state TEXT CHECK(box\_state IN ('SCANNING', 'FORMING', 'VALIDATED', 'BREAKOUT')),    
 box\_top REAL,    
 box\_bottom REAL,    
 box\_start\_date DATE,    
 box\_end\_date DATE,    
 volume\_sma\_20 REAL,    
 last\_updated\_timestamp DATETIME DEFAULT CURRENT\_TIMESTAMP    
);

### **Table: trade\_ledger**

CREATE TABLE trade\_ledger (    
 trade\_id TEXT PRIMARY KEY,    
 timestamp DATETIME DEFAULT CURRENT\_TIMESTAMP,    
 symbol TEXT NOT NULL,    
 direction TEXT CHECK(direction IN ('BUY', 'SELL')),    
 price REAL NOT NULL,    
 quantity INTEGER NOT NULL,    
 current\_stop\_loss REAL,    
 current\_target REAL,    
 gtt\_buy\_trigger\_id TEXT,    
 gtt\_position\_oco\_id TEXT,    
 is\_active INTEGER CHECK(is\_active IN (0, 1)) DEFAULT 1    
);

### **Table: system\_logs**

CREATE TABLE system\_logs (    
 log\_id INTEGER PRIMARY KEY AUTOINCREMENT,    
 timestamp DATETIME DEFAULT CURRENT\_TIMESTAMP,    
 module TEXT NOT NULL, \-- e.g., 'GTT\_ORCHESTRATOR', 'LAMBDA\_MAIN'    
 level TEXT NOT NULL, \-- e.g., 'INFO', 'ERROR', 'SIGNAL'    
 symbol TEXT,    
 payload TEXT \-- Detailed structural JSON string context    
);

## **6\. On-Demand Modules**

### **6.1 Self-Improvement Advisor (advisor.py)**

This script operates completely independently of the serverless runtime lifecycle. It can be triggered locally or via an on-demand administrative task.

1. Pulls down the latest darvas\_trading.db from storage.  
2. Parses the historical transaction data inside trade\_ledger and cross-references it with matching error states in system\_logs.  
3. Uses an LLM execution framework (gpt-4o / claude-3-5-sonnet) to generate a diagnostic performance layout report.  
4. Returns optimization proposals in a structural JSON container.

## **7\. Non-Functional Execution Boundaries**

* Lambda Runtime Duration: The entire operational pipeline execution sequence from step 4.1 to step 4.7 must complete in under 180 seconds.  
* Memory Overhead Limits: Maximum execution footprint must fit entirely within a 512MB RAM Lambda configuration allocation.  
* Idempotency Assurance: If the lambda execution script runs multiple times in succession due to cron retry errors, the GTT reconciliation protocol must confirm identical parity values, log zero modifications, and make zero redundant API execution calls to the broker endpoints.