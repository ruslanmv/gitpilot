from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .version import __version__
from .settings import get_settings, LLMProvider
from .model_catalog import list_models_for_provider


cli = typer.Typer(add_completion=False, help="GitPilot - Agentic AI assistant for GitHub")
console = Console()


def _check_configuration():
    """Check and display configuration status."""
    issues = []
    warnings = []

    # Check for .env file
    env_file = Path.cwd() / ".env"
    has_env = env_file.exists()

    # Check GitHub token
    github_token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not github_token:
        issues.append("❌ GitHub token not found")
        warnings.append("  Set GITPILOT_GITHUB_TOKEN or GITHUB_TOKEN in .env")
        warnings.append("  Get token at: https://github.com/settings/tokens")

    # Check LLM provider configuration
    settings = get_settings()
    provider = settings.provider

    provider_configured = False
    if provider == LLMProvider.openai:
        api_key = settings.openai.api_key or os.getenv("OPENAI_API_KEY")
        provider_configured = bool(api_key)
    elif provider == LLMProvider.claude:
        api_key = settings.claude.api_key or os.getenv("ANTHROPIC_API_KEY")
        provider_configured = bool(api_key)
    elif provider == LLMProvider.watsonx:
        api_key = settings.watsonx.api_key or os.getenv("WATSONX_API_KEY")
        provider_configured = bool(api_key)
    elif provider == LLMProvider.ollama:
        # Ollama doesn't require API key, just needs to be running
        provider_configured = True

    if not provider_configured:
        issues.append(f"❌ {provider.value.upper()} API key not configured")
        warnings.append(f"  Configure in Admin UI or set environment variable")

    return has_env, github_token is not None, provider_configured, issues, warnings


def _display_startup_banner(host: str, port: int):
    """Display a professional startup banner with configuration status."""
    console.print()

    # Header
    console.print(Panel.fit(
        f"[bold cyan]GitPilot[/bold cyan] [dim]v{__version__}[/dim]\n"
        "[white]Agentic AI Assistant for GitHub Repositories[/white]",
        border_style="cyan"
    ))

    # Check configuration
    has_env, has_github, has_llm, issues, warnings = _check_configuration()
    settings = get_settings()

    # Configuration table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")

    # Environment file status
    env_status = "✅ Found" if has_env else "⚠️  Not found (using defaults)"
    table.add_row("Environment File", env_status)

    # GitHub token status
    github_status = "✅ Configured" if has_github else "❌ Not configured"
    table.add_row("GitHub Token", github_status)

    # LLM Provider status
    provider_name = settings.provider.value.upper()
    llm_status = f"✅ {provider_name}" if has_llm else f"⚠️  {provider_name} (not configured)"
    table.add_row("LLM Provider", llm_status)

    # Server info
    table.add_row("Server", f"http://{host}:{port}")

    console.print(table)
    console.print()

    # Display issues and warnings
    if issues:
        console.print("[bold yellow]⚠️  Configuration Issues:[/bold yellow]")
        for issue in issues:
            console.print(f"  {issue}")
        for warning in warnings:
            console.print(f"  [dim]{warning}[/dim]")
        console.print()

    # Setup instructions if needed
    if not has_env and (not has_github or not has_llm):
        console.print(Panel(
            "[bold]Quick Setup:[/bold]\n\n"
            "1. Copy .env.template to .env:\n"
            "   [cyan]cp .env.template .env[/cyan]\n\n"
            "2. Edit .env and add your credentials\n\n"
            "3. Or configure via Admin UI in your browser\n\n"
            "[dim]See README.md for detailed setup instructions[/dim]",
            title="[yellow]Setup Required[/yellow]",
            border_style="yellow"
        ))
    else:
        console.print("[bold green]✓[/bold green] GitPilot is ready!")
        console.print()
        console.print("[bold]Next Steps:[/bold]")
        console.print("  • Open the Admin UI to configure LLM providers")
        console.print("  • Select a repository in the Workspace tab")
        console.print("  • Start chatting with your AI coding assistant")

    console.print()
    console.print("[dim]Press Ctrl+C to stop the server[/dim]")
    console.print()


