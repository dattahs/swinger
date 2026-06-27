# **Product Requirement Document (PRD): VPS-Hosted Darvas Box Trading Platform**

> **Binding spec:** [`REQUIREMENTS_v1.md`](REQUIREMENTS_v1.md) v1.3 supersedes this sketch where they diverge.  
> **Change log (2026-06-25):** Live deployment moved from AWS Lambda + DynamoDB to **single VPS** with local SQLite.

## **1. Document Control**

* Title: VPS-Hosted Automated Darvas Box Swing Trading Platform for Indian Markets  
* Version: 3.1 (VPS, GTT-Driven & Structural R Constrained)  
* Date: 2026-06-25  
* Author: Product Owner / Lead System Architect  
* Target Engine: AI Code Generation / Cursor Vibe Coding

## **2. VPS Architectural Topology**

The platform runs on a **single Linux VPS** with cron-triggered daily execution and local SQLite persistence — no Lambda, no S3 sync, no DynamoDB for v1.

```
+---------------------------------------------------------------------------------+
| DAILY POST-MARKET VPS PIPELINE (16:30 IST)                                      |
|                                                                                 |
| [cron/systemd] --> scripts/run_live.py --> LiveRunner                         |
|                           |                          |                          |
|                           v                          v                          |
|                  data/processed/              Darvas & Risk Engine              |
|                  swinger_data.db                      |                         |
|                           |                          v                          |
|                           +---------> data/live/swinger_live.db               |
|                                              (SQLite, single-writer)            |
|                                                      |                          |
|                                                      v                          |
|                                            Broker GTT Orchestrator              |
+---------------------------------------------------------------------------------+
                                      |
                                      v
                    +-------------------------------------------+
                    |               BROKER CLOUD (Upstox)       |
                    |         (Persistent GTT Orders)           |
                    +-------------------------------------------+

+---------------------------------------------------------------------------------+
| ON-DEMAND ADVISORY MODULE (MANUAL RUN)                                          |
| [CLI] ---> Reads SQLite state ---> Analyzes trade_ledger ---> JSON report       |
+---------------------------------------------------------------------------------+
```

### **2.1 Component Decomposition**

1. **Daily Core Execution (`scripts/run_live.py` + `live/runner.py`):**  
   * VPS cron or `systemd` timer at 16:30 IST.  
   * Loads EOD bars from local data lake, reconciles broker, runs engine, places GTTs, persists to `data/live/swinger_live.db`.  
2. **Advisor Module (`advisor.py`):** — build last; deterministic JSON in v1.  
3. **State Persistence:** Local SQLite on VPS filesystem. Use `flock` to prevent overlapping runs.

## **3. System Configuration Schema**

See REQUIREMENTS v1.3 Section 13. VPS essentials:

```yaml
system:
  storage:
    live_backend: sqlite
  networking:
    vps_public_ip: ""   # register in Upstox developer console

live:
  local_db_path: ./data/live/swinger_live.db
  paper_mode: true
```

## **4. Execution Step Sequence (`LiveRunner`)**

Pipeline at 16:30 IST (see REQUIREMENTS §9 for full detail):

1. Validate auth / refresh token  
2. Corporate-action adjustments  
3. Broker reconcile → `trade_ledger`  
4. Equity + kill switch  
5. `run_daily_strategy_iteration()`  
6. GTT execution (Cases A–D)  
7. Persist SQLite + notify  

**Removed (Lambda era):** S3 pull/push of database file before/after each run.

## **5. Persistent State Ledger**

SQLite on VPS — `SqliteLiveRepository` shares schema with backtest tables (`state_registry`, `trade_ledger`, `system_logs`, `decision_log`). See REQUIREMENTS §5.

## **6. On-Demand Modules**

### **6.1 Advisor (`advisor.py`)**

Independent CLI; reads live SQLite; deterministic JSON grid in v1. LLM narrative layer is v2.

## **7. Non-Functional Execution Boundaries**

* **Runtime:** Complete daily pipeline within **15 minutes** on a 2-vCPU / 4 GB VPS.  
* **Concurrency:** Only one live run at a time (`flock` or `systemd` timer guard).  
* **Idempotency:** Retried runs must not double-place GTTs — idempotency keys on every action.  
* **Static IP:** VPS public IP registered in broker developer console (SEBI retail algo requirement).

**(v2 / deprecated):** AWS Lambda, EventBridge, DynamoDB, ECR container packaging.
