"""sentinels — interactive terminal UI for the Sentinel framework."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from typing import Optional

import requests
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

DASHBOARD_URL = os.environ.get("SENTINEL_DASHBOARD_URL", "http://localhost:8501").rstrip("/")
API_KEY       = os.environ.get("SENTINEL_API_KEY", "")

_SEV_COLORS = {"P1": "red", "P2": "yellow", "P3": "cyan", "P4": "green"}
_STATUS_COLORS = {"OPEN": "red", "ACKNOWLEDGED": "yellow", "RESOLVED": "green"}


def _api(path: str, method: str = "GET", json: dict | None = None, timeout: int = 6) -> dict | list:
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    resp = requests.request(method, f"{DASHBOARD_URL}{path}", headers=headers, json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _sev_markup(sev: str) -> str:
    c = _SEV_COLORS.get(sev, "white")
    return f"[bold {c}]{sev}[/]"


def _status_markup(st: str) -> str:
    c = _STATUS_COLORS.get(st, "white")
    return f"[{c}]{st}[/]"


def _rel_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(dt.tzinfo) - dt
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        return f"{s // 3600}h ago"
    except Exception:
        return iso[:16] if iso else "—"


# ── Incident Detail Modal ────────────────────────────────────────────────────

class DetailModal(ModalScreen):
    CSS = """
    DetailModal {
        align: center middle;
    }
    #detail-box {
        width: 90;
        max-height: 38;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    #detail-scroll {
        height: 28;
    }
    #detail-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    .detail-section-header {
        text-style: bold underline;
        margin-top: 1;
    }
    .detail-close {
        margin-top: 1;
        width: 100%;
        align-horizontal: right;
    }
    """

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("q", "dismiss", "Close")]

    def __init__(self, incident: dict) -> None:
        super().__init__()
        self._inc = incident

    def compose(self) -> ComposeResult:
        inc = self._inc
        sev = inc.get("severity", "P3")
        svc = inc.get("service", "?")
        col = _SEV_COLORS.get(sev, "white")
        title = inc.get("title", svc)

        lines: list[str] = []

        # Root cause
        rc = inc.get("root_cause", "")
        if rc and "AI analysis running" not in rc:
            lines.append("[bold underline]Root Cause[/]")
            lines.append(rc)
            lines.append("")

        # Summary
        sm = inc.get("summary", "")
        if sm:
            lines.append("[bold underline]Business Impact[/]")
            lines.append(sm)
            lines.append("")

        # Contributing factors
        la = inc.get("log_analysis", {})
        factors = inc.get("contributing_factors", la.get("contributing_factors", []))
        if factors:
            lines.append("[bold underline]Contributing Factors[/]")
            for f in factors:
                lines.append(f"  • {f}")
            lines.append("")

        # Runbook
        rb = inc.get("runbook", {})
        steps = [rb.get(f"step{i}", "") for i in range(1, 6) if rb.get(f"step{i}")]
        if steps:
            lines.append("[bold underline green]Runbook[/]")
            for i, s in enumerate(steps, 1):
                lines.append(f"  [green]{i}.[/] {s}")
            lines.append("")

        # Code context
        cc = inc.get("code_context")
        if cc and cc.get("files"):
            f0 = cc["files"][0]
            lines.append("[bold underline]Code Context[/]")
            lines.append(f"  [cyan]{f0['path']}[/] (line {int(f0.get('start_line', 0))})")
            fix = f0.get("fix", "")
            if fix:
                lines.append(f"  [dim]Fix:[/] {fix[:80]}")
            lines.append("")

        # Footer metadata
        conf = la.get("confidence", inc.get("confidence", "?")).upper()
        trend = la.get("degradation_trend", "?")
        comps = la.get("affected_components", [])
        rca_src = inc.get("rca_source", "none")
        created = _rel_time(inc.get("created_at", ""))

        meta_parts = []
        if conf != "?":
            conf_col = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(conf, "white")
            meta_parts.append(f"Confidence [{conf_col}]{conf}[/]")
        if trend != "?":
            meta_parts.append(f"Trend [bold]{trend}[/]")
        if comps:
            meta_parts.append(f"Components {', '.join(comps[:3])}")
        meta_parts.append(f"AI [dim]{rca_src}[/]")
        meta_parts.append(f"Created {created}")
        lines.append("  ".join(meta_parts))

        content = "\n".join(lines)

        with Container(id="detail-box"):
            yield Label(f"[bold {col}]{sev}[/]  {title}", id="detail-title")
            with ScrollableContainer(id="detail-scroll"):
                yield Static(content)
            with Horizontal(classes="detail-close"):
                yield Button("Close  [Esc]", variant="default", id="close-btn")

    @on(Button.Pressed, "#close-btn")
    def _close(self) -> None:
        self.dismiss()


# ── Fire Incident Modal ──────────────────────────────────────────────────────

class FireModal(ModalScreen):
    CSS = """
    FireModal { align: center middle; }
    #fire-box {
        width: 60;
        height: auto;
        background: $surface;
        border: solid $warning;
        padding: 1 2;
    }
    #fire-title { text-style: bold; color: $warning; margin-bottom: 1; }
    .fire-label { margin-top: 1; }
    #fire-buttons { margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="fire-box"):
            yield Label("Fire Incident", id="fire-title")
            yield Label("Service name:", classes="fire-label")
            yield Input(placeholder="auth-service", id="fire-service")
            yield Label("Severity:", classes="fire-label")
            yield Select(
                [(s, s) for s in ("P1", "P2", "P3", "P4")],
                value="P2",
                id="fire-sev",
            )
            with Horizontal(id="fire-buttons"):
                yield Button("Fire", variant="warning", id="fire-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    @on(Button.Pressed, "#cancel-btn")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#fire-btn")
    def _fire(self) -> None:
        svc = self.query_one("#fire-service", Input).value.strip()
        sev = self.query_one("#fire-sev", Select).value
        if not svc:
            self.query_one("#fire-service", Input).focus()
            return
        self.dismiss({"service": svc, "severity": str(sev)})


# ── Diagnose Modal ───────────────────────────────────────────────────────────

class DiagnoseModal(ModalScreen):
    CSS = """
    DiagnoseModal { align: center middle; }
    #diag-box {
        width: 64;
        height: auto;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    #diag-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .diag-label { margin-top: 1; }
    #diag-buttons { margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="diag-box"):
            yield Label("Run Diagnose", id="diag-title")
            yield Label("Service name:", classes="diag-label")
            yield Input(placeholder="auth-service", id="diag-service")
            yield Label("Severity:", classes="diag-label")
            yield Select(
                [(s, s) for s in ("P1", "P2", "P3", "P4")],
                value="P2",
                id="diag-sev",
            )
            yield Label("Log source (leave blank for auto):", classes="diag-label")
            yield Select(
                [("Auto-detect", ""), ("Kubernetes", "k8s"), ("AWS CloudWatch", "aws"),
                 ("Loki", "loki"), ("Stdin", "-")],
                value="",
                id="diag-src",
            )
            with Horizontal(id="diag-buttons"):
                yield Button("Run in terminal", variant="primary", id="diag-btn")
                yield Button("Cancel", variant="default", id="diag-cancel")

    @on(Button.Pressed, "#diag-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#diag-btn")
    def _run(self) -> None:
        svc = self.query_one("#diag-service", Input).value.strip()
        sev = self.query_one("#diag-sev", Select).value
        src = self.query_one("#diag-src", Select).value
        if not svc:
            self.query_one("#diag-service", Input).focus()
            return
        self.dismiss({"service": svc, "severity": str(sev), "source": str(src)})


# ── Main App ─────────────────────────────────────────────────────────────────

CSS = """
Screen {
    layout: vertical;
}

#banner {
    height: 3;
    padding: 0 2;
    background: $primary-darken-2;
    color: $text;
    content-align: left middle;
    text-style: bold;
}

