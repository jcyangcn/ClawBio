"""
13-phylogeny.py -- Interactive Phylogenetic Tree for Genomebook

Reads all genomes, builds parent-offspring relationships, and exports
a self-contained HTML page with an interactive tree visualization.
Each node shows the agent name, generation, sex, health, and top traits.

Usage:
    python 13-phylogeny.py                              # Export to slides/genomebook/phylogeny.html
    python 13-phylogeny.py --max-gen 5                  # Limit depth
    python 13-phylogeny.py --output /tmp/tree.html
"""

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "DATA"
GENOMES_DIR = DATA / "GENOMES"
DEFAULT_OUTPUT = BASE.parent / "slides" / "genomebook" / "phylogeny.html"


def load_all_genomes(max_gen=None):
    genomes = {}
    for gf in sorted(GENOMES_DIR.glob("*.genome.json")):
        g = json.load(open(gf))
        if max_gen is not None and g.get("generation", 0) > max_gen:
            continue
        genomes[g["id"]] = g
    return genomes


def build_tree_data(genomes):
    """Build tree nodes and edges for visualization."""
    nodes = []
    edges = []

    for gid, g in genomes.items():
        # Top 3 traits
        traits = g.get("trait_scores", {})
        top3 = sorted(traits.items(), key=lambda x: x[1], reverse=True)[:3]
        top3_str = ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in top3)

        # Bottom 2 traits (vulnerabilities)
        bot2 = sorted(traits.items(), key=lambda x: x[1])[:2]
        bot2_str = ", ".join(f"{t.replace('_',' ')}: {s:.2f}" for t, s in bot2)

        # Health + conditions
        health = g.get("health_score", 1.0)
        conditions = len(g.get("clinical_history", []))
        mutations = len(g.get("mutations", []))

        # Short name
        name = g.get("name", gid)
        if len(name) > 40:
            name = name[:37] + "..."

        nodes.append({
            "id": gid,
            "name": name,
            "gen": g.get("generation", 0),
            "sex": g.get("sex", "Unknown"),
            "health": round(health, 2),
            "conditions": conditions,
            "mutations": mutations,
            "top_traits": top3_str,
            "weak_traits": bot2_str,
            "ancestry": g.get("ancestry", ""),
            "domain": g.get("domain", ""),
            "parents": g.get("parents", [None, None]),
        })

        # Edges from parents
        parents = g.get("parents", [None, None])
        if parents[0] and parents[0] in genomes:
            edges.append({"from": parents[0], "to": gid, "type": "father"})
        if parents[1] and parents[1] in genomes:
            edges.append({"from": parents[1], "to": gid, "type": "mother"})

    return nodes, edges


