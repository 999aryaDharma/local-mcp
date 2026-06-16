"""
docctx CLI — Typer app with all commands.

Commands:
    add        Ingest a URL as a new context pack
    refresh    Re-crawl an existing pack
    remove     Hard delete a pack and all its data
    list       List all packs
    query      Search documentation (for testing/debugging)
    inspect    Inspect extraction for a URL or pack structure
    explain    Explain retrieval reasoning for a query
    eval       Run retrieval evaluation harness
    doctor     Run health checks
    serve      Start the MCP server
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from docctx.config import load_config, write_default_config
from docctx.db.connection import init_db
from docctx.exceptions import (
    DocctxError,
    PackExistsError,
    PackNotFoundError,
    ScopeAmbiguousError,
)
from docctx.paths import get_config_path, get_db_path

app = typer.Typer(
    name="docctx",
    help="Local-first deterministic context retrieval engine for coding agents.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

# ── Logging setup ──────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )


# ── add ────────────────────────────────────────────────────────────────────────


@app.command()
def add(
    url: str = typer.Argument(..., help="Entry URL to ingest as a context pack."),
    name: Optional[str] = typer.Option(None, "--as", help="Pack name (default: derived from URL)."),
    scope: Optional[str] = typer.Option(
        None, "--scope", help="Crawl scope: page-only | siblings | subtree | site"
    ),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Version tag (e.g. '19')."),
    trust: int = typer.Option(1, "--trust", help="Trust tier: 1=official, 2=community."),
    rate_limit: float = typer.Option(1.0, "--rate-limit", help="Requests per second."),
    max_pages: int = typer.Option(50, "--max-pages", help="Maximum pages to crawl."),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Show debug output."),
    output_json: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """[bold]Add[/bold] a new context pack by ingesting a URL."""
    _setup_logging(verbose)
    init_db()

    from docctx.config import load_config
    cfg = load_config()
    cfg.ingestion.rate_limit_rps = rate_limit
    cfg.ingestion.max_pages = max_pages

    from docctx.ingestion.pipeline import run_add

    pages_done = []

    def progress_cb(page_url: str, stage: str) -> None:
        pages_done.append((page_url, stage))

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"Ingesting {url}...", total=None)

            def _cb(page_url: str, stage: str) -> None:
                progress.update(task, description=f"[{stage}] {page_url[:60]}...")

            result = asyncio.run(
                run_add(
                    url=url,
                    pack_name=name,
                    scope=scope,
                    version=version,
                    trust_tier=trust,
                    config=cfg,
                    progress_cb=_cb,
                )
            )

        if output_json:
            print(json.dumps({
                "pack": result.pack_name,
                "entry_url": result.entry_url,
                "ok_pages": len(result.ok_pages),
                "failed_pages": len(result.failed_pages),
                "total_chunks": result.total_chunks,
            }))
        else:
            console.print(
                Panel(
                    f"[green]OK[/green] Pack [bold]{result.pack_name}[/bold] added\n"
                    f"Pages ingested: [bold]{len(result.ok_pages)}[/bold]"
                    + (f"  Failed: [red]{len(result.failed_pages)}[/red]" if result.failed_pages else "")
                    + f"\nChunks indexed: [bold]{result.total_chunks}[/bold]",
                    title="docctx add",
                    border_style="green",
                )
            )
            if result.failed_pages and verbose:
                for p in result.failed_pages:
                    err_console.print(f"  [red]FAIL[/red] {p.url}: {p.error}")

    except PackExistsError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        if e.hint:
            err_console.print(f"[dim]Hint: {e.hint}[/dim]")
        raise typer.Exit(1)
    except ScopeAmbiguousError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        if e.hint:
            err_console.print(f"[dim]Hint: {e.hint}[/dim]")
        raise typer.Exit(1)
    except DocctxError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ── refresh ────────────────────────────────────────────────────────────────────


@app.command()
def refresh(
    pack_name: str = typer.Argument(..., help="Pack name to re-crawl."),
    force: bool = typer.Option(False, "--force", "-f", help="Force full re-ingest (ignore cache)."),
    verbose: bool = typer.Option(False, "--verbose", "-V"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Refresh[/bold] an existing pack by re-crawling its source."""
    _setup_logging(verbose)

    from docctx.ingestion.pipeline import run_refresh

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"Refreshing {pack_name}...", total=None)

            def _cb(page_url: str, stage: str) -> None:
                progress.update(task, description=f"[{stage}] {page_url[:60]}...")

            result = asyncio.run(run_refresh(pack_name=pack_name, force=force, progress_cb=_cb))

        if output_json:
            print(json.dumps({
                "pack": result.pack_name,
                "ok_pages": len(result.ok_pages),
                "unchanged_pages": len(result.unchanged_pages),
                "failed_pages": len(result.failed_pages),
                "total_chunks": result.total_chunks,
            }))
        else:
            console.print(
                Panel(
                    f"[green]OK[/green] Pack [bold]{result.pack_name}[/bold] refreshed\n"
                    f"Updated: {len(result.ok_pages)}  "
                    f"Unchanged: {len(result.unchanged_pages)}  "
                    + (f"Failed: [red]{len(result.failed_pages)}[/red]" if result.failed_pages else "Failed: 0")
                    + f"\nChunks: [bold]{result.total_chunks}[/bold]",
                    title="docctx refresh",
                    border_style="green",
                )
            )
    except PackNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except DocctxError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ── remove ─────────────────────────────────────────────────────────────────────


