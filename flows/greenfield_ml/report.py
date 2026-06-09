#!/usr/bin/env python3
"""Generate an HTML research report for a greenfield ML hill-climb run (no LLM).

Reads `experiments.jsonl` (one record per experiment, written by keep_or_revert.py)
and the final `model.py` (the best architecture), and writes `report.html` with:
  - summary stats (baseline, best, target, met?),
  - a hill-climb progress plot (best-so-far line + per-experiment candidate points,
    green=kept / red=reverted),
  - a table of every experiment (step, proposal, candidate, best, kept/reverted),
  - the best architecture (auto-summary: class, parameter count, layers + source).

Runs in the workspace (the command's cwd). Defensive: any missing piece degrades
gracefully rather than failing the flow.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import re
import subprocess
from pathlib import Path


def load_experiments() -> list[dict]:
    p = Path("experiments.jsonl")
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _inline_md(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md_to_html(text: str) -> str:
    """Minimal markdown -> HTML (headings, paragraphs, bullet lists, bold, code)."""
    out, in_list = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<h3>{_inline_md(line.lstrip('#').strip())}</h3>")
        elif line.lstrip().startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline_md(line.lstrip()[2:])}</li>")
        elif not line.strip():
            if in_list:
                out.append("</ul>"); in_list = False
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p>{_inline_md(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def load_narrative() -> str:
    p = Path("report_narrative.md")
    return md_to_html(p.read_text()) if p.exists() and p.read_text().strip() else ""


def baseline_from_git() -> float | None:
    try:
        log = subprocess.run(["git", "log", "--oneline"], capture_output=True,
                             text=True).stdout
    except Exception:
        return None
    m = re.search(r"baseline score ([0-9]*\.?[0-9]+)", log)
    return float(m.group(1)) if m else None


def make_plot(baseline: float | None, experiments: list[dict]) -> str | None:
    """Return a base64 PNG of the hill-climb, or None if plotting isn't available."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    xs_best = ([0] if baseline is not None else []) + [e["step"] for e in experiments]
    ys_best = ([baseline] if baseline is not None else []) + [e["best"] for e in experiments]

    fig, ax = plt.subplots(figsize=(7.5, 4))
    if xs_best:
        ax.plot(xs_best, ys_best, "-o", color="#2563eb", label="best so far", zorder=2)
    for e in experiments:
        ax.scatter(e["step"], e["candidate"], s=46, zorder=3,
                   color="#16a34a" if e.get("kept") else "#dc2626")
    ax.scatter([], [], color="#16a34a", label="kept candidate")
    ax.scatter([], [], color="#dc2626", label="reverted candidate")
    ax.set_xlabel("experiment #")
    ax.set_ylabel("test accuracy")
    ax.set_title("Hill-climb progress")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=9)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    Path("report_assets").mkdir(exist_ok=True)
    Path("report_assets/hillclimb.png").write_bytes(buf.getvalue())
    return base64.b64encode(buf.getvalue()).decode()


def architecture() -> tuple[str, str]:
    """(summary, source) for the best model.py.

    Picks the TOP-LEVEL model, not a building block: instantiate every nn.Module
    subclass defined in model.py with no args and choose the one with the most
    parameters (helper blocks like BasicBlock need constructor args and are
    skipped). Prefers a class that train.py actually instantiates as a tiebreak.
    """
    src = Path("model.py").read_text() if Path("model.py").exists() else "(model.py not found)"
    summary = "(architecture introspection unavailable)"
    try:
        import importlib.util
        import torch.nn as nn

        spec = importlib.util.spec_from_file_location("best_model", "model.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        classes = [v for v in vars(mod).values()
                   if isinstance(v, type) and issubclass(v, nn.Module)
                   and v.__module__ == "best_model"]
        train_src = Path("train.py").read_text() if Path("train.py").exists() else ""
        referenced = {c.__name__ for c in classes
                      if re.search(rf"\b{re.escape(c.__name__)}\s*\(", train_src)}

        best = None  # (params, prefers_train, cls, instance)
        for c in classes:
            try:
                m = c()                                  # no-arg constructable only
            except Exception:
                continue                                 # helper block needing args
            n = sum(p.numel() for p in m.parameters())
            key = (c.__name__ in referenced, n)          # train-referenced wins ties
            if best is None or key > best[0]:
                best = (key, c, m, n)

        if best is not None:
            _, cls, m, n = best
            layers = "\n".join(f"  ({name}): {sub}"
                               for name, sub in m.named_modules() if name)
            summary = f"Class: {cls.__name__}\nParameters: {n:,}\n\nLayers:\n{layers}"
        elif classes:
            names = ", ".join(c.__name__ for c in classes)
            summary = (f"Model classes: {names}\n"
                       "(none were no-arg constructable, so parameter count is "
                       "unavailable — see the source below).")
    except Exception as e:
        summary = f"(architecture introspection failed: {e})"
    return summary, src


