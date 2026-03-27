#!/usr/bin/env python3
"""
SEC EDGAR Pipeline Dashboard

Local web UI for browsing extraction results, costs, review queue, and audit history.
Reads from the output/ directory — no database needed.

    pip install flask
    python dashboard.py
    # Open http://localhost:5000
"""

import json
import glob
import os
from datetime import datetime
from flask import Flask, render_template_string, request, abort

app = Flask(__name__)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


# ─── Data Loaders ────────────────────────────────────────────────────

def load_json(filename):
    path = os.path.join(OUTPUT_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_all_extractions():
    """Load all individual company extraction files."""
    results = []
    skip = {"batch_results.json", "review_queue.json"}
    for path in glob.glob(os.path.join(OUTPUT_DIR, "*.json")):
        fname = os.path.basename(path)
        if fname in skip or "_comparison.json" in fname:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if "data" in data and "status" in data:
                data["_filename"] = fname
                data["_mtime"] = os.path.getmtime(path)
                results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    results.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
    return results


def load_comparisons():
    results = []
    for path in glob.glob(os.path.join(OUTPUT_DIR, "*_comparison.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["_filename"] = os.path.basename(path)
            data["_mtime"] = os.path.getmtime(path)
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    results.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
    return results


def load_review_queue():
    return load_json("review_queue.json") or {
        "review_queue": {"total_processed": 0, "needs_review": 0, "passed": 0, "items": []},
        "passed": []
    }


def load_audit_log(limit=100, offset=0, event_filter=None):
    path = os.path.join(OUTPUT_DIR, "audit.log")
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if event_filter and entry.get("event") != event_filter:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    entries.reverse()
    return entries[offset:offset + limit], len(entries)


def aggregate_costs(extractions):
    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_savings = 0.0
    total_calls = 0
    for ext in extractions:
        cost = ext.get("cost", {})
        if isinstance(cost, dict):
            total_cost += cost.get("total_cost_usd", 0)
            total_input += cost.get("input_tokens", 0)
            total_output += cost.get("output_tokens", 0)
            total_cache_savings += cost.get("cache_savings_usd", 0)
            total_calls += cost.get("calls", 0)
    return {
        "total_cost": total_cost,
        "total_input": total_input,
        "total_output": total_output,
        "total_cache_savings": total_cache_savings,
        "total_calls": total_calls,
        "run_count": len(extractions),
    }


# ─── Helpers ─────────────────────────────────────────────────────────

def fmt_money(val):
    if val is None:
        return "N/A"
    return f"${val:,.2f}"

def fmt_tokens(val):
    if val is None:
        return "N/A"
    return f"{val:,}"

def conf_color(conf):
    if conf is None:
        return "#666"
    if conf >= 0.7:
        return "#4ade80"
    if conf >= 0.5:
        return "#fbbf24"
    return "#f87171"

def conf_pct(conf):
    return int((conf or 0) * 100)

def time_ago(mtime):
    delta = datetime.now().timestamp() - mtime
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta/60)}m ago"
    if delta < 86400:
        return f"{int(delta/3600)}h ago"
    return f"{int(delta/86400)}d ago"


app.jinja_env.globals.update(
    fmt_money=fmt_money, fmt_tokens=fmt_tokens,
    conf_color=conf_color, conf_pct=conf_pct, time_ago=time_ago,
)


# ─── CSS ─────────────────────────────────────────────────────────────

CSS = """
:root {
    --bg: #0f1117; --surface: #1a1d2e; --surface2: #232738;
    --border: #2d3148; --text: #e0e0e8; --text2: #8b8fa3;
    --accent: #6366f1; --accent2: #818cf8; --green: #4ade80;
    --yellow: #fbbf24; --red: #f87171; --font: 'Segoe UI', system-ui, sans-serif;
    --mono: 'Cascadia Code', 'Fira Code', monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: var(--font); line-height: 1.6; }
a { color: var(--accent2); text-decoration: none; }
a:hover { text-decoration: underline; }

nav {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 0 2rem; display: flex; align-items: center; height: 56px;
    position: sticky; top: 0; z-index: 100;
}
nav .brand { font-weight: 700; font-size: 1.1rem; margin-right: 2rem; color: #fff; }
nav a { color: var(--text2); padding: 0.5rem 1rem; border-radius: 6px; font-size: 0.9rem; }
nav a:hover, nav a.active { color: #fff; background: var(--surface2); text-decoration: none; }

.container { max-width: 1200px; margin: 2rem auto; padding: 0 1.5rem; }
h1 { font-size: 1.5rem; margin-bottom: 1.5rem; color: #fff; }
h2 { font-size: 1.2rem; margin-bottom: 1rem; color: #fff; }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 1.25rem; transition: border-color 0.2s;
}
.card:hover { border-color: var(--accent); }
.card .label { font-size: 0.8rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.05em; }
.card .value { font-size: 1.8rem; font-weight: 700; color: #fff; font-family: var(--mono); margin-top: 0.25rem; }
.card .sub { font-size: 0.85rem; color: var(--text2); margin-top: 0.25rem; }

table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 10px; overflow: hidden; }
th { text-align: left; padding: 0.75rem 1rem; background: var(--surface2); color: var(--text2);
     font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }
td { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--surface2); }

.badge {
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 12px;
    font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
}
.badge-success { background: rgba(74,222,128,0.15); color: var(--green); }
.badge-partial { background: rgba(251,191,36,0.15); color: var(--yellow); }
.badge-failed { background: rgba(248,113,113,0.15); color: var(--red); }

.conf-bar {
    display: inline-flex; align-items: center; gap: 0.5rem;
}
.conf-bar .bar {
    width: 80px; height: 8px; background: var(--surface2); border-radius: 4px; overflow: hidden;
}
.conf-bar .bar .fill { height: 100%; border-radius: 4px; transition: width 0.3s; }

.metric {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem 1.25rem; margin-bottom: 0.75rem;
    display: flex; justify-content: space-between; align-items: center;
}
.metric .name { font-size: 0.85rem; color: var(--text2); }
.metric .val { font-family: var(--mono); font-size: 1.1rem; font-weight: 600; color: #fff; }
.metric .unit { font-size: 0.8rem; color: var(--text2); margin-left: 0.25rem; }
.metric .label-tag { font-size: 0.75rem; color: var(--accent2); margin-left: 0.5rem; }

.section { margin-bottom: 2rem; }
.empty { text-align: center; padding: 3rem; color: var(--text2); font-size: 1.1rem; }

.risk { background: var(--surface); border-left: 3px solid var(--accent); padding: 0.75rem 1rem; margin-bottom: 0.5rem; border-radius: 0 6px 6px 0; }
.risk .cat { font-size: 0.75rem; color: var(--accent2); text-transform: uppercase; }

.log-entry {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 0.75rem 1rem; margin-bottom: 0.5rem; font-family: var(--mono); font-size: 0.85rem;
}
.log-entry .ts { color: var(--text2); font-size: 0.75rem; }
.log-entry .tool { color: var(--accent2); font-weight: 600; }
.log-entry .event { font-size: 0.7rem; padding: 0.1rem 0.4rem; border-radius: 4px; }
.ev-tool_call { background: rgba(99,102,241,0.2); color: var(--accent2); }
.ev-tool_result { background: rgba(74,222,128,0.15); color: var(--green); }
.ev-pii_blocked { background: rgba(248,113,113,0.15); color: var(--red); }

.filter-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
.filter-btn {
    padding: 0.4rem 0.8rem; border-radius: 6px; border: 1px solid var(--border);
    background: var(--surface); color: var(--text2); cursor: pointer; font-size: 0.85rem;
}
.filter-btn:hover, .filter-btn.active { border-color: var(--accent); color: #fff; background: var(--surface2); }

.cost-bar { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
.cost-bar .name { width: 160px; font-size: 0.85rem; color: var(--text2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cost-bar .bar { flex: 1; height: 20px; background: var(--surface2); border-radius: 4px; overflow: hidden; }
.cost-bar .bar .fill { height: 100%; background: var(--accent); border-radius: 4px; }
.cost-bar .amount { width: 80px; text-align: right; font-family: var(--mono); font-size: 0.85rem; }

.priority { font-family: var(--mono); font-weight: 700; }
.priority-high { color: var(--red); }
.priority-med { color: var(--yellow); }
.priority-low { color: var(--green); }

.notes { background: var(--surface2); border-radius: 6px; padding: 0.75rem 1rem; font-size: 0.9rem; color: var(--text2); margin-top: 1rem; }
"""


# ─── Templates ───────────────────────────────────────────────────────

BASE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }} | EDGAR Pipeline</title>
    <style>""" + CSS + """</style>
