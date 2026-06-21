#!/usr/bin/env python3
"""Write report.html for the le-wm hill-climb (deterministic — no LLM).

Reads `experiments.jsonl` (one record per experiment, written by keep_or_revert.py),
`report_narrative.md` (the LLM summary), and the winning training config, and emits
`report.html` with:

  - a hill-climb progress plot (best-so-far line + per-experiment candidate points),
  - a per-experiment table: proposal, the files actually changed, candidate vs best
    success_rate, kept/reverted, and the commit sha (so the record reflects what was
    really done, not just the proposal),
  - the winning training config.

Runs with cwd = the workspace (the le-wm repo). higher success_rate is better.

    python3 "{flow_dir}/report.py" --task "{task}" --target {target_success}
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import subprocess
from pathlib import Path


def load_experiments() -> list[dict]:
    p = Path("experiments.jsonl")
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _inline_md(s: str) -> str:
    import re
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md_to_html(text: str) -> str:
    """Minimal markdown -> HTML (headings, paragraphs, bullets, bold, code)."""
    lines, out, in_list = text.splitlines(), [], False
    for ln in lines:
        s = ln.rstrip()
        if s.startswith("- ") or s.startswith("* "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline_md(s[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if s.startswith("### "):
            out.append(f"<h3>{_inline_md(s[4:])}</h3>")
        elif s.startswith("## "):
            out.append(f"<h3>{_inline_md(s[3:])}</h3>")
        elif s.startswith("# "):
            out.append(f"<h3>{_inline_md(s[2:])}</h3>")
        elif s:
            out.append(f"<p>{_inline_md(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def load_narrative() -> str:
    p = Path("report_narrative.md")
    return md_to_html(p.read_text()) if p.exists() and p.read_text().strip() else ""


def _kept(e: dict) -> bool:
    return e.get("kept") if "kept" in e else e.get("status") == "keep"


def make_plot(experiments: list[dict], target: float | None) -> str | None:
    """base64 PNG of the hill-climb (best-so-far line + candidate points), or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    if not experiments:
        return None
    steps = [e["step"] for e in experiments]
    cand = [e.get("candidate") for e in experiments]
    best = [e.get("best") for e in experiments]
    kept = [_kept(e) for e in experiments]
    fig, ax = plt.subplots(figsize=(7.5, 4))
    ax.plot(steps, best, "-o", color="#2563eb", label="best so far", zorder=2)
    kx = [s for s, k in zip(steps, kept) if k]
    ky = [c for c, k in zip(cand, kept) if k]
    rx = [s for s, k in zip(steps, kept) if not k]
    ry = [c for c, k in zip(cand, kept) if not k]
    if kx:
        ax.scatter(kx, ky, color="#16a34a", label="kept", zorder=3)
    if rx:
        ax.scatter(rx, ry, color="#dc2626", marker="x", label="reverted", zorder=3)
    if target is not None:
        ax.axhline(target, ls="--", color="#9ca3af", label=f"target {target:g}")
    ax.set_xlabel("experiment step"); ax.set_ylabel("success_rate")
    ax.set_title("Hill-climb progress"); ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def winning_config() -> str:
    """The kept training config (the winner). Shows the two config files that the
    proposer is allowed to tune."""
    out = []
    for rel in ("config/train/lewm.yaml", "config/train/model/lewm.yaml"):
        p = Path(rel)
        if p.exists():
            out.append(f"# {rel}\n{p.read_text().strip()}")
    return "\n\n".join(out) or "(config files not found)"


