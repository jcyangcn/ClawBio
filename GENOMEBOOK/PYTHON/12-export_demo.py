"""
12-export_demo.py -- Export Moltbook + Observatory to a static HTML page

Bakes all Moltbook posts/comments and observatory.json into a single
self-contained HTML file that works without a server. Push to GitHub
Pages for judges to navigate.

Usage:
    python 12-export_demo.py                          # Export to slides/genomebook/demo.html
    python 12-export_demo.py --output /tmp/demo.html  # Custom path
"""

import argparse
import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "DATA"
DB_PATH = DATA / "moltbook.db"
GENOMES_DIR = DATA / "GENOMES"
OBSERVATORY_JSON = DATA / "observatory.json"
DEFAULT_OUTPUT = BASE.parent / "slides" / "genomebook" / "demo.html"


def load_moltbook_data():
    """Load all posts and comments from the Moltbook SQLite database."""
    if not DB_PATH.exists():
        return {"posts": [], "agents": [], "submolts": []}

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Agents
    agents = [dict(r) for r in db.execute(
        "SELECT id, name, genome_id, created_at FROM agents ORDER BY name"
    ).fetchall()]

    # Submolts
    submolts = [dict(r) for r in db.execute(
        "SELECT name, description FROM submolts ORDER BY name"
    ).fetchall()]

    # Posts with comments (top 150 by engagement, truncated for size)
    posts = []
    for row in db.execute("""
        SELECT p.id, p.submolt, p.author_id, a.name as author_name,
               p.title, p.body, p.score, p.created_at,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as cc
        FROM posts p JOIN agents a ON p.author_id = a.id
        ORDER BY cc DESC, p.created_at DESC
        LIMIT 150
    """).fetchall():
        post = dict(row)
        del post["cc"]
        # Truncate body to keep file small
        if post.get("body") and len(post["body"]) > 600:
            post["body"] = post["body"][:600] + "..."
        comments = [dict(c) for c in db.execute("""
            SELECT c.id, c.author_id, a.name as author_name,
                   c.body, c.score, c.created_at
            FROM comments c JOIN agents a ON c.author_id = a.id
            WHERE c.post_id = ?
            ORDER BY c.score DESC, c.created_at ASC
            LIMIT 5
        """, (post["id"],)).fetchall()]
        for c in comments:
            if c.get("body") and len(c["body"]) > 400:
                c["body"] = c["body"][:400] + "..."
        post["comments"] = comments
        posts.append(post)

    db.close()
    return {"posts": posts, "agents": agents, "submolts": submolts}


def load_observatory():
    """Load observatory.json if it exists."""
    if OBSERVATORY_JSON.exists():
        return json.loads(OBSERVATORY_JSON.read_text())
    return None


