#!/usr/bin/env python3
"""Build self-contained HTML dashboard from all agentic-loop experiment logs."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "src" / "agentic-loop"
OUT_DIR = ROOT / "docs" / "backtest-dashboard"
OUT_HTML = OUT_DIR / "index.html"
MARCH_LOG = LOG_DIR / "march-yearwise-log.jsonl"

LOG_SOURCES: list[tuple[str, Path, str]] = [
    ("Optimization", LOG_DIR / "experiment-log.jsonl", "optimization"),
    ("Box shape", LOG_DIR / "box-shape-tuning-log.jsonl", "box_shape"),
    ("Target setting", LOG_DIR / "target-experiments-log.jsonl", "target"),
    ("March yearwise", MARCH_LOG, "march"),
]

WINDOW_2Y_START = "2024-06-01"
WINDOW_2Y_END = "2026-06-19"


def _scrub_path(p: str | None) -> str:
    if not p:
        return ""
    s = str(p).replace("\\", "/")
    s = re.sub(r"^[A-Za-z]:/", "", s)
    s = re.sub(r"^.*?/Swinger/", "", s)
    s = re.sub(r"^opt/swinger/[^/]+/current/", "", s)
    return s


def _scrub_record(raw: dict[str, Any], campaign: str) -> dict[str, Any]:
    r = dict(raw)
    r["campaign"] = campaign
    if "run_dir" in r:
        r["run_dir"] = _scrub_path(r.get("run_dir"))
    if "base_config" in r:
        r["base_config"] = _scrub_path(r.get("base_config"))
    for k in list(r.keys()):
        if isinstance(r[k], str) and ("@gmail" in r[k] or "password" in k.lower()):
            del r[k]
    return r


def _assign_window(r: dict[str, Any]) -> None:
    if r.get("period_label"):
        r["window_key"] = str(r["period_label"])
        r["window_label"] = str(r["period_label"])
    elif r.get("start_date") and r.get("end_date"):
        r["window_key"] = f"{r['start_date']}|{r['end_date']}"
        r["window_label"] = f"{r['start_date']} → {r['end_date']}"
    elif r.get("campaign") in ("optimization", "box_shape"):
        r["window_key"] = f"{WINDOW_2Y_START}|{WINDOW_2Y_END}"
        r["window_label"] = f"{WINDOW_2Y_START} → {WINDOW_2Y_END}"
    else:
        r["window_key"] = "unknown"
        r["window_label"] = "Unknown"


def _window_options(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for r in records:
        key = r.get("window_key")
        if key and key not in seen:
            seen[key] = str(r.get("window_label", key))
    items = [{"key": k, "label": v} for k, v in seen.items()]
    two_y_key = f"{WINDOW_2Y_START}|{WINDOW_2Y_END}"

    def sort_key(item: dict[str, str]) -> tuple:
        k = item["key"]
        if k == two_y_key:
            return (0, "")
        if k.startswith("Mar"):
            return (2, k)
        return (1, k)

    items.sort(key=sort_key)
    return items


def load_all_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, path, campaign in LOG_SOURCES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            r = _scrub_record(raw, campaign)
            r["campaign_label"] = label
            name = r.get("name") or r.get("config_id", "")
            start = r.get("start_date", "")
            key = f"{campaign}:{name}:{start}:{r.get('iteration', '')}"
            if key in seen:
                continue
            seen.add(key)
            if "cagr" in r:
                r["cagr_pct"] = round(100 * float(r["cagr"]), 2)
            if "max_drawdown_pct" in r:
                r["dd_pct"] = round(float(r["max_drawdown_pct"]), 2)
            _assign_window(r)
            records.append(r)
    return records


def _insight(rec: dict[str, Any]) -> str:
    cagr = rec.get("cagr_pct", 0)
    dd = rec.get("dd_pct", 0)
    trades = rec.get("total_closed_trades", "?")
    campaign = rec.get("campaign", "")
    name = rec.get("name") or rec.get("config_id", "run")
    parts: list[str] = []

    if campaign == "optimization" and name == "reset_loose_4.0":
        parts.append("Breakout reset 4% unlocked most of the strategy's edge vs default 2%.")
    if campaign == "box_shape" and "dur_min_box_duration_days_4" in name:
        parts.append("Shortening min box duration to 4d lifted CAGR above all other box knobs.")
    if campaign == "target" and name.startswith("static_target_1.2"):
        parts.append("1.2× static target peaked on recent 2Y; higher multipliers hurt that window.")
    if campaign == "target" and "dynamic_atr" in name:
        parts.append("Dynamic ATR band rarely changes outcomes — ratchet gate often blocks updates.")
    if campaign == "march":
        pl = rec.get("period_label", "")
        if cagr >= 25:
            parts.append(f"Strong fiscal year ({pl}) — momentum-friendly regime.")
        elif cagr < 5:
            parts.append(f"Weak fiscal year ({pl}) — consider defensive posture or fewer entries.")
    if dd <= 2.0 and cagr >= 20:
        parts.append("Elite risk-adjusted profile for this window.")
    elif dd > 4:
        parts.append("Elevated drawdown — size down or tighten filters in similar regimes.")

    if not parts:
        ratio = cagr / max(dd, 0.1)
        if ratio > 10:
            parts.append(f"Solid return per unit drawdown (CAGR/DD ≈ {ratio:.1f}).")
        else:
            parts.append(f"{trades} closed trades; win rate {100 * float(rec.get('win_rate') or 0):.0f}%.")
    return " ".join(parts)


def _top_two_2y(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pool = [
        r
        for r in records
        if r.get("start_date") == WINDOW_2Y_START
        and r.get("end_date", "").startswith("2026-06")
        and r.get("cagr_pct") is not None
        and r.get("campaign") in ("optimization", "box_shape", "target")
    ]
    pool.sort(key=lambda r: (-float(r.get("cagr_pct", 0)), float(r.get("dd_pct", 99))))
    return pool[:2]


def build_html(records: list[dict[str, Any]]) -> str:
    for r in records:
        r["insight"] = _insight(r)

    windows = _window_options(records)
    data_json = json.dumps(records, indent=None)
    windows_json = json.dumps(windows, indent=None)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Swinger Backtest Intelligence</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0b0f17;
      --card: #121826;
      --border: #1e293b;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --green: #34d399;
      --amber: #fbbf24;
      --rose: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(160deg, #0b0f17 0%, #111827 50%, #0f172a 100%);
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      padding: 2rem 2rem 1rem;
      border-bottom: 1px solid var(--border);
      background: rgba(15,23,42,0.8);
      backdrop-filter: blur(8px);
    }}
    h1 {{ margin: 0 0 0.25rem; font-size: 1.75rem; font-weight: 600; }}
    .subtitle {{ color: var(--muted); font-size: 0.95rem; }}
    main {{ padding: 1.5rem 2rem 3rem; max-width: 1400px; margin: 0 auto; }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .kpi {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem 1.25rem;
    }}
    .kpi .val {{ font-size: 1.5rem; font-weight: 700; color: var(--accent); }}
    .kpi .lbl {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 1.25rem;
      margin-bottom: 1.5rem;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.25rem;
      box-shadow: 0 4px 24px rgba(0,0,0,0.25);
    }}
    .card h2 {{ margin: 0 0 1rem; font-size: 1rem; font-weight: 600; color: var(--muted); }}
    .champion {{
      border-color: var(--accent);
      background: linear-gradient(135deg, #121826 0%, #0f2744 100%);
    }}
    .champion h3 {{ margin: 0 0 0.5rem; color: var(--accent); }}
    .champion .metrics {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 0.75rem 0; }}
    .champion .metrics span {{ font-size: 0.9rem; }}
    .champion p {{ margin: 0; font-size: 0.85rem; color: var(--muted); line-height: 1.5; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8rem;
    }}
    th, td {{ padding: 0.5rem 0.6rem; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-weight: 500; }}
    tr:hover td {{ background: rgba(56,189,248,0.06); }}
    .tag {{
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 6px;
      font-size: 0.7rem;
      font-weight: 600;
    }}
    .tag-opt {{ background: #1e3a5f; color: #7dd3fc; }}
    .tag-box {{ background: #14532d; color: #86efac; }}
    .tag-tgt {{ background: #4c1d95; color: #c4b5fd; }}
    .tag-mar {{ background: #713f12; color: #fde68a; }}
    .filters {{ margin-bottom: 1rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }}
    .filters button {{
      background: var(--card);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.4rem 0.9rem;
      border-radius: 8px;
      cursor: pointer;
      font-size: 0.8rem;
    }}
    .filters button.active {{ border-color: var(--accent); color: var(--accent); }}
    .section-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }}
    .section-head h2 {{ margin: 0; font-size: 1.1rem; font-weight: 600; color: var(--text); }}
    .window-filters label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-right: 0.5rem; }}
    .insight-cell {{ max-width: 280px; color: var(--muted); line-height: 1.4; }}
    footer {{ text-align: center; padding: 2rem; color: var(--muted); font-size: 0.75rem; }}
  </style>
</head>
<body>
  <header>
    <h1>Swinger Backtest Intelligence</h1>
    <p class="subtitle">Aggregated experiments — optimization, box shape, targets, March-yearwise · Generated {date.today().isoformat()}</p>
  </header>
  <main>
    <div class="section-head">
      <h2>Overview</h2>
      <div class="window-filters filters" id="windowFilters"></div>
    </div>
    <div class="kpis" id="kpis"></div>
    <div class="grid" id="champions"></div>
    <div class="grid" id="chartGrid">
      <div class="card"><h2 id="scatterTitle">CAGR vs Max Drawdown</h2><canvas id="scatterChart" height="260"></canvas></div>
      <div class="card"><h2 id="barTitle">Top 15 by CAGR</h2><canvas id="barChart" height="260"></canvas></div>
      <div class="card"><h2>March-yearwise (CAGR %)</h2><canvas id="marchChart" height="260"></canvas></div>
      <div class="card"><h2>Campaign distribution</h2><canvas id="pieChart" height="260"></canvas></div>
    </div>
    <div class="card">
      <h2>All experiments</h2>
      <div class="filters" id="filters"></div>
      <div style="overflow-x:auto">
        <table id="expTable"><thead><tr>
          <th>Campaign</th><th>Name</th><th>Window</th><th>CAGR</th><th>Max DD</th><th>Trades</th><th>Insight</th>
        </tr></thead><tbody></tbody></table>
      </div>
    </div>
  </main>
  <footer>Paths scrubbed · no credentials · open docs/backtest-dashboard/index.html locally</footer>
  <script>
    const DATA = {data_json};
    const WINDOWS = {windows_json};

    let activeWindow = 'all';
    let activeCampaign = null;
    const charts = {{}};

    const tagClass = c => ({{optimization:'tag-opt',box_shape:'tag-box',target:'tag-tgt',march:'tag-mar'}}[c] || 'tag-opt');

    function byWindow(rows) {{
      if (activeWindow === 'all') return rows;
      return rows.filter(r => r.window_key === activeWindow);
    }}

    function renderKpis(rows) {{
      const withCagr = rows.filter(r => r.cagr_pct != null);
      const best = [...withCagr].sort((a,b) => b.cagr_pct - a.cagr_pct)[0];
      const lowestDd = [...withCagr].sort((a,b) => a.dd_pct - b.dd_pct)[0];
      const kpis = document.getElementById('kpis');
      kpis.innerHTML = '';
      const winLabel = activeWindow === 'all' ? 'All windows' : (WINDOWS.find(w => w.key === activeWindow)?.label || activeWindow);
      [
        ['Experiments', rows.length],
        ['Best CAGR', best ? best.cagr_pct + '%' : '—'],
        ['Lowest DD', lowestDd ? lowestDd.dd_pct + '%' : '—'],
        ['Window', winLabel.length > 22 ? winLabel.slice(0, 20) + '…' : winLabel],
      ].forEach(([lbl, val]) => {{
        kpis.innerHTML += `<div class="kpi"><div class="val">${{val}}</div><div class="lbl">${{lbl}}</div></div>`;
      }});
    }}

    function renderChampions(rows) {{
      const pool = rows.filter(r => r.cagr_pct != null).sort((a,b) => b.cagr_pct - a.cagr_pct || a.dd_pct - b.dd_pct);
      const top = pool.slice(0, 2);
      const champs = document.getElementById('champions');
      champs.innerHTML = '';
      if (!top.length) {{
        champs.innerHTML = '<div class="card"><p style="margin:0;color:var(--muted)">No experiments for this window.</p></div>';
        return;
      }}
      top.forEach((r, i) => {{
        champs.innerHTML += `<div class="card champion">
          <h3>#${{i+1}} ${{r.name || r.config_id}} <span class="tag ${{tagClass(r.campaign)}}">${{r.campaign_label}}</span></h3>
          <div class="metrics">
            <span><strong style="color:var(--green)">${{r.cagr_pct}}%</strong> CAGR</span>
            <span><strong style="color:var(--amber)">${{r.dd_pct}}%</strong> Max DD</span>
            <span>${{r.total_closed_trades ?? '—'}} trades</span>
          </div>
          <p style="font-size:0.8rem;color:var(--muted);margin:0 0 0.5rem">${{r.window_label}}</p>
          <p>${{r.insight || ''}}</p>
        </div>`;
      }});
    }}

    function destroyChart(id) {{
      if (charts[id]) {{ charts[id].destroy(); delete charts[id]; }}
    }}

    function renderCharts(rows) {{
      const withCagr = rows.filter(r => r.cagr_pct != null);
      const winLabel = activeWindow === 'all' ? 'all windows' : (WINDOWS.find(w => w.key === activeWindow)?.label || '');
      document.getElementById('scatterTitle').textContent = 'CAGR vs Max Drawdown' + (winLabel ? ` (${{winLabel}})` : '');
      document.getElementById('barTitle').textContent = 'Top 15 by CAGR' + (winLabel ? ` (${{winLabel}})` : '');

      destroyChart('scatter');
      charts.scatter = new Chart(document.getElementById('scatterChart'), {{
        type: 'scatter',
        data: {{
          datasets: [{{
            label: 'Runs',
            data: withCagr.map(r => ({{x: r.dd_pct, y: r.cagr_pct, label: r.name || r.config_id}})),
            backgroundColor: 'rgba(56,189,248,0.55)',
            borderColor: '#38bdf8',
          }}]
        }},
        options: {{
          plugins: {{ tooltip: {{ callbacks: {{ label: ctx => ctx.raw.label + ': ' + ctx.raw.y + '% @ ' + ctx.raw.x + '% DD' }} }} }},
          scales: {{
            x: {{ title: {{ display: true, text: 'Max DD %', color: '#94a3b8' }}, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
            y: {{ title: {{ display: true, text: 'CAGR %', color: '#94a3b8' }}, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
          }}
        }}
      }});

      const top15 = [...withCagr].sort((a,b) => b.cagr_pct - a.cagr_pct).slice(0, 15);
      destroyChart('bar');
      charts.bar = new Chart(document.getElementById('barChart'), {{
        type: 'bar',
        data: {{
          labels: top15.map(r => (r.name || r.config_id || '').slice(0, 28)),
          datasets: [{{
            label: 'CAGR %',
            data: top15.map(r => r.cagr_pct),
            backgroundColor: top15.map((_,i) => `hsla(${{200 + i*8}},70%,55%,0.75)`),
          }}]
        }},
        options: {{
          indexAxis: 'y',
          plugins: {{ legend: {{ display: false }} }},
          scales: {{ x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }}, y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ display: false }} }} }}
        }}
      }});

      const march = DATA.filter(r => r.campaign === 'march');
      destroyChart('march');
      if (march.length) {{
        const configs = [...new Set(march.map(r => r.config_id))];
        const periods = [...new Set(march.map(r => r.period_label))];
        const datasets = configs.map((cid, i) => ({{
          label: cid,
          data: periods.map(p => {{
            const r = march.find(x => x.config_id === cid && x.period_label === p);
            return r ? r.cagr_pct : null;
          }}),
          borderColor: ['#38bdf8','#34d399','#fbbf24'][i % 3],
          backgroundColor: ['rgba(56,189,248,0.2)','rgba(52,211,153,0.2)','rgba(251,191,36,0.2)'][i % 3],
          tension: 0.3,
        }}));
        charts.march = new Chart(document.getElementById('marchChart'), {{
          type: 'line',
          data: {{ labels: periods, datasets }},
          options: {{
            scales: {{
              x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }}, grid: {{ color: '#1e293b' }} }},
              y: {{ title: {{ display: true, text: 'CAGR %', color: '#94a3b8' }}, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
            }}
          }}
        }});
      }}

      const campCounts = {{}};
      rows.forEach(r => {{ campCounts[r.campaign_label] = (campCounts[r.campaign_label]||0)+1; }});
      destroyChart('pie');
      charts.pie = new Chart(document.getElementById('pieChart'), {{
        type: 'doughnut',
        data: {{
          labels: Object.keys(campCounts),
          datasets: [{{ data: Object.values(campCounts), backgroundColor: ['#38bdf8','#34d399','#a78bfa','#fbbf24'] }}]
        }},
        options: {{ plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }} }}
      }});
    }}

    function renderTable() {{
      const tbody = document.querySelector('#expTable tbody');
      tbody.innerHTML = '';
      let rows = byWindow(DATA);
      if (activeCampaign) rows = rows.filter(r => r.campaign === activeCampaign);
      rows.sort((a,b) => (b.cagr_pct||0)-(a.cagr_pct||0)).forEach(r => {{
        tbody.innerHTML += `<tr>
          <td><span class="tag ${{tagClass(r.campaign)}}">${{r.campaign_label}}</span></td>
          <td>${{r.name || r.config_id || '—'}}</td>
          <td>${{r.window_label || '—'}}</td>
          <td>${{r.cagr_pct != null ? r.cagr_pct+'%' : '—'}}</td>
          <td>${{r.dd_pct != null ? r.dd_pct+'%' : '—'}}</td>
          <td>${{r.total_closed_trades ?? '—'}}</td>
          <td class="insight-cell">${{r.insight || ''}}</td>
        </tr>`;
      }});
    }}

    function refresh() {{
      const rows = byWindow(DATA);
      renderKpis(rows);
      renderChampions(rows);
      renderCharts(rows);
      renderTable();
    }}

    const windowFilters = document.getElementById('windowFilters');
    const allBtn = document.createElement('button');
    allBtn.textContent = 'All windows';
    allBtn.classList.add('active');
    allBtn.onclick = () => {{
      activeWindow = 'all';
      windowFilters.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      allBtn.classList.add('active');
      refresh();
    }};
    windowFilters.appendChild(allBtn);
    WINDOWS.forEach(w => {{
      const b = document.createElement('button');
      b.textContent = w.label.length > 36 ? w.label.slice(0, 34) + '…' : w.label;
      b.title = w.label;
      b.onclick = () => {{
        activeWindow = w.key;
        windowFilters.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        refresh();
      }};
      windowFilters.appendChild(b);
    }});

    const filters = document.getElementById('filters');
    ['all','optimization','box_shape','target','march'].forEach((f,i) => {{
      const b = document.createElement('button');
      b.textContent = f === 'all' ? 'All campaigns' : f.replace('_',' ');
      if (i===0) b.classList.add('active');
      b.onclick = () => {{
        filters.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        activeCampaign = f === 'all' ? null : f;
        renderTable();
      }};
      filters.appendChild(b);
    }});

    refresh();
  </script>
</body>
</html>"""


def main() -> int:
    records = load_all_records()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = build_html(records)
    OUT_HTML.write_text(html, encoding="utf-8")
    (OUT_DIR / "data.json").write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8"
    )
    print(f"Wrote {OUT_HTML} ({len(records)} experiments)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