def render_html(task, target, lower_is_better, baseline, experiments, plot_b64,
                arch_summary, arch_src, narrative_html="") -> str:
    best = experiments[-1]["best"] if experiments else baseline
    met = (best is not None and target is not None and
           ((best <= target) if lower_is_better else (best >= target)))
    direction = "lower is better" if lower_is_better else "higher is better"

    def cell(x):
        return html.escape(str(x))

    rows = []
    for e in experiments:
        tag = ('<span style="color:#16a34a;font-weight:600">kept</span>' if e.get("kept")
               else '<span style="color:#dc2626">reverted</span>')
        prop = html.escape((e.get("proposal") or "").strip())[:600] or "—"
        rows.append(
            f"<tr><td>{e['step']}</td><td><pre class='prop'>{prop}</pre></td>"
            f"<td>{e['candidate']:.4f}</td><td>{e['best']:.4f}</td><td>{tag}</td></tr>")
    table = "\n".join(rows) or "<tr><td colspan=5>(no hill-climb experiments — baseline met the target)</td></tr>"

    plot_html = (f'<img src="data:image/png;base64,{plot_b64}" alt="hill-climb plot">'
                 if plot_b64 else "<p><em>(plot unavailable)</em></p>")

    badge = ('<span class="ok">TARGET MET</span>' if met
             else '<span class="no">target not reached</span>')

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Research Report</title>
<style>
 body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1f2937}}
 h1{{margin-bottom:.2rem}} h2{{margin-top:2rem;border-bottom:1px solid #e5e7eb;padding-bottom:.3rem}}
 .meta{{color:#6b7280}} table{{border-collapse:collapse;width:100%;font-size:14px}}
 th,td{{border:1px solid #e5e7eb;padding:.5rem;text-align:left;vertical-align:top}}
 th{{background:#f9fafb}} pre{{background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:8px;overflow:auto;font-size:13px}}
 pre.prop{{background:#f8fafc;color:#334155;padding:.5rem;margin:0;white-space:pre-wrap;font-size:12px;border-radius:4px}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}}
 .card{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:.8rem 1.2rem}}
 .card b{{display:block;font-size:1.5rem}} .ok{{color:#16a34a;font-weight:700}} .no{{color:#b45309;font-weight:700}}
 img{{max-width:100%;border:1px solid #e5e7eb;border-radius:8px}}
 .narrative{{background:#f8fafc;border:1px solid #e5e7eb;border-left:4px solid #2563eb;border-radius:8px;padding:.5rem 1.2rem}}
 .narrative h3{{margin:.8rem 0 .3rem}} .narrative code{{background:#e2e8f0;padding:.1rem .3rem;border-radius:3px;font-size:.9em}}
</style></head><body>
<h1>ML Auto-Research Report</h1>
<p class="meta">{cell(task)}</p>
<div class="cards">
  <div class="card">baseline<b>{baseline if baseline is None else f'{baseline:.4f}'}</b></div>
  <div class="card">best<b>{best if best is None else f'{best:.4f}'}</b></div>
  <div class="card">target ({direction})<b>{target:.4f}</b></div>
  <div class="card">experiments<b>{len(experiments)}</b></div>
  <div class="card">outcome<b style="font-size:1rem">{badge}</b></div>
</div>
{f'<h2>Summary</h2><div class="narrative">{narrative_html}</div>' if narrative_html else ''}
<h2>Hill-climb progress</h2>
{plot_html}
<h2>Experiments</h2>
<table><thead><tr><th>#</th><th>Proposal</th><th>candidate</th><th>best</th><th>result</th></tr></thead>
<tbody>{table}</tbody></table>
<h2>Best architecture</h2>
<pre>{cell(arch_summary)}</pre>
<details><summary>model.py source</summary><pre>{cell(arch_src)}</pre></details>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--task", default="")
    ap.add_argument("--target", type=float, default=0.0)
    ap.add_argument("--lower-is-better", default="false")
    args = ap.parse_args()
    lower = str(args.lower_is_better).lower() == "true"

    experiments = load_experiments()
    baseline = baseline_from_git()
    plot_b64 = make_plot(baseline, experiments)
    arch_summary, arch_src = architecture()
    narrative_html = load_narrative()
    html_doc = render_html(args.task, args.target, lower, baseline, experiments,
                           plot_b64, arch_summary, arch_src, narrative_html)
    Path("report.html").write_text(html_doc, encoding="utf-8")
    best = experiments[-1]["best"] if experiments else baseline
    print(f"REPORT=report.html EXPERIMENTS={len(experiments)} BEST={best}")


if __name__ == "__main__":
    main()