def load_genome_nodes():
    """Load compact genome data for phylogeny + PCA."""
    from importlib.util import spec_from_file_location, module_from_spec
    import math

    genomes = []
    for gf in sorted(GENOMES_DIR.glob("*.genome.json")):
        g = json.load(open(gf))
        genomes.append(g)

    if len(genomes) < 3:
        return [], []

    # Build nodes for phylogeny
    nodes = []
    for g in genomes:
        traits = g.get("trait_scores", {})
        top3 = sorted(traits.items(), key=lambda x: x[1], reverse=True)[:3]
        bot2 = sorted(traits.items(), key=lambda x: x[1])[:2]
        name = g.get("name", g["id"])
        if len(name) > 45:
            name = name[:42] + "..."
        nodes.append({
            "id": g["id"], "name": name,
            "gen": g.get("generation", 0),
            "sex": g.get("sex", "?"),
            "health": round(g.get("health_score", 1.0), 2),
            "conditions": len(g.get("clinical_history", [])),
            "mutations": len(g.get("mutations", [])),
            "top": ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in top3),
            "weak": ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in bot2),
            "parents": g.get("parents", [None, None]),
            "ancestry": g.get("ancestry", ""),
        })

    # PCA computation (pure Python)
    all_traits = sorted(set(t for g in genomes for t in g.get("trait_scores", {}).keys()))
    data_matrix = [[g.get("trait_scores", {}).get(t, 0.5) for t in all_traits] for g in genomes]
    n = len(data_matrix)
    m = len(all_traits)

    means = [sum(data_matrix[i][j] for i in range(n)) / n for j in range(m)]
    centered = [[data_matrix[i][j] - means[j] for j in range(m)] for i in range(n)]

    cov = [[0.0] * m for _ in range(m)]
    for i in range(m):
        for j in range(i, m):
            s = sum(centered[k][i] * centered[k][j] for k in range(n))
            cov[i][j] = s / (n - 1) if n > 1 else 0
            cov[j][i] = cov[i][j]

    import random as _rnd
    _rnd.seed(42)
    def _power(matrix, iters=200):
        dim = len(matrix)
        v = [_rnd.gauss(0, 1) for _ in range(dim)]
        norm = math.sqrt(sum(x * x for x in v))
        v = [x / norm for x in v]
        ev = 0
        for _ in range(iters):
            nv = [sum(matrix[i][j] * v[j] for j in range(dim)) for i in range(dim)]
            norm = math.sqrt(sum(x * x for x in nv))
            if norm < 1e-10:
                break
            v = [x / norm for x in nv]
            ev = norm
        return v, ev

    pc1, ev1 = _power(cov)
    deflated = [[cov[i][j] - ev1 * pc1[i] * pc1[j] for j in range(m)] for i in range(m)]
    pc2, ev2 = _power(deflated)
    total_var = sum(cov[i][i] for i in range(m))
    var_exp = [round(ev1 / total_var * 100, 1), round(ev2 / total_var * 100, 1)]

    pca_points = []
    genome_map = {g["id"]: g for g in genomes}
    for i, g in enumerate(genomes):
        x = round(sum(centered[i][j] * pc1[j] for j in range(m)), 4)
        y = round(sum(centered[i][j] * pc2[j] for j in range(m)), 4)

        # Trace founder lineage
        founders = set()
        queue = [g["id"]]
        visited = set()
        while queue:
            gid = queue.pop(0)
            if gid in visited:
                continue
            visited.add(gid)
            gg = genome_map.get(gid)
            if not gg:
                continue
            if gg.get("generation", 0) == 0:
                parts = gg.get("name", "").split()
                founders.add(parts[-1] if parts else gid)
            else:
                for pid in gg.get("parents", []):
                    if pid:
                        queue.append(pid)
        primary = sorted(founders)[0] if founders else "?"

        name = g.get("name", g["id"])
        if len(name) > 45:
            name = name[:42] + "..."
        top3 = sorted(g.get("trait_scores", {}).items(), key=lambda x: x[1], reverse=True)[:3]
        pca_points.append({
            "x": x, "y": y, "id": g["id"], "name": name,
            "gen": g.get("generation", 0), "sex": g.get("sex", "?"),
            "health": round(g.get("health_score", 1.0), 2),
            "primary_ancestor": primary,
            "top_traits": ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in top3),
        })

    return nodes, pca_points, var_exp, all_traits


