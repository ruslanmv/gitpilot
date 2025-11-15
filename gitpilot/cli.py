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
