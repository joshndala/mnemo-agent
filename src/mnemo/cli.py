"""mnemo CLI — local-first agent memory management."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from mnemo import __version__
from mnemo.models import AgentDump, Fact
from mnemo.search import diff_dumps, search_dumps
from mnemo.storage import (
    DEFAULT_MNEMO_DIR,
    agent_exists,
    dumps_dir,
    init_agent,
    latest_dump_path,
    list_agents,
    list_dump_files,
    load_dump,
    load_config,
    require_agent,
    save_dump,
)

console = Console()
err_console = Console(stderr=True)


# ─── Shared option helpers ────────────────────────────────────────────────────

AGENT_OPTION = click.option(
    "--agent", "-a", default=None, help="Agent name (uses MNEMO_AGENT env var if set)"
)
DIR_OPTION = click.option(
    "--dir",
    "-d",
    "mnemo_dir",
    default=None,
    envvar="MNEMO_DIR",
    help="mnemo base directory (default: ~/.mnemo)",
    type=click.Path(path_type=Path),
)


def _resolve_base(mnemo_dir: Path | None) -> Path:
    return mnemo_dir or DEFAULT_MNEMO_DIR


def _resolve_agent(agent: str | None) -> str:
    import os
    resolved = agent or os.environ.get("MNEMO_AGENT")
    if not resolved:
        raise click.UsageError(
            "Agent name required. Pass --agent <name> or set MNEMO_AGENT env var."
        )
    return resolved


def _conf_color(confidence: float) -> str:
    if confidence >= 0.8:
        return "green"
    if confidence >= 0.5:
        return "yellow"
    return "red"


def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if ts else "—"


# ─── CLI root group ───────────────────────────────────────────────────────────


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="mnemo")
def cli() -> None:
    """🧠  mnemo — local-first agent memory CLI.

    Dump, diff, migrate, and query memories across Mem0, Letta, and more.

    \b
    Quick start:
      mnemo init --agent job-prep
      mnemo add --fact "Joshua uses React, Node, Supabase" --agent job-prep
      mnemo ls
      mnemo recall "tech stack" --agent job-prep
    """


# ─── mnemo init ───────────────────────────────────────────────────────────────


@cli.command("init")
@click.option("--agent", "-a", required=True, help="Agent name to initialize")
@DIR_OPTION
@click.option("--overwrite", is_flag=True, help="Overwrite existing config/dump files")
def cmd_init(agent: str, mnemo_dir: Path | None, overwrite: bool) -> None:
    """Initialize a new agent memory store."""
    base = _resolve_base(mnemo_dir)
    adir, is_new = init_agent(agent, base=base, overwrite=overwrite)

    status = "[green]Created[/]" if is_new else "[yellow]Already exists (refreshed)[/]"
    console.print(
        Panel.fit(
            f"[bold cyan]Agent:[/] {agent}\n"
            f"[bold cyan]Dir:[/]   {adir}\n"
            f"[bold cyan]Status:[/] {status}",
            title="🧠 mnemo init",
            border_style="cyan",
        )
    )
    console.print(f"\n[dim]Next:[/] mnemo add --fact \"Your first memory\" --agent {agent}")


# ─── mnemo dump ───────────────────────────────────────────────────────────────


@cli.command("dump")
@click.option("--agent", "-a", required=True, help="Agent name")
@click.option(
    "--source",
    type=click.Choice(["mem0", "letta", "local"]),
    default="local",
    show_default=True,
    help="Source to dump from",
)
@click.option("--out", "-o", type=click.Path(path_type=Path), default=None, help="Output file path")
@DIR_OPTION
def cmd_dump(agent: str, source: str, out: Path | None, mnemo_dir: Path | None) -> None:
    """Dump agent memories to a JSON file."""
    base = _resolve_base(mnemo_dir)
    require_agent(agent, base)

    with console.status(f"[cyan]Fetching memories from [bold]{source}[/]…"):
        if source == "local":
            latest = latest_dump_path(agent, base)
            dump = load_dump(latest)
        elif source == "mem0":
            try:
                cfg = load_config(agent, base)
            except FileNotFoundError as e:
                raise click.ClickException(str(e))
            if not cfg.mem0_api_key:
                raise click.ClickException(
                    "mem0 API key not set. Add mem0_api_key to config.yaml"
                )
            from mnemo.adapters.mem0_adapter import dump_from_mem0
            dump = dump_from_mem0(
                api_key=cfg.mem0_api_key,
                user_id=cfg.mem0_user_id or agent,
                agent=agent,
            )
        elif source == "letta":
            try:
                cfg = load_config(agent, base)
            except FileNotFoundError as e:
                raise click.ClickException(str(e))
            if not cfg.letta_agent_id:
                raise click.ClickException(
                    "letta_agent_id not set. Add it to config.yaml"
                )
            from mnemo.adapters.letta_adapter import dump_from_letta
            dump = dump_from_letta(
                base_url=cfg.letta_base_url or "http://localhost:8283",
                agent_id=cfg.letta_agent_id,
                agent_name=agent,
            )
        else:
            raise click.ClickException(f"Unknown source: {source}")

    # Determine output path
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out or (dumps_dir(agent, base) / f"{ts_str}.json")
    save_dump(dump, out_path)

    # Also keep latest.json updated
    latest = latest_dump_path(agent, base)
    save_dump(dump, latest)

    console.print(f"[green]✓[/] Dumped [bold]{len(dump.facts)}[/] facts → {out_path}")


# ─── mnemo load ───────────────────────────────────────────────────────────────


@cli.command("load")
@click.option("--file", "-f", "file_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--agent", "-a", required=True, help="Target agent name")
@click.option(
    "--target",
    type=click.Choice(["mem0", "letta", "local"]),
    default="local",
    show_default=True,
)
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@DIR_OPTION
def cmd_load(
    file_path: Path,
    agent: str,
    target: str,
    dry_run: bool,
    mnemo_dir: Path | None,
) -> None:
    """Load a JSON dump into an agent memory store."""
    base = _resolve_base(mnemo_dir)

    dump = load_dump(file_path)
    console.print(f"[cyan]Loaded[/] {len(dump.facts)} facts from [bold]{file_path.name}[/]")

    if dry_run:
        console.print("[yellow]--dry-run:[/] no changes written.")
        return

    if target == "local":
        require_agent(agent, base)
        dest = latest_dump_path(agent, base)
        # Merge into existing
        try:
            existing = load_dump(dest)
            existing_ids = {f.id for f in existing.facts}
            new_facts = [f for f in dump.facts if f.id not in existing_ids]
            existing.facts.extend(new_facts)
            save_dump(existing, dest)
            console.print(f"[green]✓[/] Merged {len(new_facts)} new facts into {dest}")
        except FileNotFoundError:
            save_dump(dump, dest)
            console.print(f"[green]✓[/] Saved {len(dump.facts)} facts to {dest}")

    elif target == "mem0":
        cfg = load_config(agent, base)
        if not cfg.mem0_api_key:
            raise click.ClickException("mem0_api_key not set in config.yaml")
        from mnemo.adapters.mem0_adapter import load_to_mem0
        pushed = load_to_mem0(dump, api_key=cfg.mem0_api_key, user_id=cfg.mem0_user_id or agent)
        console.print(f"[green]✓[/] Pushed {pushed} facts to Mem0")

    elif target == "letta":
        cfg = load_config(agent, base)
        if not cfg.letta_agent_id:
            raise click.ClickException("letta_agent_id not set in config.yaml")
        from mnemo.adapters.letta_adapter import load_to_letta
        pushed = load_to_letta(dump, base_url=cfg.letta_base_url or "http://localhost:8283", agent_id=cfg.letta_agent_id)
        console.print(f"[green]✓[/] Pushed {pushed} facts to Letta")


# ─── mnemo ls ─────────────────────────────────────────────────────────────────


@cli.command("ls")
@AGENT_OPTION
@DIR_OPTION
@click.option("--pretty", "-p", is_flag=True, help="Rich table output")
def cmd_ls(agent: str | None, mnemo_dir: Path | None, pretty: bool) -> None:
    """List agents and their memory dumps."""
    base = _resolve_base(mnemo_dir)
    agents = list_agents(base) if not agent or agent == "all" else [agent]

    if not agents:
        console.print("[yellow]No agents found.[/] Run: mnemo init --agent <name>")
        return

    table = Table(
        title="🧠 mnemo agents",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold cyan",
    )
    table.add_column("Agent", style="bold")
    table.add_column("Facts", justify="right")
    table.add_column("Dumps", justify="right")
    table.add_column("Last Dump", style="dim")
    table.add_column("Dir")

    for ag in agents:
        adir = base / ag
        dump_files = list_dump_files(ag, base)
        latest = latest_dump_path(ag, base)
        fact_count = "—"
        last_ts = "—"
        try:
            d = load_dump(latest)
            fact_count = str(len(d.facts))
            last_ts = _fmt_ts(d.dump_ts)
        except (FileNotFoundError, ValueError):
            pass
        table.add_row(ag, fact_count, str(len(dump_files)), last_ts, str(adir))

    console.print(table)


# ─── mnemo show ───────────────────────────────────────────────────────────────


@cli.command("show")
@click.option("--dump", "-f", "dump_file", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["pretty", "json"]),
    default="pretty",
    show_default=True,
)
def cmd_show(dump_file: Path, fmt: str) -> None:
    """Display the contents of a dump file."""
    dump = load_dump(dump_file)

    if fmt == "json":
        click.echo(dump.model_dump_json(indent=2))
        return

    # Pretty table
    table = Table(
        title=f"🧠 {dump.agent}  ·  {len(dump.facts)} facts  ·  {_fmt_ts(dump.dump_ts)}",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold magenta",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Entity", style="bold")
    table.add_column("Attribute", style="cyan")
    table.add_column("Value")
    table.add_column("Conf", justify="right", width=6)
    table.add_column("Source", style="dim", width=8)
    table.add_column("Timestamp", style="dim")

    for i, fact in enumerate(dump.facts, 1):
        color = _conf_color(fact.confidence)
        table.add_row(
            str(i),
            fact.entity,
            fact.attribute,
            fact.value,
            f"[{color}]{fact.confidence:.2f}[/]",
            fact.source,
            _fmt_ts(fact.timestamp),
        )

    console.print(table)


# ─── mnemo diff ───────────────────────────────────────────────────────────────


@cli.command("diff")
@click.argument("dump_a", type=click.Path(exists=True, path_type=Path))
@click.argument("dump_b", type=click.Path(exists=True, path_type=Path))
@click.option("--html", "out_html", type=click.Path(path_type=Path), default=None, help="Save as HTML mermaid report")
@click.option("--graph", "out_graph", type=click.Path(path_type=Path), default=None, help="Save as PNG graph (requires mnemo[graph])")
def cmd_diff(dump_a: Path, dump_b: Path, out_html: Path | None, out_graph: Path | None) -> None:
    """Show differences between two memory dumps."""
    da = load_dump(dump_a)
    db = load_dump(dump_b)
    added, removed, common = diff_dumps(da, db)

    console.print(
        Panel.fit(
            f"[dim]A:[/] {dump_a.name}  ({len(da.facts)} facts)\n"
            f"[dim]B:[/] {dump_b.name}  ({len(db.facts)} facts)\n\n"
            f"[green]+{len(added)} added[/]   [red]-{len(removed)} removed[/]   [dim]{len(common)} unchanged[/]",
            title="🔍 mnemo diff",
            border_style="magenta",
        )
    )

    def _fact_table(title: str, facts: list[Fact], style: str) -> None:
        if not facts:
            return
        t = Table(title=title, box=box.SIMPLE, header_style=f"bold {style}")
        t.add_column("Entity")
        t.add_column("Attribute")
        t.add_column("Value")
        t.add_column("Conf", justify="right")
        for f in facts:
            t.add_row(f.entity, f.attribute, f.value, f"{f.confidence:.2f}")
        console.print(t)

    _fact_table("[green]Added in B[/]", added, "green")
    _fact_table("[red]Removed from A[/]", removed, "red")

    if out_html:
        _write_diff_html(da, db, added, removed, common, out_html)
        console.print(f"[green]✓[/] HTML report saved to {out_html}")

    if out_graph:
        _write_diff_graph(da, db, added, removed, out_graph)
        console.print(f"[green]✓[/] Graph PNG saved to {out_graph}")


def _write_diff_html(da: AgentDump, db: AgentDump, added: list, removed: list, common: list, path: Path) -> None:
    """Write a self-contained HTML diff report with an embedded Mermaid diagram."""
    mermaid_lines = ["graph TD"]
    mermaid_lines.append(f'  subgraph A["{da.agent} (A)"]')
    for f in removed[:15]:
        label = f"{f.entity}:{f.attribute}"
        mermaid_lines.append(f'    R_{f.id[:6]}["{label}"]')
    mermaid_lines.append("  end")
    mermaid_lines.append(f'  subgraph B["{db.agent} (B)"]')
    for f in added[:15]:
        label = f"{f.entity}:{f.attribute}"
        mermaid_lines.append(f'    A_{f.id[:6]}["{label}"]')
    mermaid_lines.append("  end")
    for f in common[:10]:
        mermaid_lines.append(f'  R_{f.id[:6]} --> A_{f.id[:6]}')

    mermaid_src = "\n".join(mermaid_lines)

    rows_added = "".join(
        f"<tr class='added'><td>+</td><td>{f.entity}</td><td>{f.attribute}</td><td>{f.value}</td><td>{f.confidence:.2f}</td></tr>"
        for f in added
    )
    rows_removed = "".join(
        f"<tr class='removed'><td>-</td><td>{f.entity}</td><td>{f.attribute}</td><td>{f.value}</td><td>{f.confidence:.2f}</td></tr>"
        for f in removed
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>mnemo diff: {da.agent} vs {db.agent}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: system-ui; background:#0f0f0f; color:#e2e2e2; padding:2rem; }}
  h1 {{ color:#a78bfa; }}
  .mermaid {{ background:#1a1a2e; padding:1rem; border-radius:8px; margin:1rem 0; }}
  table {{ border-collapse:collapse; width:100%; margin-top:1rem; }}
  th {{ background:#1e1e2e; padding:.5rem 1rem; text-align:left; }}
  td {{ padding:.4rem 1rem; border-bottom:1px solid #2e2e3e; }}
  .added {{ background:#0d2d1a; color:#4ade80; }}
  .removed {{ background:#2d0d0d; color:#f87171; }}
</style>
</head>
<body>
<h1>🧠 mnemo diff</h1>
<p><strong>A:</strong> {da.agent} ({len(da.facts)} facts) &nbsp;→&nbsp; <strong>B:</strong> {db.agent} ({len(db.facts)} facts)</p>
<p><span style="color:#4ade80">+{len(added)} added</span> &nbsp; <span style="color:#f87171">-{len(removed)} removed</span> &nbsp; {len(common)} unchanged</p>
<div class="mermaid">{mermaid_src}</div>
<table>
<thead><tr><th>Δ</th><th>Entity</th><th>Attribute</th><th>Value</th><th>Conf</th></tr></thead>
<tbody>{rows_added}{rows_removed}</tbody>
</table>
<script>mermaid.initialize({{startOnLoad:true, theme:'dark'}});</script>
</body></html>"""

    path.write_text(html)