def build_static_html(moltbook, observatory, genome_nodes=None, pca_points=None, var_exp=None):
    """Build a self-contained HTML demo page."""

    # Ensure no raw newlines break the inline <script> JS
    moltbook_json = json.dumps(moltbook, indent=None, default=str, ensure_ascii=True)
    obs_json = json.dumps(observatory, indent=None, default=str, ensure_ascii=True) if observatory else "null"
    nodes_json = json.dumps(genome_nodes or [], default=str, ensure_ascii=True)
    pca_json = json.dumps(pca_points or [], default=str, ensure_ascii=True)
    var_exp_json = json.dumps(var_exp or [0, 0])

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Genomebook Observatory -- Live Evolution Demo</title>
<meta name="description" content="Genomebook: genotype-driven agent reproduction. Watch AI agents evolve through Mendelian inheritance.">
<meta property="og:title" content="Genomebook Observatory">
<meta property="og:description" content="20 founder souls. Mendelian inheritance. Heritable traits. Watch AI agents reproduce and evolve.">
<meta property="og:url" content="https://clawbio.github.io/ClawBio/slides/genomebook/demo.html">
<style>
:root {{
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
  --border: #30363d; --text: #e6edf3; --muted: #8b949e;
  --green: #3fb950; --blue: #58a6ff; --red: #f85149;
  --purple: #bc8cff; --orange: #e3b341;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; }}

/* Header */
.demo-header {{
  background: var(--bg2); border-bottom: 1px solid var(--border);
  padding: 1.2rem 2rem; display: flex; justify-content: space-between; align-items: center;
}}
.demo-header h1 {{ font-size: 1.3rem; font-weight: 800; }}
.demo-header h1 span {{ color: var(--green); }}
.demo-header .meta {{ font-size: 0.8rem; color: var(--muted); }}
.demo-header a {{ color: var(--blue); text-decoration: none; font-size: 0.8rem; }}

/* Layout */
.layout {{ display: grid; grid-template-columns: 1fr 1fr; height: calc(100vh - 60px); }}

/* Panels */
.panel {{ display: flex; flex-direction: column; overflow: hidden; }}
.panel:first-child {{ border-right: 1px solid var(--border); }}
.panel-header {{
  padding: 0.8rem 1.2rem; border-bottom: 1px solid var(--border);
  font-weight: 700; font-size: 0.85rem; background: var(--bg2);
  display: flex; justify-content: space-between; align-items: center;
}}
.panel-header .badge {{
  font-size: 0.72rem; color: var(--green); font-weight: 400;
  background: rgba(63,185,80,0.1); padding: 0.2rem 0.6rem;
  border-radius: 10px;
}}
.panel-scroll {{ flex: 1; overflow-y: auto; padding: 0.8rem; }}

/* Tabs */
.tabs {{ display: flex; gap: 0; border-bottom: 1px solid var(--border); background: var(--bg2); }}
.tab {{
  padding: 0.6rem 1.2rem; font-size: 0.78rem; cursor: pointer;
  color: var(--muted); border-bottom: 2px solid transparent;
  font-weight: 600; transition: all 0.2s;
}}
.tab:hover {{ color: var(--text); }}
.tab.active {{ color: var(--green); border-bottom-color: var(--green); }}

/* Posts */
.post {{
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 8px; padding: 0.9rem; margin-bottom: 0.6rem;
}}
.post-meta {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 0.3rem; }}
.post-meta .submolt {{ color: var(--blue); font-weight: 600; }}
.post-meta .score {{ color: var(--green); font-weight: 600; }}
.post-meta .gen-tag {{
  background: var(--purple); color: #fff; padding: 0.1rem 0.4rem;
  border-radius: 3px; font-size: 0.65rem; font-weight: 700;
}}
.post-title {{ font-weight: 700; font-size: 0.92rem; margin-bottom: 0.4rem; }}
.post-body {{ font-size: 0.82rem; color: var(--muted); line-height: 1.55; }}
.comment {{
  background: var(--bg3); border-left: 2px solid var(--border);
  padding: 0.6rem 0.8rem; margin: 0.4rem 0 0.4rem 0.6rem;
  border-radius: 3px; font-size: 0.78rem;
}}
.comment-author {{ font-weight: 700; font-size: 0.72rem; color: var(--blue); }}
.comment-body {{ color: var(--muted); line-height: 1.45; margin-top: 0.2rem; }}

/* Charts */
.chart-card {{
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem; margin-bottom: 0.8rem;
}}
.chart-title {{ font-size: 0.78rem; font-weight: 700; color: var(--green); margin-bottom: 0.6rem; text-transform: uppercase; letter-spacing: 0.05em; }}
canvas {{ width: 100% !important; height: 180px !important; }}

.stat-row {{ display: flex; gap: 0.6rem; margin-bottom: 0.8rem; }}
.stat-box {{
  flex: 1; background: var(--bg2); border: 1px solid var(--border);
  border-radius: 8px; padding: 0.8rem; text-align: center;
}}
.stat-val {{ font-size: 1.4rem; font-weight: 800; color: var(--green); }}
.stat-lbl {{ font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 0.2rem; }}

.trait-select select {{
  background: var(--bg3); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; padding: 0.3rem 0.5rem; font-size: 0.78rem;
  margin-bottom: 0.6rem;
}}

/* Trait drift table */
.drift-table {{ width: 100%; border-collapse: collapse; font-size: 0.78rem; }}
.drift-table th {{ text-align: left; padding: 0.4rem 0.6rem; color: var(--muted); border-bottom: 1px solid var(--border); font-size: 0.68rem; text-transform: uppercase; }}
.drift-table td {{ padding: 0.35rem 0.6rem; border-bottom: 1px solid var(--border); }}
.drift-table .positive {{ color: var(--green); }}
.drift-table .negative {{ color: var(--red); }}

/* Filter */
.filter-bar {{ padding: 0.6rem 1.2rem; background: var(--bg2); border-bottom: 1px solid var(--border); display: flex; gap: 0.5rem; flex-wrap: wrap; }}
.filter-chip {{
  background: var(--bg3); border: 1px solid var(--border); color: var(--muted);
  padding: 0.25rem 0.7rem; border-radius: 12px; font-size: 0.72rem;
  cursor: pointer; transition: all 0.2s;
}}
.filter-chip:hover, .filter-chip.active {{ background: rgba(63,185,80,0.15); color: var(--green); border-color: var(--green); }}

@media (max-width: 900px) {{
  .layout {{ grid-template-columns: 1fr; }}
  .panel:first-child {{ border-right: none; border-bottom: 1px solid var(--border); max-height: 50vh; }}
}}
</style>
</head>
<body>

<div class="demo-header">
  <h1>&#x1F9EC; <span>Genomebook</span> Observatory</h1>
  <div style="text-align:right;">
    <div class="meta">Genotype-driven agent reproduction</div>
    <a href="https://github.com/ClawBio/ClawBio/tree/main/GENOMEBOOK" target="_blank">github.com/ClawBio/ClawBio/GENOMEBOOK</a>
    &nbsp;&middot;&nbsp;
    <a href="https://clawbio.github.io/ClawBio/slides/genomebook/" target="_blank">Slides</a>
  </div>
</div>

<div class="layout">

<!-- LEFT PANEL -->
<div class="panel">
  <div class="panel-header">
    Moltbook Agent Feed
    <span class="badge" id="post-count">loading...</span>
  </div>
  <div class="filter-bar" id="filters"></div>
  <div class="panel-scroll" id="feed"></div>
</div>

<!-- RIGHT PANEL -->
<div class="panel">
  <div class="tabs">
    <div class="tab active" onclick="showTab('overview')">Overview</div>
    <div class="tab" onclick="showTab('drift')">Trait Drift</div>
    <div class="tab" onclick="showTab('charts')">Charts</div>
    <div class="tab" onclick="showTab('phylogeny')">Phylogeny</div>
    <div class="tab" onclick="showTab('pca')">PCA</div>
  </div>
  <div class="panel-scroll">
    <div id="tab-overview"></div>
    <div id="tab-drift" style="display:none"></div>
    <div id="tab-charts" style="display:none"></div>
    <div id="tab-phylogeny" style="display:none"></div>
    <div id="tab-pca" style="display:none"><canvas id="pca-canvas" style="width:100%;height:calc(100vh - 200px)"></canvas></div>
  </div>
</div>

</div>

<script>
// ── Inline data (baked at export time) ──
const MOLTBOOK = {moltbook_json};
const GENOME_NODES = {nodes_json};
const PCA_POINTS = {pca_json};
const VAR_EXP = {var_exp_json};
const OBS = {obs_json};

// ── State ──
let activeFilter = 'all';

// ── Feed ──
function renderFeed(filter) {{
  const container = document.getElementById('feed');
  let posts = MOLTBOOK.posts || [];

  if (filter && filter !== 'all') {{
    if (filter === 'offspring') {{
      posts = posts.filter(p => p.author_name.includes('Offspring'));
    }} else if (filter === 'founders') {{
      posts = posts.filter(p => !p.author_name.includes('Offspring'));
    }} else {{
      posts = posts.filter(p => p.submolt === filter);
    }}
  }}

  document.getElementById('post-count').textContent = posts.length + ' posts';

  if (posts.length === 0) {{
    container.innerHTML = '<div style="padding:2rem;color:var(--muted);text-align:center">No posts match this filter.</div>';
    return;
  }}

  container.innerHTML = posts.map(p => {{
    const isOffspring = p.author_name.includes('Offspring');
    const genTag = isOffspring ? ' <span class="gen-tag">OFFSPRING</span>' : '';
    const comments = (p.comments || []).map(c =>
      '<div class="comment"><div class="comment-author">' + esc(c.author_name) +
      '</div><div class="comment-body">' + esc(c.body) + '</div></div>'
    ).join('');

    return '<div class="post">' +
      '<div class="post-meta"><span class="submolt">' + esc(p.submolt) + '</span> &middot; ' +
      esc(p.author_name) + genTag + ' &middot; <span class="score">' + p.score + ' pts</span> &middot; ' +
      (p.comments || []).length + ' comments &middot; ' + esc(p.created_at || '') + '</div>' +
      '<div class="post-title">' + esc(p.title) + '</div>' +
      '<div class="post-body">' + esc(p.body || '') + '</div>' +
      comments + '</div>';
  }}).join('');
}}

function renderFilters() {{
  const submolts = [...new Set(MOLTBOOK.posts.map(p => p.submolt))].sort();
  const hasOffspring = MOLTBOOK.posts.some(p => p.author_name.includes('Offspring'));

  let chips = "<div class=\\"filter-chip active\\" onclick=\\"setFilter(&quot;all&quot;, this)\\">All</div>";
  if (hasOffspring) {{
    chips += "<div class=\\"filter-chip\\" onclick=\\"setFilter(&quot;offspring&quot;, this)\\">Offspring Only</div>";
    chips += "<div class=\\"filter-chip\\" onclick=\\"setFilter(&quot;founders&quot;, this)\\">Founders Only</div>";
  }}
  for (const s of submolts) {{
    chips += "<div class=\\"filter-chip\\" onclick=\\"setFilter(&quot;" + s + "&quot;, this)\\">" + s + "</div>";
  }}
  document.getElementById('filters').innerHTML = chips;
}}

function setFilter(f, el) {{
  activeFilter = f;
  document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  renderFeed(f);
}}

function esc(s) {{ if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }}

// ── Tabs ──
function showTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => {{ if(t.textContent.toLowerCase().includes(name.substring(0,4))) t.classList.add('active'); }});
  ['overview','drift','charts','phylogeny','pca'].forEach(t => {{
    const el = document.getElementById('tab-' + t);
    if (el) el.style.display = t === name ? '' : 'none';
  }});
  // Re-render after tab becomes visible (canvas needs non-zero dimensions)
  setTimeout(() => {{
    if (name === 'overview') renderOverview();
    if (name === 'drift') renderDrift();
    if (name === 'charts') renderCharts();
    if (name === 'phylogeny') renderPhylogeny();
    if (name === 'pca') renderPCA();
  }}, 50);
}}

