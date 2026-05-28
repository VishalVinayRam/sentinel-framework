"""sentinel diagnose — run AI root cause analysis on any service from the CLI."""

import json
import os
import sys
from typing import Optional

import click


# ── command ────────────────────────────────────────────────────────────────

@click.command("diagnose")
@click.argument("service")
@click.option("--severity", "-s", default="P2",
              type=click.Choice(["P1", "P2", "P3", "P4"], case_sensitive=False),
              show_default=True, help="Incident severity")
@click.option("--from", "log_from", default=None,
              type=click.Choice(["k8s", "aws", "loki", "file", "-"], case_sensitive=False),
              help="Log source (default: auto-detect)")
# Kubernetes
@click.option("--namespace", "-n", default="default", show_default=True,
              help="Kubernetes namespace")
@click.option("--container", default=None,
              help="Container name (multi-container pods)")
@click.option("--selector", default=None,
              help="Pod label selector — overrides default app=<service>")
@click.option("--kubeconfig", default=None,
              help="Path to kubeconfig file")
# AWS CloudWatch
@click.option("--log-group", default="/ecs/", show_default=True,
              help="CloudWatch log group prefix")
@click.option("--region", default=None,
              help="AWS region (default: AWS_DEFAULT_REGION or us-east-1)")
@click.option("--aws-endpoint", default=None,
              help="Override AWS endpoint URL (LocalStack, Floci, etc.)")
# Loki
@click.option("--loki-url", default=None,
              help="Loki base URL (default: $LOKI_URL or http://localhost:3100)")
@click.option("--loki-tenant", default=None,
              help="Loki tenant / org ID (default: $LOKI_TENANT_ID)")
@click.option("--loki-label", default="service", show_default=True,
              help="Loki stream label used to select logs")
# File
@click.option("--log-file", "log_file", default=None,
              type=click.Path(), help="Path to a local log file")
# Code context
@click.option("--repo-path", default=None,
              type=click.Path(exists=True, file_okay=False),
              help="Repo root for code context scan (default: current directory)")
@click.option("--no-code", is_flag=True,
              help="Skip source code scan")
# LLM
@click.option("--llm", default=None,
              type=click.Choice(["ollama", "openai", "anthropic", "gemini"], case_sensitive=False),
              help="Force a specific LLM provider (default: first available)")
@click.option("--model", default=None,
              help="Model name override (e.g. gpt-4o, claude-opus-4-7)")
# Time window
@click.option("--since", default=30, type=int, show_default=True,
              help="Log look-back window in minutes")
# Output
@click.option("--output", "-o", default="terminal",
              type=click.Choice(["terminal", "json", "markdown"]),
              show_default=True, help="Output format")
@click.option("--save", default=None,
              help="Save RCA output to a file")
@click.option("--verbose", "-v", is_flag=True,
              help="Show LLM provider selection and fetch details")