def build_html(nodes, edges):
    nodes_json = json.dumps(nodes, default=str)
    edges_json = json.dumps(edges, default=str)

    # Count stats
    gens = set(n["gen"] for n in nodes)
    max_gen = max(gens) if gens else 0
    founders = [n for n in nodes if n["gen"] == 0]

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Genomebook Phylogeny -- Evolutionary Tree</title>
<style>
:root {{
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
  --border: #30363d; --text: #e6edf3; --muted: #8b949e;
  --green: #3fb950; --blue: #58a6ff; --red: #f85149;
  --purple: #bc8cff; --orange: #e3b341; --pink: #f778ba;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; overflow: hidden; height: 100vh; }}

.header {{
  background: var(--bg2); border-bottom: 1px solid var(--border);
  padding: 0.8rem 1.5rem; display: flex; justify-content: space-between; align-items: center;
}}
.header h1 {{ font-size: 1.1rem; font-weight: 800; }}
.header h1 span {{ color: var(--green); }}
.header .meta {{ font-size: 0.75rem; color: var(--muted); }}
.header a {{ color: var(--blue); text-decoration: none; font-size: 0.75rem; }}

.controls {{
  background: var(--bg2); border-bottom: 1px solid var(--border);
  padding: 0.5rem 1.5rem; display: flex; gap: 1rem; align-items: center; font-size: 0.78rem;
}}
.controls label {{ color: var(--muted); }}
.controls select, .controls input {{
  background: var(--bg3); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; padding: 0.25rem 0.5rem; font-size: 0.75rem;
}}
.controls button {{
  background: var(--green); color: #000; border: none; border-radius: 4px;
  padding: 0.3rem 0.8rem; font-size: 0.75rem; font-weight: 700; cursor: pointer;
}}

.tree-container {{
  width: 100%; height: calc(100vh - 90px); overflow: auto;
  position: relative;
}}
canvas {{ display: block; }}

.tooltip {{
  position: fixed; background: var(--bg2); border: 1px solid var(--border);
  border-radius: 8px; padding: 0.8rem; font-size: 0.78rem; max-width: 320px;
  pointer-events: none; display: none; z-index: 100; line-height: 1.5;
  box-shadow: 0 4px 20px rgba(0,0,0,0.5);
}}
.tooltip .tt-name {{ font-weight: 700; font-size: 0.88rem; margin-bottom: 0.3rem; }}
.tooltip .tt-meta {{ color: var(--muted); font-size: 0.72rem; margin-bottom: 0.4rem; }}
.tooltip .tt-traits {{ margin-top: 0.3rem; }}
.tooltip .tt-label {{ color: var(--muted); font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; }}
.tt-health {{ font-weight: 700; }}
</style>
</head>
<body>

<div class="header">
  <h1>&#x1F9EC; <span>Genomebook</span> Phylogeny</h1>
  <div style="text-align:right">
    <div class="meta">{len(nodes)} agents &middot; {max_gen + 1} generations &middot; {len(founders)} founders</div>
    <a href="demo.html">Observatory</a> &middot;
    <a href="https://clawbio.github.io/ClawBio/slides/genomebook/">Slides</a>
  </div>
</div>

<div class="controls">
  <label>Highlight:</label>
  <select id="highlight" onchange="render()">
    <option value="health">Health Score</option>
    <option value="sex">Sex</option>
    <option value="mutations">Mutations</option>
  </select>
  <label>Max gen:</label>
  <input type="range" id="maxgen" min="0" max="{max_gen}" value="{max_gen}" oninput="render(); document.getElementById('mg-val').textContent=this.value" style="width:100px">
  <span id="mg-val" style="color:var(--green);font-weight:700">{max_gen}</span>
  <button onclick="resetView()">Reset View</button>
</div>

<div class="tree-container" id="container">
  <canvas id="tree"></canvas>
</div>
<div class="tooltip" id="tooltip"></div>

<script>
const NODES = {nodes_json};
const EDGES = {edges_json};

const canvas = document.getElementById('tree');
const ctx = canvas.getContext('2d');
const container = document.getElementById('container');
const tooltip = document.getElementById('tooltip');

// Layout constants
const NODE_W = 120;
const NODE_H = 36;
const GEN_HEIGHT = 100;
const PAD_X = 30;
const PAD_Y = 50;

let positions = {{}};
let scale = 1;
let offsetX = 0, offsetY = 0;
let dragging = false, dragX = 0, dragY = 0;

function layout(maxGen) {{
  positions = {{}};
  const byGen = {{}};
  for (const n of NODES) {{
    if (n.gen > maxGen) continue;
    if (!byGen[n.gen]) byGen[n.gen] = [];
    byGen[n.gen].push(n);
  }}

  const gens = Object.keys(byGen).map(Number).sort((a,b) => a - b);
  let maxWidth = 0;

  for (const gen of gens) {{
    const agents = byGen[gen];
    const rowWidth = agents.length * (NODE_W + 10);
    if (rowWidth > maxWidth) maxWidth = rowWidth;
  }}

  for (const gen of gens) {{
    const agents = byGen[gen];
    const rowWidth = agents.length * (NODE_W + 10);
    const startX = (maxWidth - rowWidth) / 2 + PAD_X;

    for (let i = 0; i < agents.length; i++) {{
      positions[agents[i].id] = {{
        x: startX + i * (NODE_W + 10),
        y: PAD_Y + gen * GEN_HEIGHT,
        node: agents[i],
      }};
    }}
  }}

  const totalW = maxWidth + PAD_X * 2;
  const totalH = (gens.length) * GEN_HEIGHT + PAD_Y * 2;
  canvas.width = Math.max(totalW, container.clientWidth) * 2;
  canvas.height = Math.max(totalH, container.clientHeight) * 2;
  canvas.style.width = Math.max(totalW, container.clientWidth) + 'px';
  canvas.style.height = Math.max(totalH, container.clientHeight) + 'px';
}}

function getColor(node, mode) {{
  if (mode === 'health') {{
    const h = node.health;
    if (h >= 0.9) return '#3fb950';
    if (h >= 0.7) return '#58a6ff';
    if (h >= 0.5) return '#e3b341';
    return '#f85149';
  }}
  if (mode === 'sex') {{
    return node.sex === 'Male' ? '#58a6ff' : '#f778ba';
  }}
  if (mode === 'mutations') {{
    if (node.mutations === 0) return '#3fb950';
    if (node.mutations <= 2) return '#e3b341';
    return '#f85149';
  }}
  return '#58a6ff';
}}

function render() {{
  const maxGen = parseInt(document.getElementById('maxgen').value);
  const mode = document.getElementById('highlight').value;

  layout(maxGen);

  ctx.save();
  ctx.scale(2, 2);
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Draw edges
  for (const e of EDGES) {{
    const from = positions[e.from];
    const to = positions[e.to];
    if (!from || !to) continue;

    ctx.strokeStyle = e.type === 'father' ? 'rgba(88,166,255,0.3)' : 'rgba(247,120,186,0.3)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(from.x + NODE_W / 2, from.y + NODE_H);
    // Bezier curve
    const midY = (from.y + NODE_H + to.y) / 2;
    ctx.bezierCurveTo(
      from.x + NODE_W / 2, midY,
      to.x + NODE_W / 2, midY,
      to.x + NODE_W / 2, to.y
    );
    ctx.stroke();
  }}

  // Draw nodes
  for (const id in positions) {{
    const p = positions[id];
    const n = p.node;
    const color = getColor(n, mode);

    // Node box
    ctx.fillStyle = '#161b22';
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(p.x, p.y, NODE_W, NODE_H, 6);
    ctx.fill();
    ctx.stroke();

    // Generation badge
    ctx.fillStyle = 'rgba(63,185,80,0.15)';
    ctx.beginPath();
    ctx.roundRect(p.x, p.y, 22, 14, [6, 0, 6, 0]);
    ctx.fill();
    ctx.fillStyle = '#3fb950';
    ctx.font = '8px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('G' + n.gen, p.x + 11, p.y + 10);

    // Name (truncated)
    ctx.fillStyle = '#e6edf3';
    ctx.font = '9px system-ui';
    ctx.textAlign = 'left';
    let displayName = n.name;
    if (displayName.length > 16) displayName = displayName.substring(0, 15) + '...';
    ctx.fillText(displayName, p.x + 25, p.y + 11);

    // Health bar
    const barW = NODE_W - 8;
    const barH = 4;
    const barX = p.x + 4;
    const barY = p.y + NODE_H - 10;
    ctx.fillStyle = '#21262d';
    ctx.fillRect(barX, barY, barW, barH);
    ctx.fillStyle = color;
    ctx.fillRect(barX, barY, barW * n.health, barH);

    // Sex indicator
    ctx.fillStyle = n.sex === 'Male' ? '#58a6ff' : '#f778ba';
    ctx.font = '8px system-ui';
    ctx.textAlign = 'left';
    ctx.fillText(n.sex === 'Male' ? 'M' : 'F', p.x + 25, p.y + NODE_H - 6);

    // Health text
    ctx.fillStyle = '#8b949e';
    ctx.font = '7px system-ui';
    ctx.textAlign = 'right';
    ctx.fillText(n.health.toFixed(2), p.x + NODE_W - 4, p.y + NODE_H - 6);
  }}

  // Generation labels
  const gens = [...new Set(Object.values(positions).map(p => p.node.gen))].sort((a,b) => a - b);
  for (const gen of gens) {{
    ctx.fillStyle = '#8b949e';
    ctx.font = 'bold 10px system-ui';
    ctx.textAlign = 'left';
    ctx.fillText('Generation ' + gen, 8, PAD_Y + gen * GEN_HEIGHT + NODE_H / 2 + 4);
  }}

  ctx.restore();
}}

// Tooltip on hover
canvas.addEventListener('mousemove', (e) => {{
  const rect = canvas.getBoundingClientRect();
  const mx = (e.clientX - rect.left);
  const my = (e.clientY - rect.top);

  let found = null;
  for (const id in positions) {{
    const p = positions[id];
    if (mx >= p.x && mx <= p.x + NODE_W && my >= p.y && my <= p.y + NODE_H) {{
      found = p.node;
      break;
    }}
  }}

  if (found) {{
    const healthColor = found.health >= 0.7 ? 'var(--green)' : found.health >= 0.5 ? 'var(--orange)' : 'var(--red)';
    tooltip.style.display = 'block';
    tooltip.style.left = (e.clientX + 15) + 'px';
    tooltip.style.top = (e.clientY + 15) + 'px';
    tooltip.innerHTML =
      '<div class="tt-name">' + found.name + '</div>' +
      '<div class="tt-meta">Gen ' + found.gen + ' &middot; ' + found.sex + ' &middot; ' + found.ancestry + '</div>' +
      (found.domain ? '<div class="tt-meta">Domain: ' + found.domain + '</div>' : '') +
      '<div class="tt-traits"><span class="tt-label">Health: </span><span class="tt-health" style="color:' + healthColor + '">' + found.health + '</span>' +
      (found.conditions > 0 ? ' &middot; ' + found.conditions + ' conditions' : '') +
      (found.mutations > 0 ? ' &middot; ' + found.mutations + ' mutations' : '') + '</div>' +
      '<div class="tt-traits"><span class="tt-label">Strengths: </span>' + found.top_traits + '</div>' +
      '<div class="tt-traits"><span class="tt-label">Weaknesses: </span>' + found.weak_traits + '</div>' +
      (found.parents[0] ? '<div class="tt-traits"><span class="tt-label">Parents: </span>' + found.parents[0] + ' x ' + found.parents[1] + '</div>' : '');
  }} else {{
    tooltip.style.display = 'none';
  }}
}});

canvas.addEventListener('mouseleave', () => {{ tooltip.style.display = 'none'; }});

function resetView() {{
  document.getElementById('maxgen').value = {max_gen};
  document.getElementById('mg-val').textContent = '{max_gen}';
  render();
  container.scrollTo(0, 0);
}}

render();
</script>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description="Genomebook Phylogenetic Tree")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-gen", type=int, default=None, help="Limit to N generations")
    args = parser.parse_args()

    genomes = load_all_genomes(max_gen=args.max_gen)
    print(f"Loaded {len(genomes)} genomes")

    nodes, edges = build_tree_data(genomes)
    print(f"Nodes: {len(nodes)}, Edges: {len(edges)}")

    html = build_html(nodes, edges)
    out = Path(args.output)
    out.write_text(html)
    print(f"Written: {out}")
    print(f"\nhttps://clawbio.github.io/ClawBio/slides/genomebook/phylogeny.html")


if __name__ == "__main__":
    main()