</head>
<body>
<nav>
    <span class="brand">EDGAR Pipeline</span>
    <a href="/" class="{{ 'active' if active=='overview' }}">Overview</a>
    <a href="/extractions" class="{{ 'active' if active=='extractions' }}">Extractions</a>
    <a href="/comparisons" class="{{ 'active' if active=='comparisons' }}">Comparisons</a>
    <a href="/review" class="{{ 'active' if active=='review' }}">Review</a>
    <a href="/costs" class="{{ 'active' if active=='costs' }}">Costs</a>
    <a href="/audit" class="{{ 'active' if active=='audit' }}">Audit</a>
</nav>
<div class="container">{{ content|safe }}</div>
</body></html>"""


def render(title, content, active=""):
    return render_template_string(BASE, title=title, content=content, active=active)


# ─── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def overview():
    extractions = load_all_extractions()
    costs = aggregate_costs(extractions)
    review = load_review_queue()
    rq = review.get("review_queue", {})
    success = sum(1 for e in extractions if e.get("status") == "success")
    failed = len(extractions) - success

    content = render_template_string("""
    <h1>Overview</h1>
    <div class="cards">
        <div class="card"><div class="label">Extractions</div><div class="value">{{ total }}</div></div>
        <div class="card"><div class="label">Success Rate</div><div class="value">{{ rate }}%</div>
            <div class="sub">{{ success }} passed, {{ failed }} failed</div></div>
        <div class="card"><div class="label">Total Cost</div><div class="value">{{ fmt_money(costs.total_cost) }}</div>
            <div class="sub">{{ costs.total_calls }} API calls</div></div>
        <div class="card"><div class="label">Cache Savings</div><div class="value">{{ fmt_money(costs.total_cache_savings) }}</div></div>
        <div class="card"><div class="label">Review Queue</div><div class="value">{{ rq.needs_review }}</div>
            <div class="sub">items flagged</div></div>
    </div>
    <h2>Recent Extractions</h2>
    {% if extractions %}
    <table>
        <tr><th>Company</th><th>Status</th><th>Revenue</th><th>Cost</th><th>When</th></tr>
        {% for e in extractions[:10] %}
        <tr>
            <td><a href="/extraction/{{ e._filename }}">{{ e.data.company_name|default('?') }}</a></td>
            <td><span class="badge badge-{{ e.status }}">{{ e.status }}</span></td>
            <td style="font-family:var(--mono)">{{ fmt_money(e.data.revenue.value) if e.data.revenue else 'N/A' }}
                {% if e.data.revenue %}<span style="color:var(--text2);font-size:0.8rem"> {{ e.data.revenue.unit }}</span>{% endif %}</td>
            <td style="font-family:var(--mono)">{{ fmt_money(e.cost.total_cost_usd) if e.cost else 'N/A' }}</td>
            <td style="color:var(--text2)">{{ time_ago(e._mtime) }}</td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div class="empty">No extractions yet. Run the pipeline first.</div>
    {% endif %}
    """, extractions=extractions, costs=costs, rq=rq,
         total=len(extractions), success=success, failed=failed,
         rate=int(success/len(extractions)*100) if extractions else 0)
    return render("Overview", content, "overview")


@app.route("/extractions")
def extractions_list():
    extractions = load_all_extractions()
    content = render_template_string("""
    <h1>Extractions</h1>
    {% if extractions %}
    <table>
        <tr><th>Company</th><th>Ticker</th><th>Period</th><th>Status</th><th>Revenue</th><th>Net Income</th><th>Cost</th><th>Iterations</th></tr>
        {% for e in extractions %}
        <tr>
            <td><a href="/extraction/{{ e._filename }}">{{ e.data.company_name|default('?') }}</a></td>
            <td style="font-family:var(--mono)">{{ e.data.ticker|default('') }}</td>
            <td>{{ e.data.period_end|default('') }}</td>
            <td><span class="badge badge-{{ e.status }}">{{ e.status }}</span></td>
            <td style="font-family:var(--mono)">{{ fmt_money(e.data.revenue.value) if e.data.revenue else 'N/A' }}</td>
            <td style="font-family:var(--mono)">{{ fmt_money(e.data.net_income.value) if e.data.net_income else 'N/A' }}</td>
            <td style="font-family:var(--mono)">{{ fmt_money(e.cost.total_cost_usd) if e.cost else '' }}</td>
            <td>{{ e.iterations|default('') }}</td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div class="empty">No extractions yet.</div>
    {% endif %}
    """, extractions=extractions)
    return render("Extractions", content, "extractions")


@app.route("/extraction/<filename>")
def extraction_detail(filename):
    if "/" in filename or "\\" in filename:
        abort(400)
    data = load_json(filename)
    if not data or "data" not in data:
        abort(404)
    d = data["data"]
    cost = data.get("cost", {})
    fields = [
        ("Revenue", d.get("revenue", {})),
        ("Net Income", d.get("net_income", {})),
        ("Total Assets", d.get("total_assets", {})),
        ("Total Liabilities", d.get("total_liabilities", {})),
    ]
    content = render_template_string("""
    <h1>{{ d.company_name|default('Unknown') }}
        {% if d.ticker %}<span style="color:var(--text2);font-size:1rem;margin-left:0.5rem">({{ d.ticker }})</span>{% endif %}
    </h1>
    <div class="cards" style="margin-bottom:2rem">
        <div class="card"><div class="label">Form Type</div><div class="value" style="font-size:1.2rem">{{ d.form_type|default('?') }}</div></div>
        <div class="card"><div class="label">Period End</div><div class="value" style="font-size:1.2rem">{{ d.period_end|default('?') }}</div></div>
        <div class="card"><div class="label">Fiscal Year</div><div class="value" style="font-size:1.2rem">{{ d.fiscal_year|default('?') }}</div></div>
        <div class="card"><div class="label">Iterations</div><div class="value" style="font-size:1.2rem">{{ iterations }}</div></div>
    </div>

    <div class="section">
        <h2>Financial Data</h2>
        {% for name, field in fields %}
        <div class="metric">
            <div>
                <div class="name">{{ name }}</div>
                <div><span class="val">{{ fmt_money(field.value) if field.value is not none else 'N/A' }}</span>
                    <span class="unit">{{ field.unit|default('') }}</span>
                    {% if field.label %}<span class="label-tag">{{ field.label }}</span>{% endif %}
                </div>
                {% if field.source_section %}<div style="font-size:0.75rem;color:var(--text2);margin-top:0.25rem">Source: {{ field.source_section }}</div>{% endif %}
            </div>
            <div class="conf-bar">
                <div class="bar"><div class="fill" style="width:{{ conf_pct(field.confidence) }}%;background:{{ conf_color(field.confidence) }}"></div></div>
                <span style="font-family:var(--mono);font-size:0.85rem;color:{{ conf_color(field.confidence) }}">{{ '%.0f'|format((field.confidence or 0)*100) }}%</span>
            </div>
        </div>
        {% endfor %}

        <div class="metric">
            <div>
                <div class="name">EPS</div>
                <div><span class="val">{{ d.eps.value if d.eps and d.eps.value is not none else 'N/A' }}</span>
                    {% if d.eps and d.eps.diluted %}<span class="unit">(diluted)</span>{% endif %}
                </div>
            </div>
            {% if d.eps %}
            <div class="conf-bar">
                <div class="bar"><div class="fill" style="width:{{ conf_pct(d.eps.confidence) }}%;background:{{ conf_color(d.eps.confidence) }}"></div></div>
                <span style="font-family:var(--mono);font-size:0.85rem;color:{{ conf_color(d.eps.confidence) }}">{{ '%.0f'|format((d.eps.confidence or 0)*100) }}%</span>
            </div>
            {% endif %}
        </div>
    </div>

    {% if d.risk_factors %}
    <div class="section">
        <h2>Risk Factors ({{ d.risk_factors|length }})</h2>
        {% for rf in d.risk_factors %}
        <div class="risk">
            <div class="cat">{{ rf.category }}{% if rf.category_detail %} &mdash; {{ rf.category_detail }}{% endif %}</div>
            <div>{{ rf.title }}</div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if d.conflict_detected %}
    <div class="card" style="border-color:var(--red);margin-bottom:1rem">
        <div class="label" style="color:var(--red)">Conflict Detected</div>
        <div style="margin-top:0.5rem">{{ d.notes|default('See extraction data') }}</div>
    </div>
    {% elif d.notes %}
    <div class="notes">{{ d.notes }}</div>
    {% endif %}

    {% if cost %}
    <div class="section" style="margin-top:2rem">
        <h2>Cost Breakdown</h2>
        <div class="cards">
            <div class="card"><div class="label">Total Cost</div><div class="value" style="font-size:1.2rem">{{ fmt_money(cost.total_cost_usd) }}</div></div>
            <div class="card"><div class="label">API Calls</div><div class="value" style="font-size:1.2rem">{{ cost.calls|default(0) }}</div></div>
            <div class="card"><div class="label">Input Tokens</div><div class="value" style="font-size:1.2rem">{{ fmt_tokens(cost.input_tokens) }}</div></div>
            <div class="card"><div class="label">Output Tokens</div><div class="value" style="font-size:1.2rem">{{ fmt_tokens(cost.output_tokens) }}</div></div>
            <div class="card"><div class="label">Cache Read</div><div class="value" style="font-size:1.2rem">{{ fmt_tokens(cost.cache_read_tokens) }}</div></div>
            <div class="card"><div class="label">Cache Savings</div><div class="value" style="font-size:1.2rem">{{ fmt_money(cost.cache_savings_usd) }}</div></div>
        </div>
    </div>
    {% endif %}
    """, d=d, fields=fields, cost=cost, iterations=data.get("iterations", "?"))
    return render(d.get("company_name", "Extraction"), content, "extractions")


@app.route("/comparisons")
def comparisons_list():
    comparisons = load_comparisons()
    content = render_template_string("""
    <h1>Comparison Reports</h1>
    {% if comparisons %}
    <table>
        <tr><th>Report</th><th>Status</th><th>Iterations</th><th>Cost</th><th>When</th></tr>
        {% for c in comparisons %}
        <tr>
            <td><a href="/comparison/{{ c._filename }}">{{ c._filename.replace('_comparison.json','').replace('_',' ') }}</a></td>
            <td><span class="badge badge-{{ c.status|default('unknown') }}">{{ c.status|default('?') }}</span></td>
            <td>{{ c.iterations|default('') }}</td>
            <td style="font-family:var(--mono)">{{ fmt_money(c.cost.total_cost_usd) if c.cost else '' }}</td>
            <td style="color:var(--text2)">{{ time_ago(c._mtime) }}</td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div class="empty">No comparison reports yet. Run with multiple companies.</div>
    {% endif %}
    """, comparisons=comparisons)
    return render("Comparisons", content, "comparisons")


@app.route("/comparison/<filename>")
def comparison_detail(filename):
    if "/" in filename or "\\" in filename:
        abort(400)
    data = load_json(filename)
    if not data:
        abort(404)
    content = render_template_string("""
    <h1>{{ filename.replace('_comparison.json','').replace('_',' ') }}</h1>
    <div class="card" style="margin-bottom:2rem">
        <div style="white-space:pre-wrap;line-height:1.8">{{ data.summary|default('No summary available.') }}</div>
    </div>
    {% if data.cost %}
    <div class="cards">
        <div class="card"><div class="label">Cost</div><div class="value" style="font-size:1.2rem">{{ fmt_money(data.cost.total_cost_usd) }}</div></div>
        <div class="card"><div class="label">Iterations</div><div class="value" style="font-size:1.2rem">{{ data.iterations|default('?') }}</div></div>
    </div>
    {% endif %}
    """, data=data, filename=filename)
    return render("Comparison", content, "comparisons")


@app.route("/review")
def review_queue():
    review = load_review_queue()
    rq = review.get("review_queue", {})
    items = rq.get("items", [])
    content = render_template_string("""
    <h1>Review Queue</h1>
    <div class="cards" style="margin-bottom:2rem">
        <div class="card"><div class="label">Total Processed</div><div class="value">{{ rq.total_processed }}</div></div>
        <div class="card"><div class="label">Needs Review</div><div class="value" style="color:var(--yellow)">{{ rq.needs_review }}</div></div>
        <div class="card"><div class="label">Passed</div><div class="value" style="color:var(--green)">{{ rq.passed }}</div></div>
    </div>
    {% if items %}
    <table>
        <tr><th>Priority</th><th>Company</th><th>Status</th><th>Reasons</th></tr>
        {% for item in items %}
        <tr>
            <td><span class="priority {% if item.priority >= 8 %}priority-high{% elif item.priority >= 4 %}priority-med{% else %}priority-low{% endif %}">{{ item.priority }}</span></td>
            <td>{{ item.company }}</td>
            <td><span class="badge badge-{{ item.status }}">{{ item.status }}</span></td>
            <td>
                {% for r in item.reasons %}
                <div style="font-size:0.85rem;margin-bottom:0.25rem">{{ r }}</div>
                {% endfor %}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div class="empty">No items in the review queue.</div>
    {% endif %}
    """, rq=rq, items=items)
    return render("Review Queue", content, "review")


@app.route("/costs")
def costs_page():
    extractions = load_all_extractions()
    totals = aggregate_costs(extractions)
    max_cost = max((e.get("cost", {}).get("total_cost_usd", 0) for e in extractions), default=0.01) or 0.01
    content = render_template_string("""
    <h1>Cost History</h1>
    <div class="cards">
        <div class="card"><div class="label">Total Spend</div><div class="value">{{ fmt_money(totals.total_cost) }}</div></div>
        <div class="card"><div class="label">Runs</div><div class="value">{{ totals.run_count }}</div></div>
        <div class="card"><div class="label">Total Tokens</div><div class="value" style="font-size:1.2rem">{{ fmt_tokens(totals.total_input + totals.total_output) }}</div></div>
        <div class="card"><div class="label">Cache Savings</div><div class="value">{{ fmt_money(totals.total_cache_savings) }}</div></div>
    </div>

    <h2>Cost per Extraction</h2>
    <div class="section">
    {% for e in extractions %}
        {% set cost = e.cost.total_cost_usd if e.cost else 0 %}
        <div class="cost-bar">
            <div class="name">{{ e.data.company_name|default('?') }}</div>
            <div class="bar"><div class="fill" style="width:{{ (cost/max_cost*100)|int }}%"></div></div>
            <div class="amount">{{ fmt_money(cost) }}</div>
        </div>
    {% endfor %}
    </div>

    <h2>Detail</h2>
    <table>
        <tr><th>Company</th><th>Cost</th><th>Input</th><th>Output</th><th>Cache Read</th><th>Cache Savings</th><th>Calls</th></tr>
        {% for e in extractions %}
        {% set c = e.cost if e.cost else {} %}
        <tr>
            <td>{{ e.data.company_name|default('?') }}</td>
            <td style="font-family:var(--mono)">{{ fmt_money(c.total_cost_usd|default(0)) }}</td>
            <td style="font-family:var(--mono)">{{ fmt_tokens(c.input_tokens|default(0)) }}</td>
            <td style="font-family:var(--mono)">{{ fmt_tokens(c.output_tokens|default(0)) }}</td>
            <td style="font-family:var(--mono)">{{ fmt_tokens(c.cache_read_tokens|default(0)) }}</td>
            <td style="font-family:var(--mono)">{{ fmt_money(c.cache_savings_usd|default(0)) }}</td>
            <td>{{ c.calls|default(0) }}</td>
        </tr>
        {% endfor %}
    </table>
    """, extractions=extractions, totals=totals, max_cost=max_cost)
    return render("Costs", content, "costs")


@app.route("/audit")
def audit_log():
    event_filter = request.args.get("filter")
    page = int(request.args.get("page", 0))
    limit = 50
    entries, total = load_audit_log(limit=limit, offset=page * limit, event_filter=event_filter)
    pages = (total + limit - 1) // limit
    content = render_template_string("""
    <h1>Audit Log <span style="color:var(--text2);font-size:1rem">({{ total }} entries)</span></h1>
    <div class="filter-bar">
        <a href="/audit" class="filter-btn {{ 'active' if not filter }}">All</a>
        <a href="/audit?filter=tool_call" class="filter-btn {{ 'active' if filter=='tool_call' }}">Tool Calls</a>
        <a href="/audit?filter=tool_result" class="filter-btn {{ 'active' if filter=='tool_result' }}">Results</a>
        <a href="/audit?filter=pii_blocked" class="filter-btn {{ 'active' if filter=='pii_blocked' }}">PII Blocked</a>
    </div>
    {% if entries %}
    {% for e in entries %}
    <div class="log-entry">
        <span class="event ev-{{ e.event }}">{{ e.event }}</span>
        <span class="tool">{{ e.tool|default('') }}</span>
        <span class="ts">{{ e.timestamp|default('') }}</span>
        {% if e.url %}<div style="color:var(--text2);font-size:0.8rem;margin-top:0.25rem">{{ e.url }}</div>{% endif %}
        {% if e.company %}<div style="color:var(--text2);font-size:0.8rem">Company: {{ e.company }}</div>{% endif %}
        {% if e.extraction_status %}<div style="font-size:0.8rem;color:{{ conf_color(1.0) if e.extraction_status=='success' else conf_color(0.3) }}">{{ e.extraction_status }}</div>{% endif %}
        {% if e.errors %}<div style="color:var(--red);font-size:0.8rem">{{ e.errors|join(', ') }}</div>{% endif %}
        {% if e.findings %}<div style="color:var(--red);font-size:0.8rem">{{ e.findings|join(', ') }}</div>{% endif %}
    </div>
    {% endfor %}
    {% if pages > 1 %}
    <div style="margin-top:1rem;display:flex;gap:0.5rem;justify-content:center">
        {% for p in range(pages) %}
        <a href="/audit?page={{ p }}{% if filter %}&filter={{ filter }}{% endif %}" class="filter-btn {{ 'active' if p==page }}">{{ p+1 }}</a>
        {% endfor %}
    </div>
    {% endif %}
    {% else %}
    <div class="empty">No audit entries{% if filter %} for "{{ filter }}"{% endif %}.</div>
    {% endif %}
    """, entries=entries, total=total, filter=event_filter, page=page, pages=pages)
    return render("Audit Log", content, "audit")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"  EDGAR Pipeline Dashboard")
    print(f"  Reading from: {OUTPUT_DIR}")
    print(f"  Open: http://localhost:5000")
    app.run(debug=True, port=5000)