def diagnose_command(
    service, severity, log_from,
    namespace, container, selector, kubeconfig,
    log_group, region, aws_endpoint,
    loki_url, loki_tenant, loki_label,
    log_file, repo_path, no_code,
    llm, model, since, output, save, verbose,
):
    """Run AI root cause analysis on SERVICE.

    \b
    Auto-detects log source (kubectl → Loki → CloudWatch) unless --from is given.

    \b
    Examples:
      # Kubernetes — auto-detect pods by service name
      sentinel diagnose auth-service --severity P1

      # Kubernetes — specific namespace
      sentinel diagnose payments -n production --since 60

      # AWS CloudWatch
      sentinel diagnose worker-service --from aws --log-group /ecs/ --region us-east-1

      # Grafana Loki
      sentinel diagnose api-gateway --from loki --loki-url http://loki.monitoring:3100

      # Pipe logs from anywhere
      kubectl logs -l app=myapp --since=30m | sentinel diagnose myapp --from -
      tail -n 500 /var/log/app.log     | sentinel diagnose myapp --from -

      # Local log file
      sentinel diagnose search-api --log-file /tmp/search.log

      # Force Anthropic, save markdown report
      sentinel diagnose auth-service --llm anthropic --output markdown --save rca.md

      # No log source — RCA based on service name + severity only (useful for drafts)
      sentinel diagnose unknown-svc --no-code
    """
    severity = severity.upper()

    # Model override must happen before build_providers() reads env
    if model:
        for env_key in ("KSERVE_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL", "GEMINI_MODEL"):
            os.environ[env_key] = model

    _status = _printer(output, verbose)

    # ── 1. Log source ──────────────────────────────────────────────────────
    source = _resolve_source(
        log_from=log_from, service=service,
        namespace=namespace, container=container,
        selector=selector, kubeconfig=kubeconfig,
        log_group=log_group,
        region=region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_endpoint=aws_endpoint,
        loki_url=loki_url or os.environ.get("LOKI_URL", "http://localhost:3100"),
        loki_tenant=loki_tenant or os.environ.get("LOKI_TENANT_ID", ""),
        loki_label=loki_label,
        log_file=log_file,
    )

    log_context  = ""
    log_src_name = "none"

    if source:
        log_src_name = source.name
        _status(f"Fetching logs via {source.name}…")
        try:
            log_context = source.fetch(service, minutes=since)
        except Exception as exc:
            if verbose:
                _status(f"[warn] log fetch failed: {exc}")
        n = log_context.count("\n") if log_context else 0
        _status(f"{n} log lines fetched.")
    else:
        _status("No log source detected — RCA based on service name + severity only.")

    # ── 2. Code context ────────────────────────────────────────────────────
    code_context = None
    if not no_code:
        _status("Scanning source code…")
        try:
            from sentinel.diagnose.code_scanner import scan as scan_code
            code_context = scan_code(service, repo_root=repo_path)
            if code_context:
                _status(f"Found {len(code_context.get('files', []))} relevant source file(s).")
            else:
                _status("No matching source files found — skipping code context.")
        except Exception as exc:
            if verbose:
                _status(f"[warn] code scan failed: {exc}")

    # ── 3. LLM providers ───────────────────────────────────────────────────
    from sentinel.diagnose.engine import build_providers
    providers = build_providers(only=llm, verbose=verbose)

    if not providers:
        click.echo(
            "\n  No LLM provider available.\n"
            "  Set one of: ANTHROPIC_API_KEY  OPENAI_API_KEY  GEMINI_API_KEY\n"
            "  or start Ollama and ensure KSERVE_ENDPOINT=http://localhost:8081\n",
            err=True,
        )
        raise SystemExit(1)

    # ── 4. Run RCA ─────────────────────────────────────────────────────────
    _status("Running RCA…")
    from sentinel.diagnose.engine import run_rca
    rca, provider_label = run_rca(
        service=service,
        severity=severity,
        log_context=log_context,
        code_context=code_context,
        providers=providers,
    )

    if not rca:
        click.echo(
            "\n  RCA failed — all LLM providers returned unusable output.\n"
            "  Check API keys, model availability, or try --verbose for details.\n",
            err=True,
        )
        raise SystemExit(1)

    # ── 5. Output ──────────────────────────────────────────────────────────
    _render(rca, provider_label, service, severity, log_src_name, output, save)


# ── output renderers ────────────────────────────────────────────────────────

def _render(rca, provider_label, service, severity, log_src_name, fmt, save_path):
    if fmt == "json":
        payload = {
            **rca,
            "service": service, "severity": severity,
            "provider": provider_label, "log_source": log_src_name,
        }
        text = json.dumps(payload, indent=2)
        click.echo(text)
        if save_path:
            _save(save_path, text)
        return

    if fmt == "markdown":
        text = _as_markdown(rca, provider_label, service, severity, log_src_name)
        click.echo(text)
        if save_path:
            _save(save_path, text)
        return

    # terminal (default)
    W = 66
    click.echo()
    click.echo(click.style("  " + "═" * W, fg="cyan"))
    click.echo(click.style("    SENTINEL  —  Root Cause Analysis", fg="cyan", bold=True))
    click.echo(click.style("  " + "═" * W, fg="cyan"))
    click.echo(f"  Service    {click.style(service, bold=True)}")
    click.echo(f"  Severity   {_sev_color(severity)}")
    click.echo(f"  Log source {log_src_name}")
    click.echo(f"  AI model   {provider_label}")
    click.echo(click.style("  " + "─" * W, fg="cyan"))
    click.echo()

    click.echo(click.style("  ROOT CAUSE", bold=True, fg="red"))
    click.echo(click.style("  " + "─" * 44, fg="red"))
    _wrap_echo(rca.get("root_cause", "n/a"), indent=2)
    click.echo()

    click.echo(click.style("  BUSINESS IMPACT", bold=True))
    click.echo("  " + "─" * 44)
    _wrap_echo(rca.get("summary", "n/a"), indent=2)
    click.echo()

    factors = rca.get("contributing_factors", [])
    if factors:
        click.echo(click.style("  CONTRIBUTING FACTORS", bold=True))
        click.echo("  " + "─" * 44)
        for factor in factors:
            click.echo(f"  • {factor}")
        click.echo()

    runbook = rca.get("runbook", {})
    steps = [runbook.get(f"step{i}", "") for i in range(1, 6) if runbook.get(f"step{i}")]
    if steps:
        click.echo(click.style("  RUNBOOK", bold=True, fg="green"))
        click.echo(click.style("  " + "─" * 44, fg="green"))
        for i, step in enumerate(steps, 1):
            click.echo(f"  {i}. {step}")
        click.echo()

    components = rca.get("affected_components", [])
    trend      = rca.get("degradation_trend", "unknown")
    confidence = rca.get("confidence", "medium").upper()

    click.echo(click.style("  " + "─" * W, fg="cyan"))
    click.echo(f"  Confidence   {_conf_color(confidence)}")
    click.echo(f"  Trend        {trend}")
    if components:
        click.echo(f"  Components   {', '.join(components)}")
    click.echo(click.style("  " + "═" * W, fg="cyan"))
    click.echo()

    if save_path:
        _save(save_path, _as_markdown(rca, provider_label, service, severity, log_src_name))
        click.echo(f"  Saved → {save_path}\n")