#status-bar {
    height: 1;
    padding: 0 2;
    background: $primary-darken-3;
    color: $text-muted;
    content-align: left middle;
}

#main-area {
    height: 1fr;
    layout: horizontal;
}

#left-panel {
    width: 50%;
    border-right: solid $primary;
    padding: 0;
}

#right-panel {
    width: 50%;
    padding: 1 2;
}

#incidents-label {
    height: 2;
    padding: 0 1;
    background: $primary-darken-1;
    text-style: bold;
    content-align: left middle;
}

#incidents-table {
    height: 1fr;
}

#detail-label {
    height: 2;
    background: $primary-darken-1;
    text-style: bold;
    content-align: left middle;
    margin-bottom: 1;
}

#detail-content {
    height: 1fr;
    overflow-y: auto;
}

#stats-bar {
    height: 2;
    padding: 0 2;
    background: $surface;
    color: $text-muted;
    content-align: left middle;
}
"""


class SentinelTUI(App):
    """Interactive Sentinel terminal UI."""

    TITLE = "Sentinel"
    CSS = CSS

    BINDINGS = [
        Binding("d", "diagnose",  "Diagnose"),
        Binding("f", "fire",      "Fire incident"),
        Binding("r", "resolve",   "Resolve"),
        Binding("a", "ack",       "Acknowledge"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("enter", "detail",    "View detail"),
        Binding("q", "quit_app", "Quit"),
    ]

    _incidents: reactive[list] = reactive([], recompose=False)
    _selected_id: reactive[str] = reactive("")
    _connected: reactive[bool] = reactive(False)
    _stats: reactive[dict] = reactive({})

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="banner")
        yield Static("", id="status-bar")
        with Horizontal(id="main-area"):
            with Vertical(id="left-panel"):
                yield Static(" Open Incidents", id="incidents-label")
                yield DataTable(id="incidents-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="right-panel"):
                yield Static(" Incident Detail", id="detail-label")
                with ScrollableContainer(id="detail-content"):
                    yield Static(_PLACEHOLDER, id="detail-body")
        yield Static("", id="stats-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._update_banner()
        self.load_incidents()
        self.set_interval(15, self.load_incidents)

    def _setup_table(self) -> None:
        table = self.query_one("#incidents-table", DataTable)
        table.add_columns("Sev", "Service", "Status", "Title", "Age")

    def _update_banner(self) -> None:
        dot = "[green]●[/]" if self._connected else "[red]●[/]"
        self.query_one("#banner", Static).update(
            f"  SENTINEL  {dot}  {DASHBOARD_URL}"
        )

    @work(thread=True)
    def load_incidents(self) -> None:
        try:
            data = _api("/api/incidents")
            incidents = data if isinstance(data, list) else data.get("incidents", [])
            stats = _api("/api/stats")
            self._connected = True
            self.call_from_thread(self._apply_incidents, incidents, stats)
        except Exception as exc:
            self._connected = False
            self.call_from_thread(self._show_offline, str(exc))

    def _apply_incidents(self, incidents: list, stats: dict) -> None:
        self._incidents = incidents
        self._stats = stats
        self._update_banner()
        self._refresh_table()
        self._refresh_stats()
        if self._selected_id:
            self._refresh_detail()

    def _show_offline(self, err: str) -> None:
        self._update_banner()
        try:
            self.query_one("#status-bar", Static).update(
                f"  [red]Dashboard offline[/]  {DASHBOARD_URL}  —  {err[:80]}"
            )
        except NoMatches:
            pass

    def _refresh_table(self) -> None:
        table = self.query_one("#incidents-table", DataTable)
        table.clear()
        open_inc = [i for i in self._incidents if i.get("status") != "RESOLVED"]
        for inc in open_inc:
            sev    = inc.get("severity", "P3")
            svc    = inc.get("service", "?")[:22]
            status = inc.get("status", "?")[:14]
            title  = (inc.get("title") or svc)[:36]
            age    = _rel_time(inc.get("created_at", ""))
            table.add_row(
                _sev_markup(sev),
                svc,
                _status_markup(status),
                title,
                age,
                key=inc.get("incident_id", ""),
            )
        try:
            self.query_one("#status-bar", Static).update(
                f"  [green]Connected[/]  ·  {len(open_inc)} open incidents  "
                f"·  auto-refresh 15s  ·  [dim]Enter[/] to expand"
            )
        except NoMatches:
            pass

    def _refresh_stats(self) -> None:
        st = self._stats
        if not st:
            return
        p1 = st.get("by_severity", {}).get("P1", 0)
        p2 = st.get("by_severity", {}).get("P2", 0)
        total = st.get("total_open", st.get("total", "?"))
        mttr  = st.get("mttr_minutes", "?")
        try:
            self.query_one("#stats-bar", Static).update(
                f"  Open: [bold]{total}[/]  ·  "
                f"P1: [bold red]{p1}[/]  P2: [bold yellow]{p2}[/]  ·  "
                f"MTTR: [bold]{mttr}[/] min"
            )
        except NoMatches:
            pass

    def _refresh_detail(self) -> None:
        if not self._selected_id:
            return
        inc = self._incident_by_id(self._selected_id)
        if inc:
            self._render_detail(inc)

    def _incident_by_id(self, iid: str) -> Optional[dict]:
        for inc in self._incidents:
            if inc.get("incident_id") == iid:
                return inc
        return None

    def _render_detail(self, inc: dict) -> None:
        sev   = inc.get("severity", "P3")
        svc   = inc.get("service", "?")
        col   = _SEV_COLORS.get(sev, "white")
        title = inc.get("title", svc)
        status = inc.get("status", "?")

        lines = [
            f"[bold {col}]{sev}[/]  {title}",
            f"[dim]Status:[/] {_status_markup(status)}  [dim]ID:[/] {inc.get('incident_id','?')}",
            "",
        ]

        rc = inc.get("root_cause", "")
        if rc and "AI analysis running" not in rc and "⏳" not in rc:
            lines += ["[bold red]Root Cause[/]", rc, ""]
        elif inc.get("ai_pending") or "⏳" in rc or "running" in rc.lower():
            lines += ["[dim yellow]⏳  AI analysis in progress…[/]", ""]

        sm = inc.get("summary", "")
        if sm:
            lines += ["[bold]Business Impact[/]", sm, ""]

        la = inc.get("log_analysis", {})
        factors = inc.get("contributing_factors", la.get("contributing_factors", []))
        if factors:
            lines.append("[bold]Contributing Factors[/]")
            for f in factors:
                lines.append(f"  • {f}")
            lines.append("")

        rb = inc.get("runbook", {})
        steps = [rb.get(f"step{i}", "") for i in range(1, 6) if rb.get(f"step{i}")]
        if steps:
            lines.append("[bold green]Runbook[/]")
            for i, s in enumerate(steps, 1):
                lines.append(f"  [green]{i}.[/] {s}")
            lines.append("")

        cc = inc.get("code_context")
        if cc and cc.get("files"):
            f0 = cc["files"][0]
            lines.append("[bold cyan]Code Context[/]")
            lines.append(f"  [cyan]{f0['path']}[/] : {int(f0.get('start_line', 0))}")
            fix = f0.get("fix", "")
            if fix:
                lines.append(f"  [dim]Fix → [/]{fix[:70]}")
            lines.append("")

        conf  = la.get("confidence", "").upper()
        trend = la.get("degradation_trend", "")
        rca_src = inc.get("rca_source", "none")
        if conf or trend:
            meta = []
            if conf:
                cc2 = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(conf, "white")
                meta.append(f"Confidence [{cc2}]{conf}[/]")
            if trend:
                meta.append(f"Trend [bold]{trend}[/]")
            meta.append(f"[dim]{rca_src}[/]")
            lines.append("  ·  ".join(meta))

        try:
            self.query_one("#detail-body", Static).update("\n".join(lines))
            self.query_one("#detail-label", Static).update(" Incident Detail")
        except NoMatches:
            pass

    # ── actions ──────────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self.load_incidents()

    def action_detail(self) -> None:
        table = self.query_one("#incidents-table", DataTable)
        row_key = table.cursor_row
        if row_key is None:
            return
        row = table.get_row_at(row_key)
        if not row:
            return
        # Find incident by matching severity+service in table row
        try:
            iid = list(table.rows.keys())[row_key].value
        except Exception:
            return
        inc = self._incident_by_id(iid)
        if inc:
            self.push_screen(DetailModal(inc))

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        iid = event.row_key.value if event.row_key else None
        if iid:
            self._selected_id = iid
            inc = self._incident_by_id(iid)
            if inc:
                self._render_detail(inc)

    def action_fire(self) -> None:
        def _after(result: dict | None) -> None:
            if not result:
                return
            self._do_fire(result["service"], result["severity"])
        self.push_screen(FireModal(), _after)

    @work(thread=True)
    def _do_fire(self, service: str, severity: str) -> None:
        try:
            resp = _api("/api/demo/fire", method="POST",
                        json={"service": service, "severity": severity})
            iid = resp.get("incident_id", "?")
            self.call_from_thread(self._notify, f"Incident fired: {iid}")
        except Exception as exc:
            self.call_from_thread(self._notify, f"[red]Error:[/] {exc}")
        self.call_from_thread(self.load_incidents)

    def action_resolve(self) -> None:
        table = self.query_one("#incidents-table", DataTable)
        row_key = table.cursor_row
        if row_key is None:
            return
        try:
            iid = list(table.rows.keys())[row_key].value
        except Exception:
            return
        self._do_resolve(iid)

    @work(thread=True)
    def _do_resolve(self, iid: str) -> None:
        try:
            _api(f"/api/incidents/{iid}/resolve", method="POST")
            self.call_from_thread(self._notify, f"Resolved {iid[:16]}…")
        except Exception as exc:
            self.call_from_thread(self._notify, f"[red]Error:[/] {exc}")
        self.call_from_thread(self.load_incidents)

    def action_ack(self) -> None:
        table = self.query_one("#incidents-table", DataTable)
        row_key = table.cursor_row
        if row_key is None:
            return
        try:
            iid = list(table.rows.keys())[row_key].value
        except Exception:
            return
        self._do_ack(iid)

    @work(thread=True)
    def _do_ack(self, iid: str) -> None:
        try:
            _api(f"/api/incidents/{iid}/acknowledge", method="POST",
                 json={"assigned_to": os.environ.get("USER", "on-call")})
            self.call_from_thread(self._notify, f"Acknowledged {iid[:16]}…")
        except Exception as exc:
            self.call_from_thread(self._notify, f"[red]Error:[/] {exc}")
        self.call_from_thread(self.load_incidents)

    def action_diagnose(self) -> None:
        def _after(result: dict | None) -> None:
            if not result:
                return
            self._launch_diagnose(result["service"], result["severity"], result["source"])
        self.push_screen(DiagnoseModal(), _after)

    def _launch_diagnose(self, service: str, severity: str, source: str) -> None:
        cmd = [sys.executable, "-m", "sentinel.cli", "diagnose", service,
               "--severity", severity]
        if source:
            cmd += ["--from", source]
        # Suspend TUI, run in foreground, resume
        with self.suspend():
            subprocess.run(cmd, env={**os.environ, "PYTHONPATH": "."})
            input("\n  Press Enter to return to Sentinel…")

    def action_quit_app(self) -> None:
        self.exit()

    def _notify(self, msg: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(f"  {msg}")
        except NoMatches:
            pass


_PLACEHOLDER = (
    "[dim]Select an incident from the list on the left.\n\n"
    "  ↑ ↓   Navigate incidents\n"
    "  Enter  View full detail\n"
    "  D      Run diagnose on a service\n"
    "  F      Fire a new demo incident\n"
    "  R      Resolve selected\n"
    "  A      Acknowledge selected\n"
    "  Ctrl+R Refresh now\n"
    "  Q      Quit[/]"
)


def run() -> None:
    """Entry point for the `sentinels` command."""
    app = SentinelTUI()
    app.run()


if __name__ == "__main__":
    run()