def _write_diff_graph(da: AgentDump, db: AgentDump, added: list, removed: list, path: Path) -> None:
    try:
        import networkx as nx  # type: ignore
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        raise click.ClickException(
            "Graph output requires: pip install mnemo[graph]"
        )

    G = nx.DiGraph()
    for f in added:
        G.add_node(f"{f.entity}:{f.attribute}", color="green", label=f.attribute)
    for f in removed:
        G.add_node(f"{f.entity}:{f.attribute}", color="red", label=f.attribute)

    node_colors = [G.nodes[n].get("color", "skyblue") for n in G.nodes]
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.set_facecolor("#0f0f0f")
    fig.patch.set_facecolor("#0f0f0f")
    pos = nx.spring_layout(G, seed=42)
    nx.draw_networkx(G, pos, ax=ax, node_color=node_colors, font_color="white", edge_color="#555")
    ax.set_title(f"mnemo diff: {da.agent} vs {db.agent}", color="white")
    plt.tight_layout()
    plt.savefig(path, facecolor=fig.get_facecolor())
    plt.close()


# ─── mnemo recall ─────────────────────────────────────────────────────────────


@cli.command("recall")
@click.argument("query")
@AGENT_OPTION
@DIR_OPTION
@click.option("--limit", "-n", default=5, show_default=True, help="Max results")
def cmd_recall(query: str, agent: str | None, mnemo_dir: Path | None, limit: int) -> None:
    """Recall memories by natural-language query (TF-IDF)."""
    base = _resolve_base(mnemo_dir)
    target_agents = [agent] if agent else list_agents(base)

    if not target_agents:
        console.print("[yellow]No agents found.[/]")
        return

    dumps = []
    for ag in target_agents:
        try:
            d = load_dump(latest_dump_path(ag, base))
            dumps.append(d)
        except (FileNotFoundError, ValueError):
            pass

    results = search_dumps(dumps, query, limit=limit)

    if not results:
        console.print(f"[yellow]No matches found for:[/] {query}")
        return

    table = Table(
        title=f"🔍 recall: \"{query}\"",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("Score", justify="right", width=8)
    table.add_column("Agent", width=12)
    table.add_column("Entity")
    table.add_column("Attribute", style="cyan")
    table.add_column("Value")
    table.add_column("Conf", justify="right", width=6)

    for r in results:
        color = _conf_color(r.fact.confidence)
        table.add_row(
            f"{r.score:.4f}",
            r.agent,
            r.fact.entity,
            r.fact.attribute,
            r.fact.value,
            f"[{color}]{r.fact.confidence:.2f}[/]",
        )

    console.print(table)


# ─── mnemo search ─────────────────────────────────────────────────────────────


@cli.command("search")
@click.argument("query")
@AGENT_OPTION
@DIR_OPTION
@click.option("--limit", "-n", default=10, show_default=True)
def cmd_search(query: str, agent: str | None, mnemo_dir: Path | None, limit: int) -> None:
    """Search memories (alias for recall with higher default limit)."""
    ctx = click.get_current_context()
    ctx.invoke(cmd_recall, query=query, agent=agent, mnemo_dir=mnemo_dir, limit=limit)


# ─── mnemo migrate ────────────────────────────────────────────────────────────


@cli.command("migrate")
@click.option("--dump", "-f", "dump_file", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--target", required=True, type=click.Choice(["mem0", "letta", "local"]))
@click.option("--agent", "-a", required=True)
@click.option("--min-conf", default=0.0, help="Only migrate facts above this confidence")
@click.option("--dry-run", is_flag=True)
@DIR_OPTION
def cmd_migrate(
    dump_file: Path,
    target: str,
    agent: str,
    min_conf: float,
    dry_run: bool,
    mnemo_dir: Path | None,
) -> None:
    """Migrate facts from a dump file to a target store."""
    base = _resolve_base(mnemo_dir)
    dump = load_dump(dump_file)

    original_count = len(dump.facts)
    if min_conf > 0:
        dump.facts = [f for f in dump.facts if f.confidence >= min_conf]

    console.print(
        f"[cyan]Migrating[/] {len(dump.facts)}/{original_count} facts "
        f"[dim](min-conf={min_conf:.2f})[/] → [bold]{target}[/]"
    )

    if dry_run:
        console.print("[yellow]--dry-run:[/] no data written.")
        return

    if target == "local":
        require_agent(agent, base)
        ctx = click.get_current_context()
        # Write temp file then load
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp = Path(tf.name)
        save_dump(dump, tmp)
        try:
            ctx.invoke(cmd_load, file_path=tmp, agent=agent, target="local", dry_run=False, mnemo_dir=mnemo_dir)
        finally:
            os.unlink(tmp)

    elif target == "mem0":
        cfg = load_config(agent, base)
        if not cfg.mem0_api_key:
            raise click.ClickException("mem0_api_key not set in config.yaml")
        from mnemo.adapters.mem0_adapter import load_to_mem0
        pushed = load_to_mem0(dump, api_key=cfg.mem0_api_key, user_id=cfg.mem0_user_id or agent)
        console.print(f"[green]✓[/] Migrated {pushed} facts to Mem0")

    elif target == "letta":
        cfg = load_config(agent, base)
        from mnemo.adapters.letta_adapter import load_to_letta
        pushed = load_to_letta(dump, base_url=cfg.letta_base_url or "http://localhost:8283", agent_id=cfg.letta_agent_id or "")
        console.print(f"[green]✓[/] Migrated {pushed} facts to Letta")


# ─── mnemo add ────────────────────────────────────────────────────────────────


@cli.command("add")
@click.option("--fact", "-f", "fact_text", required=True, help="Free-text fact to store")
@AGENT_OPTION
@DIR_OPTION
@click.option("--entity", default="user", show_default=True, help="Entity this fact is about")
@click.option("--attribute", default="memory", show_default=True, help="Attribute/category")
@click.option("--confidence", "-c", default=1.0, type=float, show_default=True)
@click.option("--source", default="manual", type=click.Choice(["chat", "tool", "manual", "import"]))
def cmd_add(
    fact_text: str,
    agent: str | None,
    mnemo_dir: Path | None,
    entity: str,
    attribute: str,
    confidence: float,
    source: str,
) -> None:
    """Add a fact to an agent's memory store."""
    base = _resolve_base(mnemo_dir)
    ag = _resolve_agent(agent)
    require_agent(ag, base)

    latest = latest_dump_path(ag, base)
    try:
        dump = load_dump(latest)
    except FileNotFoundError:
        dump = AgentDump(agent=ag)

    fact = Fact(
        entity=entity,
        attribute=attribute,
        value=fact_text,
        source=source,  # type: ignore[arg-type]
        confidence=confidence,
    )
    dump.facts.append(fact)
    save_dump(dump, latest)

    color = _conf_color(fact.confidence)
    console.print(
        f"[green]✓[/] Added fact [{color}]{fact.id[:8]}[/] to [bold]{ag}[/]\n"
        f"  [dim]{fact.entity}[/] · [cyan]{fact.attribute}[/] · {fact.value}"
    )


# ─── mnemo serve ──────────────────────────────────────────────────────────────


@cli.command("serve")
@click.option("--agent", "-a", required=True)
@click.option("--port", "-p", default=8080, show_default=True)
@click.option("--read-only", is_flag=True, help="Disable write endpoints")
@DIR_OPTION
def cmd_serve(agent: str, port: int, read_only: bool, mnemo_dir: Path | None) -> None:
    """Start the MCP-compatible FastAPI server for an agent."""
    base = _resolve_base(mnemo_dir)
    require_agent(agent, base)

    try:
        import uvicorn  # type: ignore
    except ImportError:
        raise click.ClickException("uvicorn not installed. Run: pip install uvicorn")

    from mnemo.server import create_app

    app = create_app(agent=agent, base=base, read_only=read_only)

    console.print(
        Panel.fit(
            f"[bold cyan]Agent:[/]     {agent}\n"
            f"[bold cyan]Port:[/]      {port}\n"
            f"[bold cyan]Read-only:[/] {read_only}\n\n"
            f"[dim]MCP tools:  http://localhost:{port}/mcp/list_tools[/]\n"
            f"[dim]Docs:       http://localhost:{port}/docs[/]",
            title="🌐 mnemo serve",
            border_style="green",
        )
    )

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    cli()