def _as_markdown(rca, provider, service, severity, source) -> str:
    parts = [
        f"# Sentinel RCA — {service} ({severity})",
        f"",
        f"**AI model:** {provider} | **Log source:** {source}",
        f"",
        f"## Root Cause",
        f"{rca.get('root_cause', 'n/a')}",
        f"",
        f"## Business Impact",
        f"{rca.get('summary', 'n/a')}",
        f"",
    ]
    factors = rca.get("contributing_factors", [])
    if factors:
        parts += ["## Contributing Factors", ""]
        parts += [f"- {f}" for f in factors]
        parts += [""]
    runbook = rca.get("runbook", {})
    steps = [runbook.get(f"step{i}", "") for i in range(1, 6) if runbook.get(f"step{i}")]
    if steps:
        parts += ["## Runbook", ""]
        parts += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
        parts += [""]
    components = rca.get("affected_components", [])
    if components:
        parts.append(f"**Affected:** {', '.join(components)}")
    parts += [
        f"**Confidence:** {rca.get('confidence', 'medium')}",
        f"**Trend:** {rca.get('degradation_trend', 'unknown')}",
    ]
    return "\n".join(parts)


# ── source factory ──────────────────────────────────────────────────────────

def _resolve_source(
    log_from, service, namespace, container, selector, kubeconfig,
    log_group, region, aws_endpoint, loki_url, loki_tenant, loki_label, log_file,
):
    from sentinel.diagnose.log_sources import (
        KubernetesLogSource, CloudWatchLogSource, LokiLogSource,
        FileLogSource, StdinLogSource, auto_detect,
    )

    if log_from is None:
        if log_file:
            return FileLogSource(log_file)
        return auto_detect(service, namespace=namespace)

    if log_from == "-":
        return StdinLogSource()

    if log_from == "file":
        if not log_file:
            click.echo("  --log-file is required when --from file", err=True)
            raise SystemExit(1)
        return FileLogSource(log_file)

    if log_from == "k8s":
        return KubernetesLogSource(
            namespace=namespace, container=container,
            label_selector=selector, kubeconfig=kubeconfig,
        )

    if log_from == "aws":
        return CloudWatchLogSource(
            region=region, log_group_prefix=log_group, endpoint_url=aws_endpoint,
        )

    if log_from == "loki":
        return LokiLogSource(
            base_url=loki_url, tenant_id=loki_tenant, service_label=loki_label,
        )

    return None


# ── utilities ───────────────────────────────────────────────────────────────

def _printer(fmt: str, verbose: bool):
    """Return a status echo function (writes to stderr so it doesn't pollute json/md output)."""
    if fmt in ("json", "markdown") and not verbose:
        return lambda _: None
    return lambda msg: click.echo(f"  {msg}", err=True)


def _sev_color(sev: str) -> str:
    return click.style(sev, bold=True, fg={"P1": "red", "P2": "yellow", "P3": "blue"}.get(sev, "white"))


def _conf_color(conf: str) -> str:
    return click.style(conf, fg={"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(conf, "white"))


def _wrap_echo(text: str, indent: int = 2, width: int = 80) -> None:
    prefix = " " * indent
    words  = text.split()
    line   = prefix
    for word in words:
        if len(line) + len(word) + 1 > width:
            click.echo(line)
            line = prefix + word + " "
        else:
            line += word + " "
    if line.strip():
        click.echo(line)


def _save(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)