def _run_server(host: str, port: int, reload: bool = False):
    """Run the FastAPI server."""
    uvicorn.run(
        "gitpilot.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@cli.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser"),
):
    """Start the GitPilot server with web UI."""
    # Check if port is already in use (prevent double-start)
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            console.print(
                f"[yellow]⚠[/yellow]  Port {port} is already in use. "
                f"GitPilot may already be running."
            )
            console.print(
                f"[dim]Run 'make stop' or kill the process on port {port} first.[/dim]"
            )
            sys.exit(1)

    # Display startup banner
    _display_startup_banner(host, port)

    # Start server in background thread
    thread = threading.Thread(
        target=_run_server,
        kwargs={"host": host, "port": port, "reload": reload},
        daemon=False,
    )
    thread.start()

    # Open browser after a short delay
    if open_browser:
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://{host}:{port}")
            console.print(f"[green]✓[/green] Browser opened at http://{host}:{port}")
        except Exception:
            console.print(f"[yellow]![/yellow] Please open http://{host}:{port} in your browser")

    # Wait for server thread
    try:
        thread.join()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down GitPilot...[/yellow]")
        sys.exit(0)


@cli.command()
def config():
    """Show current configuration."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]GitPilot Configuration[/bold cyan]",
        border_style="cyan"
    ))

    settings = get_settings()

    # Configuration details
    table = Table(title="Settings", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_column("Source", style="dim")

    # Provider
    env_provider = os.getenv("GITPILOT_PROVIDER")
    provider_source = "Environment" if env_provider else "Settings file"
    table.add_row("Active Provider", settings.provider.value, provider_source)

    # GitHub token
    github_token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    github_status = "Configured" if github_token else "Not set"
    github_source = "Environment" if github_token else "N/A"
    table.add_row("GitHub Token", github_status, github_source)

    # Provider-specific config
    if settings.provider == LLMProvider.openai:
        api_key = settings.openai.api_key or os.getenv("OPENAI_API_KEY")
        key_status = "Configured" if api_key else "Not set"
        key_source = "Environment" if os.getenv("OPENAI_API_KEY") else ("Settings" if settings.openai.api_key else "N/A")
        table.add_row("OpenAI API Key", key_status, key_source)
        table.add_row("OpenAI Model", settings.openai.model or "gpt-4o-mini", "Settings")

    elif settings.provider == LLMProvider.claude:
        api_key = settings.claude.api_key or os.getenv("ANTHROPIC_API_KEY")
        key_status = "Configured" if api_key else "Not set"
        key_source = "Environment" if os.getenv("ANTHROPIC_API_KEY") else ("Settings" if settings.claude.api_key else "N/A")
        table.add_row("Claude API Key", key_status, key_source)
        table.add_row("Claude Model", settings.claude.model, "Settings")

    elif settings.provider == LLMProvider.watsonx:
        api_key = settings.watsonx.api_key or os.getenv("WATSONX_API_KEY")
        key_status = "Configured" if api_key else "Not set"
        key_source = "Environment" if os.getenv("WATSONX_API_KEY") else ("Settings" if settings.watsonx.api_key else "N/A")
        table.add_row("Watsonx API Key", key_status, key_source)
        table.add_row("Watsonx Model", settings.watsonx.model_id, "Settings")

    elif settings.provider == LLMProvider.ollama:
        table.add_row("Ollama URL", settings.ollama.base_url, "Settings")
        table.add_row("Ollama Model", settings.ollama.model, "Settings")

    console.print(table)
    console.print()
    console.print(f"[dim]Settings file: ~/.gitpilot/settings.json[/dim]")
    console.print()


@cli.command()
def version():
    """Show GitPilot version."""
    console.print(f"GitPilot [cyan]v{__version__}[/cyan]")


def main():
    """Main entry point - run server by default."""
    if len(sys.argv) == 1:
        # No arguments, run server with defaults
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", 8000)) == 0:
                console.print(
                    "[yellow]⚠[/yellow]  Port 8000 is already in use. "
                    "GitPilot may already be running."
                )
                sys.exit(1)
        _display_startup_banner("127.0.0.1", 8000)
        try:
            _run_server("127.0.0.1", 8000, reload=False)
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down GitPilot...[/yellow]")
            sys.exit(0)
    else:
        # Run CLI commands
        cli()


def serve_only():
    """Entry point for gitpilot-api command."""
    console.print("[cyan]GitPilot API Server[/cyan]")
    console.print("[dim]Starting on http://127.0.0.1:8000[/dim]\n")
    try:
        _run_server("127.0.0.1", 8000, reload=False)
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
        sys.exit(0)
@cli.command()
def run(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository as owner/repo"),
    message: str = typer.Option("", "--message", "-m", help="User request message"),
    branch: str = typer.Option(None, "--branch", "-b", help="Target branch"),
    auto_pr: bool = typer.Option(False, "--auto-pr", help="Create PR after execution"),
    from_pr: int = typer.Option(None, "--from-pr", help="Fetch context from PR number"),
    headless: bool = typer.Option(False, "--headless", help="Non-interactive JSON output"),
):
    """Run GitPilot non-interactively (headless mode for CI/CD)."""
    import asyncio
    import sys

    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()

    if not message:
        console.print("[red]Error:[/red] --message is required (or pipe via stdin)")
        raise typer.Exit(code=1)

    token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        console.print("[red]Error:[/red] GITPILOT_GITHUB_TOKEN or GITHUB_TOKEN must be set")
        raise typer.Exit(code=1)

    from .headless import run_headless

    result = asyncio.run(run_headless(
        repo_full_name=repo,
        message=message,
        token=token,
        branch=branch,
        auto_pr=auto_pr,
        from_pr=from_pr,
    ))

    if headless:
        # Pure JSON for CI/CD consumption
        console.print(result.to_json())
    else:
        if result.success:
            console.print(f"[green]Success:[/green] {result.output[:500]}")
        else:
            console.print(f"[red]Failed:[/red] {result.error}")
        if result.pr_url:
            console.print(f"[cyan]PR:[/cyan] {result.pr_url}")

    raise typer.Exit(code=0 if result.success else 1)


@cli.command("init")
def init_project(
    path: str = typer.Argument(".", help="Project directory to initialise"),
):
    """Initialize .gitpilot/ directory with template GITPILOT.md."""
    from pathlib import Path as StdPath
    from .memory import MemoryManager

    workspace = StdPath(path).resolve()
    mgr = MemoryManager(workspace)
    md_path = mgr.init_project()
    console.print(f"[green]Initialized:[/green] {md_path}")
    console.print("[dim]Edit .gitpilot/GITPILOT.md to add your project conventions.[/dim]")


@cli.command("plugin")
def plugin_cmd(
    action: str = typer.Argument(..., help="install | uninstall | list"),
    source: str = typer.Argument(None, help="Git URL, local path, or plugin name"),
):
    """Manage GitPilot plugins."""
    from .plugins import PluginManager

    mgr = PluginManager()

    if action == "list":
        plugins = mgr.list_installed()
        if not plugins:
            console.print("[dim]No plugins installed.[/dim]")
            return
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Description")
        for p in plugins:
            table.add_row(p.name, p.version, p.description)
        console.print(table)

    elif action == "install":
        if not source:
            console.print("[red]Error:[/red] source is required for install")
            raise typer.Exit(code=1)
        try:
            info = mgr.install(source)
            console.print(f"[green]Installed:[/green] {info.name} v{info.version}")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1)

    elif action == "uninstall":
        if not source:
            console.print("[red]Error:[/red] plugin name is required")
            raise typer.Exit(code=1)
        if mgr.uninstall(source):
            console.print(f"[green]Uninstalled:[/green] {source}")
        else:
            console.print(f"[yellow]Not found:[/yellow] {source}")

    else:
        console.print(f"[red]Unknown action:[/red] {action}. Use: install, uninstall, list")
        raise typer.Exit(code=1)


@cli.command("skill")
def skill_cmd(
    name: str = typer.Argument(None, help="Skill name to invoke (or 'list')"),
):
    """List or invoke skills."""
    from .skills import SkillManager

    mgr = SkillManager(workspace_path=Path.cwd())
    mgr.load_all()

    if not name or name == "list":
        skills = mgr.list_skills()
        if not skills:
            console.print("[dim]No skills found.[/dim]")
            console.print("[dim]Create .gitpilot/skills/*.md to add skills.[/dim]")
            return
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Name")
        table.add_column("Description")
        table.add_column("Auto")
        for s in skills:
            table.add_row(s["name"], s["description"], str(s.get("auto_trigger", False)))
        console.print(table)
    else:
        prompt = mgr.invoke(name)
        if prompt is None:
            console.print(f"[red]Skill not found:[/red] {name}")
            raise typer.Exit(code=1)
        console.print(f"[cyan]/{name}[/cyan]")
        console.print(prompt)


@cli.command("scan")
def scan_cmd(
    path: str = typer.Argument(".", help="Directory or file to scan"),
    min_confidence: float = typer.Option(0.5, "--min-confidence", help="Minimum confidence threshold"),
):
    """Run AI-powered security scan on a directory or file."""
    from .security import SecurityScanner

    scanner = SecurityScanner(min_confidence=min_confidence)
    target = Path(path).resolve()

    if target.is_file():
        findings = scanner.scan_file(str(target))
        if not findings:
            console.print("[green]No security issues found.[/green]")
            return
        table = Table(show_header=True, header_style="bold red")
        table.add_column("Severity")
        table.add_column("Rule")
        table.add_column("Title")
        table.add_column("Line")
        table.add_column("File")
        for f in findings:
            table.add_row(f.severity.value, f.rule_id, f.title, str(f.line_number), f.file_path)
        console.print(table)
    else:
        result = scanner.scan_directory(str(target))
        console.print(f"[cyan]Scanned:[/cyan] {result.files_scanned} files in {result.scan_duration_ms:.0f}ms")
        if not result.findings:
            console.print("[green]No security issues found.[/green]")
            return
        console.print(f"[yellow]Found {len(result.findings)} issues:[/yellow]")
        for sev, count in sorted(result.summary.items()):
            color = "red" if sev in ("critical", "high") else "yellow" if sev == "medium" else "dim"
            console.print(f"  [{color}]{sev}: {count}[/{color}]")
        console.print()
        table = Table(show_header=True, header_style="bold red")
        table.add_column("Severity")
        table.add_column("Rule")
        table.add_column("Title")
        table.add_column("Line")
        table.add_column("File")
        for f in result.findings[:50]:
            table.add_row(f.severity.value, f.rule_id, f.title, str(f.line_number), f.file_path)
        console.print(table)
        if len(result.findings) > 50:
            console.print(f"[dim]... and {len(result.findings) - 50} more[/dim]")


@cli.command("predict")
def predict_cmd(
    context: str = typer.Argument(..., help="Context string to get predictions for"),
):
    """Get proactive suggestions based on context."""
    from .predictions import PredictiveEngine

    engine = PredictiveEngine()
    suggestions = engine.predict(context)

    if not suggestions:
        console.print("[dim]No suggestions for this context.[/dim]")
        return

    for s in suggestions:
        score_color = "green" if s.relevance_score >= 0.8 else "yellow" if s.relevance_score >= 0.6 else "dim"
        console.print(f"  [{score_color}][{s.relevance_score:.0%}][/{score_color}] [bold]{s.title}[/bold]")
        console.print(f"        {s.description}")
        console.print(f"        [cyan]Prompt:[/cyan] {s.prompt}")
        console.print()


@cli.command("list-models")
def list_models_cmd(
    provider: str = typer.Option(
        None,
        "--provider",
        "-p",
        help="LLM provider (openai, claude, watsonx, ollama). Defaults to active provider.",
    )
):
    """List LLM models available for the configured provider."""
    settings = get_settings()

    if provider is None:
        target = settings.provider
    else:
        # Normalize to enum
        try:
            target = LLMProvider(provider)
        except ValueError:
            console.print(f"[red]Unknown provider:[/red] {provider}")
            raise typer.Exit(code=1)

    models, error = list_models_for_provider(target, settings)

    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]Models for provider[/bold cyan] [white]{target.value}[/white]",
            border_style="cyan",
        )
    )

    if error:
        console.print(f"[yellow]Warning:[/yellow] {error}")

    if not models:
        console.print("No models found.")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Model ID", style="white")

    for i, m in enumerate(models, start=1):
        table.add_row(str(i), m)

    console.print(table)
    console.print()


@cli.command("generate")
def generate_cmd(
    message: str = typer.Option(..., "--message", "-m", help="What to generate (e.g. 'Flask hello world app')"),
    output_dir: str = typer.Option(".", "--output", "-o", help="Directory to write generated files into"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be generated without writing files"),
):
    """Generate code locally using the configured LLM.

    Creates files on disk from a natural-language prompt. No GitHub
    required — works with any LLM provider (Ollama, OpenAI, Claude).

    Examples::

        gitpilot generate -m "Create a Flask hello world app"
        gitpilot generate -m "Python CLI with click" -o my-project
        gitpilot generate -m "React component for a todo list" --dry-run
    """
    import re

    settings = get_settings()
    provider_name = settings.provider.value
    model_name = "default"
    provider_settings = getattr(settings, provider_name, None)
    if provider_settings:
        model_name = getattr(provider_settings, "model", None) or "default"

    console.print(f"[dim]Provider:[/dim] {provider_name} · [dim]Model:[/dim] {model_name}")
    console.print(f"[dim]Output:[/dim] {os.path.abspath(output_dir)}")
    console.print()

    prompt = (
        "You are GitPilot, a code generation assistant.\n"
        "Generate the requested project files. For EACH file, output it in this EXACT format:\n"
        "\n"
        "```language filepath\n"
        "...complete file content...\n"
        "```\n"
        "\n"
        "Example:\n"
        "```python app.py\n"
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "```\n"
        "\n"
        "Rules:\n"
        "- Opening fence: triple backticks + language + space + relative filepath\n"
        "- Output COMPLETE file content, not snippets\n"
        "- Generate ALL files needed for a working project\n"
        "- Include a README.md if appropriate\n"
        "\n"
        f"User request: {message}\n"
    )

    console.print("[bold]Generating...[/bold]", end="")

    try:
        from .llm_provider import build_llm
        llm = build_llm()
        answer = llm.call([{"role": "user", "content": prompt}])
    except Exception as e:
        console.print(f"\n[red]LLM error:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(" [green]done[/green]\n")

    # Extract structured edits using the same extractor as the API
    from .api import _extract_edits_from_answer

    edits = _extract_edits_from_answer(answer)

    if not edits:
        console.print("[yellow]No files extracted from LLM response.[/yellow]")
        console.print("[dim]Raw response (first 2000 chars):[/dim]")
        console.print(answer[:2000])
        raise typer.Exit(code=1)

    files_written = []
    for edit in edits:
        safe_path = edit["file"]
        if not safe_path:
            continue

        content = edit.get("content", "").rstrip()

        if dry_run:
            console.print(f"  [cyan]Would create:[/cyan] {safe_path} ({len(content)} bytes)")
        else:
            full_path = os.path.join(output_dir, safe_path)
            os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            files_written.append(safe_path)
            console.print(f"  [green]Created:[/green] {safe_path} ({len(content)} bytes)")

    console.print()
    if dry_run:
        console.print(f"[dim]Dry run: {len(edits)} file(s) would be created.[/dim]")
    else:
        console.print(f"[green]Generated {len(files_written)} file(s) in {os.path.abspath(output_dir)}[/green]")