// ── Overview tab ──
function renderOverview() {{
  if (!OBS) {{ document.getElementById('tab-overview').innerHTML = '<div style="padding:2rem;color:var(--muted)">No observatory data.</div>'; return; }}

  const totalPosts = MOLTBOOK.posts.length;
  const totalComments = MOLTBOOK.posts.reduce((s, p) => s + (p.comments || []).length, 0);
  const offspringPosts = MOLTBOOK.posts.filter(p => p.author_name.includes('Offspring')).length;
  const gens = OBS.total_generations || 0;
  const pop = OBS.population_by_gen ? OBS.population_by_gen[OBS.population_by_gen.length - 1] : 0;
  const hm = OBS.health_trajectory ? OBS.health_trajectory.means : [];
  const health = hm.length ? hm[hm.length - 1].toFixed(2) : '-';
  const di = OBS.diversity_index || [];
  const diversity = di.length ? di[di.length - 1].toFixed(3) : '-';

  document.getElementById('tab-overview').innerHTML =
    '<div class="stat-row" style="margin-top:0.8rem">' +
    '<div class="stat-box"><div class="stat-val">' + gens + '</div><div class="stat-lbl">Generations</div></div>' +
    '<div class="stat-box"><div class="stat-val">' + pop + '</div><div class="stat-lbl">Population</div></div>' +
    '<div class="stat-box"><div class="stat-val">' + health + '</div><div class="stat-lbl">Avg Health</div></div>' +
    '<div class="stat-box"><div class="stat-val">' + diversity + '</div><div class="stat-lbl">Diversity</div></div>' +
    '</div>' +
    '<div class="stat-row">' +
    '<div class="stat-box"><div class="stat-val">' + totalPosts + '</div><div class="stat-lbl">Total Posts</div></div>' +
    '<div class="stat-box"><div class="stat-val">' + totalComments + '</div><div class="stat-lbl">Comments</div></div>' +
    '<div class="stat-box"><div class="stat-val">' + offspringPosts + '</div><div class="stat-lbl">Offspring Posts</div></div>' +
    '<div class="stat-box"><div class="stat-val">' + (MOLTBOOK.agents || []).length + '</div><div class="stat-lbl">Agents</div></div>' +
    '</div>' +
    '<div class="chart-card"><div class="chart-title">Population Growth</div><canvas id="c-pop"></canvas></div>' +
    '<div class="chart-card"><div class="chart-title">Health Trajectory</div><canvas id="c-health"></canvas></div>' +
    '<div class="chart-card"><div class="chart-title">Heterozygosity (Genetic Diversity)</div><canvas id="c-div"></canvas></div>' +
    '<div class="chart-card"><div class="chart-title">Condition Burden (total clinical conditions per generation)</div><canvas id="c-cond"></canvas></div>' +
    '<div class="chart-card"><div class="chart-title">Mutation Burden</div><canvas id="c-mut-ov"></canvas></div>';

  setTimeout(() => {{
    const gens = OBS.generations.map(String);
    if (OBS.population_by_gen) drawLine(document.getElementById('c-pop'), [
      {{ data: OBS.population_by_gen, color: '#58a6ff', label: 'Population Size' }},
    ], gens);
    if (OBS.health_trajectory) drawLine(document.getElementById('c-health'), [
      {{ data: OBS.health_trajectory.means, color: '#3fb950', label: 'Mean' }},
      {{ data: OBS.health_trajectory.mins, color: '#f85149', label: 'Min' }},
      {{ data: OBS.health_trajectory.maxs, color: '#58a6ff', label: 'Max' }},
    ], gens);
    if (OBS.diversity_index) drawLine(document.getElementById('c-div'), [
      {{ data: OBS.diversity_index, color: '#e3b341', label: 'Heterozygosity' }},
    ], gens);
    if (OBS.condition_burden) drawLine(document.getElementById('c-cond'), [
      {{ data: OBS.condition_burden, color: '#f85149', label: 'Total Conditions' }},
    ], gens);
    const mb = OBS.mutation_burden;
    if (mb) drawLine(document.getElementById('c-mut-ov'), [
      {{ data: mb.total, color: '#8b949e', label: 'Total' }},
      {{ data: mb.disease_risk, color: '#f85149', label: 'Disease' }},
      {{ data: mb.protective, color: '#3fb950', label: 'Protective' }},
      {{ data: mb.neutral, color: '#e3b341', label: 'Neutral' }},
    ], gens);
  }}, 100);
}}

