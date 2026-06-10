"""Rendering helpers: state -> colour/label, footer legend."""

from __future__ import annotations

from rich.text import Text

from .models import NodeKind, NodeState, UnifiedNode

STATE_STYLE: dict[NodeState, str] = {
    NodeState.SYNCED: "green",
    NodeState.OUT_OF_SYNC: "yellow",
    NodeState.MISSING_LOCAL: "cyan",
    NodeState.LOCAL_ONLY: "red",
    NodeState.ERROR: "bold red",
    NodeState.UNKNOWN: "dim",
}

STATE_GLYPH: dict[NodeState, str] = {
    NodeState.SYNCED: "✓",
    NodeState.OUT_OF_SYNC: "↻",
    NodeState.MISSING_LOCAL: "↓",
    NodeState.LOCAL_ONLY: "⚠",
    NodeState.ERROR: "✗",
    NodeState.UNKNOWN: "·",
}

LEGEND = (
    "[green]✓ synced[/]  [yellow]↻ out-of-sync[/]  "
    "[cyan]↓ to clone[/]  [red]⚠ local-only[/]  [bold red]✗ error[/]"
)


def repo_label(node: UnifiedNode) -> Text:
    style = STATE_STYLE.get(node.state, "")
    glyph = STATE_GLYPH.get(node.state, "")
    label = Text()
    label.append(f"{glyph} ", style=style)
    label.append(node.name, style=style)

    st = node.status
    if st and st.branch:
        label.append(f"  [{st.branch}]", style="dim")

    if node.is_new and node.state is NodeState.MISSING_LOCAL:
        label.append("  NEW", style="bold magenta reverse")

    if st and node.state is NodeState.OUT_OF_SYNC:
        bits = []
        if st.behind:
            bits.append(f"↓{st.behind}")
        if st.ahead:
            bits.append(f"↑{st.ahead}")
        if st.dirty:
            bits.append("✎")
        if not st.has_upstream:
            bits.append("no-upstream")
        if bits:
            label.append("  " + " ".join(bits), style="yellow")
    return label


def group_label(node: UnifiedNode) -> Text:
    repos = list(node.iter_repos())
    total = len(repos)
    out = sum(1 for r in repos if r.state is NodeState.OUT_OF_SYNC)
    missing = sum(1 for r in repos if r.state is NodeState.MISSING_LOCAL)
    label = Text()
    label.append(node.name, style="bold")
    summary = f"  ({total} repo{'s' if total != 1 else ''}"
    if out:
        summary += f", {out} out-of-sync"
    if missing:
        summary += f", {missing} to clone"
    summary += ")"
    label.append(summary, style="dim")
    return label


def node_label(node: UnifiedNode) -> Text:
    return repo_label(node) if node.kind is NodeKind.REPO else group_label(node)
