"""sentinels — simple terminal UI."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

import requests
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

DASHBOARD_URL = os.environ.get("SENTINEL_DASHBOARD_URL", "http://localhost:8501").rstrip("/")
API_KEY       = os.environ.get("SENTINEL_API_KEY", "")

_SEV_COLOR = {"P1": "red", "P2": "yellow", "P3": "cyan", "P4": "green"}


def _api(path: str, method: str = "GET", body: dict | None = None):
    h = {"X-API-Key": API_KEY} if API_KEY else {}
    r = requests.request(method, f"{DASHBOARD_URL}{path}", headers=h, json=body, timeout=6)
    r.raise_for_status()
    return r.json()


def _age(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        s = int((datetime.now(d.tzinfo) - d).total_seconds())
        if s < 60:   return f"{s}s"
        if s < 3600: return f"{s//60}m"
        return f"{s//3600}h"
    except Exception:
        return "?"


CSS = """
Screen { background: #1a1a1a; }

#title {
    height: 3;
    content-align: left middle;
    padding: 0 2;
    background: #1a1a1a;
    color: #e0e0e0;
    border-bottom: solid #333;
}

#incident-list {
    height: 1fr;
    border: none;
    background: #1a1a1a;
    padding: 0 1;
}

#incident-list > ListItem {
    background: #1a1a1a;
    padding: 0 1;
    border: none;
}

#incident-list > ListItem.--highlight {
    background: #2a2a2a;
}

#incident-list > ListItem > Static {
    background: transparent;
}

#detail {
    height: 12;
    padding: 1 2;
    border-top: solid #333;
    background: #1a1a1a;
    color: #c0c0c0;
    overflow-y: auto;
}

#status {
    height: 1;
    padding: 0 2;
    background: #111;
    color: #555;
    content-align: left middle;
}