@app.command()
def remove(
    pack_name: str = typer.Argument(..., help="Pack name to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Remove[/bold] a pack and all its data (documents, chunks)."""
    if not yes:
        confirmed = typer.confirm(
            f"Delete pack '{pack_name}' and all its data? This cannot be undone."
        )
        if not confirmed:
            console.print("Aborted.")
            raise typer.Exit(0)

    from docctx.db.connection import db_connection
    from docctx.db.queries import delete_pack, get_pack

    with db_connection() as conn:
        pack = get_pack(conn, pack_name)
        if pack is None:
            err_console.print(f"[red]Error:[/red] Pack '{pack_name}' not found.")
            raise typer.Exit(1)

        delete_pack(conn, pack_name)
        conn.commit()

    if output_json:
        print(json.dumps({"deleted": pack_name, "ok": True}))
    else:
        console.print(f"[green]OK[/green] Pack [bold]{pack_name}[/bold] deleted.")


# ── list ───────────────────────────────────────────────────────────────────────


@app.command(name="list")
def cmd_list_packs(
    verbose: bool = typer.Option(False, "--verbose", "-V"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]List[/bold] all context packs."""
    from docctx.retrieval.service import RetrievalService

    svc = RetrievalService()
    packs = svc.list_packs()

    if output_json:
        print(json.dumps({"packs": packs, "total": len(packs)}))
        return

    if not packs:
        console.print("[dim]No packs found. Run [bold]docctx add <url>[/bold] to get started.[/dim]")
        return

    table = Table(title=f"Context Packs ({len(packs)} total)", show_header=True, header_style="bold")
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Chunks", justify="right")
    table.add_column("Freshness")
    table.add_column("Last Refreshed")

    if verbose:
        table.add_column("Scope")
        table.add_column("Version")
        table.add_column("Docs", justify="right")

    for p in packs:
        freshness = p.get("freshness", "unknown")
        freshness_color = {"fresh": "green", "stale": "yellow", "very_stale": "red"}.get(freshness, "dim")
        freshness_text = Text(freshness, style=freshness_color)

        last_refreshed = p.get("last_refreshed") or "never"
        if last_refreshed and last_refreshed != "never":
            last_refreshed = last_refreshed[:10]  # date only

        row = [
            p["name"],
            str(p.get("chunks", 0)),
            freshness_text,
            last_refreshed,
        ]
        if verbose:
            row += [
                p.get("scope_rule", ""),
                p.get("version") or "",
                str(p.get("docs", 0)),
            ]
        table.add_row(*row)

    console.print(table)


# ── query ──────────────────────────────────────────────────────────────────────


@app.command()
def query(
    query_text: str = typer.Argument(..., help="Search query."),
    pack: Optional[str] = typer.Option(None, "--pack", "-p", help="Filter to pack (glob supported)."),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results."),
    min_confidence: str = typer.Option("any", "--min-confidence", help="high | low | any"),
    format_mode: str = typer.Option("standard", "--format", help="compact | standard | full"),
    output_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-V"),
) -> None:
    """[bold]Query[/bold] documentation for testing and debugging."""
    _setup_logging(verbose)

    from docctx.retrieval.service import RetrievalService

    svc = RetrievalService()
    response = svc.search(
        query=query_text,
        pack=pack,
        limit=limit,
        min_confidence=min_confidence,
        response_mode=format_mode,
    )

    if output_json:
        print(json.dumps({
            "chunks": [
                {
                    "id": c.id,
                    "pack": c.pack,
                    "heading_path": c.heading_path,
                    "summary": c.summary,
                    "content_preview": c.content_preview,
                    "url": c.url,
                    "score": c.score,
                    "confidence": c.confidence,
                }
                for c in response.chunks
            ],
            "result_status": response.result_status,
            "scanned_packs": response.scanned_packs,
            "scanned_chunks": response.scanned_chunks,
            "suggestion": response.suggestion,
        }))
        return

    if not response.chunks:
        console.print(f"[yellow]No results[/yellow] — status: {response.result_status}")
        if response.suggestion:
            console.print(f"[dim]{response.suggestion}[/dim]")
        return

    console.print(
        f"\n[bold]Query:[/bold] {query_text}  "
        f"[dim](scanned {response.scanned_packs} packs, {response.scanned_chunks} chunks)[/dim]\n"
    )

    for i, chunk in enumerate(response.chunks, 1):
        conf_color = "green" if chunk.confidence == "high" else "yellow"
        console.print(
            f"[{i}] [bold]{chunk.heading_path}[/bold]  "
            f"[dim]{chunk.pack}[/dim]  "
            f"score=[{conf_color}]{chunk.score:.2f}[/{conf_color}]  "
            f"conf=[{conf_color}]{chunk.confidence}[/{conf_color}]"
        )
        console.print(f"    [dim]{chunk.url}[/dim]")
        console.print(f"    {chunk.summary}")
        if format_mode in ("standard", "full") and chunk.content_preview:
            console.print(f"    [dim]{chunk.content_preview[:120]}…[/dim]")
        console.print()


# ── inspect ────────────────────────────────────────────────────────────────────


@app.command()
def inspect(
    target: str = typer.Argument(..., help="URL to inspect extraction, or pack name to inspect structure."),
    output_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-V"),
) -> None:
    """[bold]Inspect[/bold] extraction for a URL, or pack structure for a pack name."""
    _setup_logging(verbose)

    from docctx.observability.inspect import (
        format_inspect_pack,
        inspect_pack,
        inspect_url,
    )

    # Determine if target is a URL or pack name
    is_url = target.startswith("http://") or target.startswith("https://")

    try:
        if is_url:
            data = inspect_url(target)
        else:
            data = inspect_pack(target)

        if output_json:
            print(json.dumps(data, indent=2, default=str))
        else:
            if is_url:
                console.print(Panel(
                    f"URL: {data['url']}\n"
                    f"Title: {data.get('title', '(none)')}\n"
                    f"Markdown length: {data['markdown_length']} chars (~{data['token_estimate']} tokens)\n"
                    f"Headings: {data['heading_count']}  Code blocks: {data['code_blocks']}\n"
                    f"Chunks produced: {data['chunks_produced']}",
                    title="inspect url",
                ))
                if data.get("chunk_preview"):
                    console.print("\n[bold]Chunk preview:[/bold]")
                    for c in data["chunk_preview"]:
                        console.print(
                            f"  [{c['index']}] {c['heading_path']} "
                            f"[dim]({c['token_count']} tokens)[/dim]"
                        )
                        console.print(f"      {c['summary']}")
            else:
                console.print(format_inspect_pack(data))

    except PackNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        raise typer.Exit(1)


# ── explain ────────────────────────────────────────────────────────────────────


@app.command()
def explain(
    query_text: str = typer.Argument(..., help="Query to explain retrieval for."),
    pack: Optional[str] = typer.Option(None, "--pack", "-p"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Explain[/bold] retrieval reasoning — shows scores, boosts, and threshold decisions."""
    from docctx.retrieval.explain import explain_query, format_explain_report

    report = explain_query(query_text, pack=pack)

    if output_json:
        print(json.dumps({
            "query": report.query,
            "pack_filter": report.pack_filter,
            "scanned_packs": report.scanned_packs,
            "scanned_chunks": report.scanned_chunks,
            "floor_score": report.floor_score,
            "confidence_cutoff": report.confidence_cutoff,
            "results": report.results,
        }, indent=2))
    else:
        console.print(format_explain_report(report))


# ── eval ───────────────────────────────────────────────────────────────────────


@app.command(name="eval")
def run_eval(
    dataset_path: str = typer.Argument(..., help="Path to JSON evaluation dataset."),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Eval[/bold] — run retrieval evaluation harness."""
    from docctx.eval.harness import EvalDataset, EvalHarness

    try:
        dataset = EvalDataset.from_json(dataset_path)
    except FileNotFoundError:
        err_console.print(f"[red]Error:[/red] Dataset not found at {dataset_path}")
        raise typer.Exit(1)
    except json.JSONDecodeError:
        err_console.print(f"[red]Error:[/red] Invalid JSON in {dataset_path}")
        raise typer.Exit(1)

    harness = EvalHarness()
    report = harness.run(dataset)

    if output_json:
        # Convert dataclasses to dict manually or use asdict
        import dataclasses
        print(json.dumps(dataclasses.asdict(report), indent=2))
        return

    console.print(f"\n[bold]Eval Report[/bold] ({report.total_queries} queries)\n")
    
    table = Table(show_header=True, header_style="bold")
    table.add_column("Query", style="cyan")
    table.add_column("Category")
    table.add_column("Hit@1")
    table.add_column("Hit@5")
    table.add_column("Top Score", justify="right")

    for r in report.results:
        h1 = "[green]YES[/green]" if r.hit_at_1 else "[red]NO[/red]"
        h5 = "[green]YES[/green]" if r.hit_at_5 else "[red]NO[/red]"
        if r.is_empty:
            h1 = "[dim]empty[/dim]"
            h5 = "[dim]empty[/dim]"
            
        table.add_row(
            r.query[:40] + ("..." if len(r.query) > 40 else ""),
            r.category,
            h1,
            h5,
            f"{r.top_score:.2f}",
        )
    
    console.print(table)
    console.print(f"\nMetrics:")
    console.print(f"Hit@1 Rate:   [bold]{report.hit_at_1_rate * 100:.1f}%[/bold]")
    console.print(f"Hit@5 Rate:   [bold]{report.hit_at_5_rate * 100:.1f}%[/bold]")
    console.print(f"Empty Rate:   {report.empty_rate * 100:.1f}%")
    console.print(f"Avg TopScore: {report.avg_top_score:.2f}")


# ── doctor ─────────────────────────────────────────────────────────────────────


@app.command()
def doctor(
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """[bold]Doctor[/bold] — run health checks on the docctx installation."""
    from docctx.observability.doctor import format_doctor_report, run_doctor

    results = run_doctor()

    if output_json:
        print(json.dumps([
            {
                "name": r.name,
                "ok": r.ok,
                "description": r.description,
                "hint": r.hint,
            }
            for r in results
        ], indent=2))
        return

    all_ok = all(r.ok for r in results)
    for r in results:
        icon = "[green]OK[/green]" if r.ok else "[red]ERR[/red]"
        status = "OK" if r.ok else "ERROR"
        console.print(f"[{icon}] [{status}] {r.name}: {r.description}")
        if not r.ok and r.hint:
            console.print(f"  [dim]-> {r.hint}[/dim]")

    console.print()
    if all_ok:
        console.print("[green]All checks passed.[/green] docctx is healthy.")
    else:
        errors = sum(1 for r in results if not r.ok)
        console.print(f"[red]{errors} check(s) failed.[/red]")
        raise typer.Exit(1)


# ── serve ──────────────────────────────────────────────────────────────────────


@app.command()
def serve(
    verbose: bool = typer.Option(False, "--verbose", "-V"),
) -> None:
    """[bold]Serve[/bold] — start the MCP server on stdio."""
    _setup_logging(verbose)

    from docctx.mcp_server import serve as _serve
    _serve()


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    # Ensure config file exists with defaults
    cfg_path = get_config_path()
    write_default_config(cfg_path)

    app()


if __name__ == "__main__":
    main()