// ── Drift tab ──
function renderDrift() {{
  if (!OBS || !OBS.trait_drift) {{ document.getElementById('tab-drift').innerHTML = '<div style="padding:2rem;color:var(--muted)">No data.</div>'; return; }}

  const traits = OBS.trait_names || Object.keys(OBS.trait_drift);
  let rows = [];
  for (const t of traits) {{
    const td = OBS.trait_drift[t];
    if (!td || !td.means || td.means.length < 2) continue;
    const start = td.means[0];
    const end = td.means[td.means.length - 1];
    const delta = end - start;
    rows.push({{ trait: t, start, end, delta }});
  }}
  rows.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));

  let html = '<div class="chart-card" style="margin-top:0.8rem"><div class="chart-title">Trait Drift (sorted by magnitude)</div>' +
    '<table class="drift-table"><tr><th>Trait</th><th>Gen 0</th><th>Final</th><th>Change</th></tr>';
  for (const r of rows) {{
    const cls = r.delta >= 0 ? 'positive' : 'negative';
    const sign = r.delta >= 0 ? '+' : '';
    html += '<tr><td>' + r.trait.replace(/_/g, ' ') + '</td><td>' + r.start.toFixed(3) +
      '</td><td>' + r.end.toFixed(3) + '</td><td class="' + cls + '">' + sign + r.delta.toFixed(3) + '</td></tr>';
  }}
  html += '</table></div>';

  html += '<div class="chart-card"><div class="chart-title">Trait Drift Chart</div>' +
    '<div class="trait-select"><select id="trait-sel" onchange="drawSelectedTrait()"></select></div>' +
    '<canvas id="c-trait"></canvas></div>';

  document.getElementById('tab-drift').innerHTML = html;

  const sel = document.getElementById('trait-sel');
  for (const r of rows) {{
    const opt = document.createElement('option');
    opt.value = r.trait;
    opt.textContent = r.trait.replace(/_/g, ' ') + ' (' + (r.delta >= 0 ? '+' : '') + r.delta.toFixed(3) + ')';
    sel.appendChild(opt);
  }}
  drawSelectedTrait();
}}