Footer {
    background: #111;
    color: #555;
}
"""


class SentinelTUI(App):
    CSS = CSS

    BINDINGS = [
        Binding("j,down",    "cursor_down",   "Down",      show=False),
        Binding("k,up",      "cursor_up",     "Up",        show=False),
        Binding("r",         "refresh",       "Refresh"),
        Binding("f",         "fire",          "Fire"),
        Binding("x",         "resolve",       "Resolve"),
        Binding("d",         "diagnose",      "Diagnose"),
        Binding("q",         "quit",          "Quit"),
    ]

    _incidents: reactive[list] = reactive([])
    _selected:  reactive[int]  = reactive(0)
    _connected: reactive[bool] = reactive(False)
    _status_msg: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Static("", id="title")
        yield ListView(id="incident-list")
        yield Static("", id="detail")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._draw_title()
        self.fetch_incidents()
        self.set_interval(15, self.fetch_incidents)

    def _draw_title(self) -> None:
        connected = "[green]●[/]" if self._connected else "[red]●[/]"
        try:
            self.query_one("#title", Static).update(
                f"  [bold white]sentinels[/]   {connected}  [dim]{DASHBOARD_URL}[/]"
            )
        except NoMatches:
            pass

    @work(thread=True)
    def fetch_incidents(self) -> None:
        try:
            data = _api("/api/incidents")
            incs = data if isinstance(data, list) else data.get("incidents", [])
            self._connected = True
            self.call_from_thread(self._apply, incs)
        except Exception as e:
            self._connected = False
            self.call_from_thread(self._set_status, f"[red]offline:[/] {e}")
            self.call_from_thread(self._draw_title)

    def _apply(self, incs: list) -> None:
        self._incidents = incs
        self._draw_title()
        self._rebuild_list()
        self._draw_detail()

    def _rebuild_list(self) -> None:
        lv = self.query_one("#incident-list", ListView)
        lv.clear()
        open_incs = [i for i in self._incidents if i.get("status") != "RESOLVED"]
        for inc in open_incs:
            sev = inc.get("severity", "P3")
            svc = inc.get("service", "?")
            col = _SEV_COLOR.get(sev, "white")
            rc  = inc.get("root_cause", "")
            pending = inc.get("ai_pending", False)
            short = "analyzing…" if (pending or "⏳" in rc) else (rc[:55] or inc.get("title", "")[:55])
            age = _age(inc.get("created_at", ""))
            row = f"  [{col}]{sev}[/]  [white]{svc:<22}[/]  [dim]{short:<56}[/]  [dim]{age:>4}[/]"
            lv.append(ListItem(Static(row)))
        self._set_status(f"  {len(open_incs)} open  ·  auto-refresh 15s")

    def _draw_detail(self) -> None:
        incs = [i for i in self._incidents if i.get("status") != "RESOLVED"]
        detail = self.query_one("#detail", Static)
        if not incs:
            detail.update("[dim]no incidents[/]")
            return
        idx = min(self._selected, len(incs) - 1)
        if idx < 0:
            detail.update("")
            return
        inc = incs[idx]

        sev  = inc.get("severity", "P3")
        col  = _SEV_COLOR.get(sev, "white")
        svc  = inc.get("service", "?")
        rc   = inc.get("root_cause", "")
        sm   = inc.get("summary", "")
        src  = inc.get("rca_source", "")
        st   = inc.get("status", "OPEN")
        iid  = inc.get("incident_id", "")[:20]

        if inc.get("ai_pending") or "⏳" in rc:
            rc_line = "[yellow]⏳ AI analysis running…[/]"
        else:
            rc_line = f"[white]{rc[:100]}[/]" if rc else "[dim]—[/]"

        rb   = inc.get("runbook", {})
        steps = [rb.get(f"step{i}", "") for i in range(1, 5) if rb.get(f"step{i}")]
        runbook_line = "  ".join(f"[dim]{i+1}.[/] {s[:40]}" for i, s in enumerate(steps)) if steps else ""

        la    = inc.get("log_analysis", {})
        trend = la.get("degradation_trend", "")
        conf  = la.get("confidence", "")

        lines = [
            f"  [{col}]{sev}[/]  [bold white]{svc}[/]  [dim]{iid}[/]  [{_st_col(st)}]{st}[/]  [dim]{src}[/]",
            "",
            f"  [bold]Root cause[/]  {rc_line}",
        ]
        if sm:
            lines.append(f"  [bold]Impact[/]     [dim]{sm[:100]}[/]")
        if trend or conf:
            meta = "  ".join(filter(None, [
                f"trend [bold]{trend}[/]" if trend else "",
                f"confidence [bold]{conf}[/]" if conf else "",
            ]))
            lines.append(f"  {meta}")
        if runbook_line:
            lines.append(f"  [bold]Runbook[/]    {runbook_line}")

        detail.update("\n".join(lines))

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status", Static).update(f"  {msg}")
        except NoMatches:
            pass

    # ── list navigation ──────────────────────────────────────────────────────

    @on(ListView.Highlighted)
    def _highlighted(self, event: ListView.Highlighted) -> None:
        lv = self.query_one("#incident-list", ListView)
        self._selected = lv.index or 0
        self._draw_detail()

    def action_cursor_down(self) -> None:
        self.query_one("#incident-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#incident-list", ListView).action_cursor_up()

    # ── actions ──────────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._set_status("refreshing…")
        self.fetch_incidents()

    def action_fire(self) -> None:
        svc = self._prompt_inline("service name: ")
        if not svc:
            return
        self._do_fire(svc)

    def action_resolve(self) -> None:
        incs = [i for i in self._incidents if i.get("status") != "RESOLVED"]
        if not incs:
            return
        idx = min(self._selected, len(incs) - 1)
        iid = incs[idx].get("incident_id", "")
        if iid:
            self._do_resolve(iid)

    def action_diagnose(self) -> None:
        incs = [i for i in self._incidents if i.get("status") != "RESOLVED"]
        if not incs:
            return
        idx = min(self._selected, len(incs) - 1)
        svc = incs[idx].get("service", "")
        sev = incs[idx].get("severity", "P2")
        with self.suspend():
            subprocess.run(
                [sys.executable, "-m", "sentinel.cli", "diagnose", svc, "--severity", sev],
                env={**os.environ, "PYTHONPATH": "."},
            )
            input("\nPress Enter to return…")

    def _prompt_inline(self, prompt: str) -> str:
        with self.suspend():
            try:
                return input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                return ""

    @work(thread=True)
    def _do_fire(self, svc: str) -> None:
        try:
            resp = _api("/api/demo/fire", "POST", {"service": svc, "severity": "P2"})
            self.call_from_thread(self._set_status, f"fired {resp.get('incident_id','?')[:16]}")
        except Exception as e:
            self.call_from_thread(self._set_status, f"[red]error:[/] {e}")
        self.call_from_thread(self.fetch_incidents)

    @work(thread=True)
    def _do_resolve(self, iid: str) -> None:
        try:
            _api(f"/api/incidents/{iid}/resolve", "POST")
            self.call_from_thread(self._set_status, f"resolved {iid[:16]}")
        except Exception as e:
            self.call_from_thread(self._set_status, f"[red]error:[/] {e}")
        self.call_from_thread(self.fetch_incidents)


def _st_col(st: str) -> str:
    return {"OPEN": "red", "ACKNOWLEDGED": "yellow", "RESOLVED": "green"}.get(st, "white")


def run() -> None:
    SentinelTUI().run()


if __name__ == "__main__":
    run()