def render_html(task, target, experiments, plot_b64, config_src, narrative_html) -> str:
    kept_rows = [e for e in experiments if _kept(e)]
    best = max((e.get("best") for e in experiments), default=None)
    met = best is not None and target is not None and best >= target

    rows = []
    for e in experiments:
        tag = ('<span style="color:#16a34a;font-weight:600">kept</span>' if _kept(e)
               else '<span style="color:#dc2626">reverted</span>')
        prop = html.escape((e.get("proposal") or "").strip())[:800] or "—"
        changed = ", ".join(e.get("files_changed") or [])
        changed = (f"<code>{html.escape(changed)}</code>" if changed
                   else "<span style='color:#dc2626'>none</span>")  # empty = no-op implement!
        sha = (e.get("commit_sha") or "")[:8]
        sha = f"<code>{sha}</code>" if sha else "—"
        cand = e.get("candidate"); bst = e.get("best")
        rows.append(
            f"<tr><td>{e['step']}</td><td><pre class='prop'>{prop}</pre></td>"
            f"<td>{changed}</td><td>{cand:g}</td><td>{bst:g}</td>"
            f"<td>{tag}</td><td>{sha}</td></tr>")
    table = "\n".join(rows) or "<tr><td colspan=7>(no experiments recorded)</td></tr>"
    plot_html = (f'<img src="data:image/png;base64,{plot_b64}" alt="hill-climb plot">'
                 if plot_b64 else "<p><em>(plot unavailable)</em></p>")
    badge = ('<span class="ok">TARGET MET</span>' if met
             else '<span class="no">target not reached</span>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>LeWM Hill-climb Report</title>
<style>
 body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#1f2937}}
 h1{{margin-bottom:.2rem}} h2{{margin-top:2rem;border-bottom:1px solid #e5e7eb;padding-bottom:.3rem}}
 .meta{{color:#6b7280}} table{{border-collapse:collapse;width:100%;font-size:14px}}
 th,td{{border:1px solid #e5e7eb;padding:.5rem;text-align:left;vertical-align:top}}
 th{{background:#f9fafb}} pre{{background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:8px;overflow:auto;font-size:12px}}
 pre.prop{{background:#f8fafc;color:#334155;padding:.5rem;margin:0;white-space:pre-wrap;font-size:12px;border-radius:4px;max-height:14rem;overflow:auto}}
 code{{background:#e2e8f0;padding:.1rem .3rem;border-radius:3px;font-size:.85em}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}}
 .card{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:.8rem 1.2rem}}
 .card b{{display:block;font-size:1.5rem}} .ok{{color:#16a34a;font-weight:700}} .no{{color:#b45309;font-weight:700}}
 img{{max-width:100%;border:1px solid #e5e7eb;border-radius:8px}}
 .narrative{{background:#f8fafc;border:1px solid #e5e7eb;border-left:4px solid #2563eb;border-radius:8px;padding:.5rem 1.2rem}}
</style></head><body>
<h1>LeWM OGBench-Cube Hill-climb Report</h1>
<p class="meta">{html.escape(task)}</p>
<div class="cards">
  <div class="card">best success_rate<b>{best if best is None else f'{best:g}'}</b></div>
  <div class="card">target<b>{'' if target is None else f'{target:g}'}</b></div>
  <div class="card">experiments<b>{len(experiments)}</b></div>
  <div class="card">kept<b>{len(kept_rows)}</b></div>
  <div class="card">outcome<b style="font-size:1rem">{badge}</b></div>
</div>
{f'<h2>Summary</h2><div class="narrative">{narrative_html}</div>' if narrative_html else ''}
<h2>Hill-climb progress</h2>
{plot_html}
<h2>Experiments</h2>
<table><thead><tr><th>#</th><th>Proposal</th><th>changed files</th><th>candidate</th><th>best</th><th>result</th><th>commit</th></tr></thead>
<tbody>{table}</tbody></table>
<h2>Winning config</h2>
<pre>{html.escape(config_src)}</pre>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--task", default="")
    ap.add_argument("--target", type=float, default=None)
    args = ap.parse_args()
    experiments = load_experiments()
    plot_b64 = make_plot(experiments, args.target)
    html_doc = render_html(args.task, args.target, experiments, plot_b64,
                           winning_config(), load_narrative())
    Path("report.html").write_text(html_doc, encoding="utf-8")
    best = max((e.get("best") for e in experiments), default=None)
    print(f"REPORT=report.html EXPERIMENTS={len(experiments)} BEST={best}")


if __name__ == "__main__":
    main()