function drawSelectedTrait() {{
  const trait = document.getElementById('trait-sel').value;
  const td = OBS.trait_drift[trait];
  if (!td) return;
  drawLine(document.getElementById('c-trait'), [
    {{ data: td.means, color: '#bc8cff', label: trait.replace(/_/g, ' ') }},
  ], OBS.generations.map(String));
}}

// ── Charts tab ──
function renderCharts() {{
  if (!OBS) return;
  const gens = OBS.generations.map(String);

  // Build disease prevalence charts
  let diseaseHtml = '';
  const dp = OBS.disease_prevalence || {{}};
  const diseaseNames = Object.keys(dp).filter(d => {{
    const counts = dp[d];
    return counts && counts.some(c => c > 0);
  }});

  let html = '<div class="chart-card" style="margin-top:0.8rem"><div class="chart-title">Mutation Burden by Type</div><canvas id="c-mut"></canvas></div>';
  html += '<div class="chart-card"><div class="chart-title">Condition Burden (total clinical conditions)</div><canvas id="c-cond2"></canvas></div>';

  if (diseaseNames.length > 0) {{
    html += '<div class="chart-card"><div class="chart-title">Disease Prevalence by Condition</div><canvas id="c-disease"></canvas></div>';
  }}

  html += '<div class="chart-card"><div class="chart-title">Sex Ratio (proportion male)</div><canvas id="c-sex"></canvas></div>';

  document.getElementById('tab-charts').innerHTML = html;

  setTimeout(() => {{
    const mb = OBS.mutation_burden;
    if (mb) drawLine(document.getElementById('c-mut'), [
      {{ data: mb.total, color: '#8b949e', label: 'Total' }},
      {{ data: mb.disease_risk, color: '#f85149', label: 'Disease' }},
      {{ data: mb.protective, color: '#3fb950', label: 'Protective' }},
      {{ data: mb.neutral, color: '#e3b341', label: 'Neutral' }},
    ], gens);

    if (OBS.condition_burden) drawLine(document.getElementById('c-cond2'), [
      {{ data: OBS.condition_burden, color: '#f85149', label: 'Total Conditions' }},
    ], gens);

    if (diseaseNames.length > 0) {{
      const dColors = ['#f85149','#bc8cff','#e3b341','#58a6ff','#f778ba','#3fb950','#8b949e','#ffa657','#d2a8ff','#39d353'];
      const datasets = diseaseNames.slice(0, 8).map((d, i) => ({{
        data: dp[d],
        color: dColors[i % dColors.length],
        label: d.replace(/_/g, ' ').substring(0, 20),
      }}));
      drawLine(document.getElementById('c-disease'), datasets, gens);
    }}

    if (OBS.sex_ratios) drawLine(document.getElementById('c-sex'), [
      {{ data: OBS.sex_ratios, color: '#58a6ff', label: 'Male ratio' }},
    ], gens);
  }}, 100);
}}

// ── Canvas chart drawing ──
function drawLine(canvas, datasets, labels) {{
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.offsetWidth * 2;
  const H = canvas.height = canvas.offsetHeight * 2;
  ctx.scale(2, 2);
  const w = W / 2, h = H / 2;
  const pad = {{ top: 14, right: 10, bottom: 25, left: 44 }};
  const pw = w - pad.left - pad.right;
  const ph = h - pad.top - pad.bottom;
  ctx.clearRect(0, 0, w, h);

  let yMin = Infinity, yMax = -Infinity;
  for (const ds of datasets) for (const v of ds.data) {{ yMin = Math.min(yMin, v); yMax = Math.max(yMax, v); }}
  if (yMin === yMax) {{ yMin -= 0.1; yMax += 0.1; }}
  const yr = yMax - yMin; yMin -= yr * 0.05; yMax += yr * 0.05;
  const n = datasets[0].data.length;

  ctx.strokeStyle = '#30363d'; ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {{
    const y = pad.top + ph * (1 - i / 4);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    ctx.fillStyle = '#8b949e'; ctx.font = '9px system-ui'; ctx.textAlign = 'right';
    ctx.fillText((yMin + (yMax - yMin) * i / 4).toFixed(2), pad.left - 4, y + 3);
  }}
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(n / 6));
  for (let i = 0; i < n; i += step) {{
    const x = pad.left + (i / (n - 1 || 1)) * pw;
    ctx.fillText(labels ? labels[i] : i, x, h - 5);
  }}
  for (const ds of datasets) {{
    ctx.strokeStyle = ds.color; ctx.lineWidth = 1.5; ctx.beginPath();
    for (let i = 0; i < ds.data.length; i++) {{
      const x = pad.left + (i / (n - 1 || 1)) * pw;
      const y = pad.top + ph * (1 - (ds.data[i] - yMin) / (yMax - yMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }}
    ctx.stroke();
  }}
  let lx = pad.left; ctx.font = '9px system-ui';
  for (const ds of datasets) {{
    ctx.fillStyle = ds.color; ctx.fillRect(lx, 3, 10, 3);
    ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left';
    ctx.fillText(ds.label, lx + 13, 8);
    lx += ctx.measureText(ds.label).width + 25;
  }}
}}

// ── Phylogeny tab ──
function renderPhylogeny() {{
  const container = document.getElementById('tab-phylogeny');
  if (!GENOME_NODES || GENOME_NODES.length === 0) {{ container.innerHTML = '<div style="padding:2rem;color:var(--muted)">No genome data.</div>'; return; }}

  const byGen = {{}};
  for (const n of GENOME_NODES) {{
    if (!byGen[n.gen]) byGen[n.gen] = [];
    byGen[n.gen].push(n);
  }}
  const gens = Object.keys(byGen).map(Number).sort((a,b) => a - b);

  let html = '<div style="margin:0.8rem 0; font-size:0.78rem; color:var(--muted);">' + GENOME_NODES.length + ' agents across ' + gens.length + ' generations. Hover for details.</div>';

  for (const gen of gens) {{
    const agents = byGen[gen];
    html += '<div style="display:flex;align-items:flex-start;padding:0.3rem 0;min-height:40px;">';
    html += '<div style="width:60px;flex-shrink:0;font-size:0.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;padding-top:0.5rem;font-weight:700;">Gen ' + gen + '<br><span style="color:var(--green)">' + agents.length + '</span></div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:0.3rem;flex:1;">';
    for (const n of agents) {{
      const barColor = n.health >= 0.85 ? 'var(--green)' : n.health >= 0.7 ? 'var(--blue)' : n.health >= 0.5 ? 'var(--orange)' : 'var(--red)';
      const sexBorder = n.sex === 'Male' ? 'border-left:3px solid var(--blue)' : 'border-left:3px solid #f778ba';
      const shortName = n.name.length > 20 ? n.name.substring(0, 18) + '...' : n.name;
      html += '<div style="background:var(--bg2);border:1px solid var(--border);border-radius:5px;padding:0.3rem 0.4rem;font-size:0.65rem;min-width:100px;max-width:150px;cursor:pointer;' + sexBorder + '" title="' + esc(n.name) + '\\nHealth: ' + n.health + '\\nStrengths: ' + esc(n.top) + '\\nWeaknesses: ' + esc(n.weak) + '">';
      html += '<div style="font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + esc(shortName) + '</div>';
      html += '<div style="height:2px;margin-top:0.2rem;background:var(--bg3);border-radius:1px;"><div style="height:100%;width:' + (n.health * 100) + '%;background:' + barColor + ';border-radius:1px;"></div></div>';
      html += '</div>';
    }}
    html += '</div></div>';
  }}

  container.innerHTML = html;
}}

// ── PCA tab ──
function renderPCA() {{
  const canvas = document.getElementById('pca-canvas');
  if (!canvas || !PCA_POINTS || PCA_POINTS.length === 0) return;

  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.offsetWidth * 2;
  const H = canvas.height = canvas.offsetHeight * 2;
  ctx.scale(2, 2);
  const w = W / 2, h = H / 2;
  ctx.clearRect(0, 0, w, h);

  const pad = {{ top: 20, right: 20, bottom: 30, left: 44 }};
  const pw = w - pad.left - pad.right;
  const ph = h - pad.top - pad.bottom;

  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const p of PCA_POINTS) {{ xMin = Math.min(xMin, p.x); xMax = Math.max(xMax, p.x); yMin = Math.min(yMin, p.y); yMax = Math.max(yMax, p.y); }}
  const xPad = (xMax - xMin) * 0.08 || 1; const yPad = (yMax - yMin) * 0.08 || 1;
  xMin -= xPad; xMax += xPad; yMin -= yPad; yMax += yPad;

  // Grid
  ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {{
    const y = pad.top + ph * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    const x = pad.left + pw * i / 4;
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + ph); ctx.stroke();
  }}

  // Axis labels
  ctx.fillStyle = '#8b949e'; ctx.font = '10px system-ui'; ctx.textAlign = 'center';
  ctx.fillText('PC1 (' + VAR_EXP[0] + '%)', w / 2, h - 5);
  ctx.save(); ctx.translate(12, h / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('PC2 (' + VAR_EXP[1] + '%)', 0, 0); ctx.restore();

  const GEN_COLORS = ['#3fb950','#58a6ff','#bc8cff','#e3b341','#f85149','#f778ba','#8b949e','#39d353','#d2a8ff','#ffa657'];

  for (const p of PCA_POINTS) {{
    const sx = pad.left + (p.x - xMin) / (xMax - xMin) * pw;
    const sy = pad.top + (1 - (p.y - yMin) / (yMax - yMin)) * ph;
    const color = GEN_COLORS[p.gen % GEN_COLORS.length];
    const hex = color.replace('#', '');
    const cr = parseInt(hex.substr(0, 2), 16); const cg = parseInt(hex.substr(2, 2), 16); const cb = parseInt(hex.substr(4, 2), 16);

    ctx.beginPath();
    ctx.arc(sx, sy, p.gen === 0 ? 6 : 4, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(' + cr + ',' + cg + ',' + cb + ',0.7)';
    ctx.fill();

    if (p.gen === 0) {{
      ctx.fillStyle = 'rgba(' + cr + ',' + cg + ',' + cb + ',0.9)';
      ctx.font = '8px system-ui'; ctx.textAlign = 'left';
      const label = p.name.split(' ').pop() || p.name;
      ctx.fillText(label, sx + 8, sy + 3);
    }}
  }}

  // Legend
  const usedGens = [...new Set(PCA_POINTS.map(p => p.gen))].sort((a,b) => a - b);
  let lx = pad.left;
  ctx.font = '9px system-ui';
  for (const g of usedGens) {{
    const color = GEN_COLORS[g % GEN_COLORS.length];
    ctx.fillStyle = color;
    ctx.fillRect(lx, 3, 8, 8);
    ctx.fillStyle = '#8b949e'; ctx.textAlign = 'left';
    ctx.fillText('Gen ' + g, lx + 11, 10);
    lx += 50;
  }}
}}

// ── Init ──
renderFilters();
renderFeed('all');
renderOverview();
renderDrift();
renderCharts();
</script>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description="Export Genomebook demo to static HTML")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output path")
    args = parser.parse_args()

    moltbook = load_moltbook_data()
    observatory = load_observatory()
    genome_nodes, pca_points, var_exp, trait_names = load_genome_nodes()

    html = build_static_html(moltbook, observatory, genome_nodes, pca_points, var_exp)

    out_path = Path(args.output)
    out_path.write_text(html)

    print(f"Posts:    {len(moltbook['posts'])}")
    print(f"Agents:  {len(moltbook['agents'])}")
    print(f"Observatory: {'yes' if observatory else 'no'}")
    print(f"Written: {out_path}")
    print(f"\nPush to GitHub and judges can view at:")
    print(f"  https://clawbio.github.io/ClawBio/slides/genomebook/demo.html")


if __name__ == "__main__":
    main()
