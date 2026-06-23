"""Rendering helpers: state -> colour/label, footer legend."""

from __future__ import annotations

from rich.text import Text

from .models import NodeKind, NodeState, UnifiedNode

# Branches on these names are "normal" (grey badge); any other branch gets a
# distinct colour so non-default checkouts are immediately visible.
DEFAULT_BRANCHES = {"main", "master"}

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
        branch_style = "dim" if st.branch in DEFAULT_BRANCHES else "bold yellow"
        label.append(f"  [{st.branch}]", style=branch_style)

    if node.is_new and node.state is NodeState.MISSING_LOCAL:
        label.append("  ")
        label.append("NEW", style="bold magenta reverse")

    if node.remote_mismatch:
        # show the protocol the local origin IS using (the "wrong" one)
        # clone_url reflects workspace setting; mismatch → local is the opposite
        local_is_https = bool(node.clone_url and node.clone_url.startswith("git@"))
        label.append("  [↔https]" if local_is_https else "  [↔ssh]", style="bold magenta")

    if st and node.state is NodeState.OUT_OF_SYNC:
        bits: list[tuple[str, str]] = []
        if st.behind:
            bits.append((f"↓{st.behind}", "yellow"))
        elif st.ahead and not st.fetched:
            # behind unknown: fetch not run yet — remote may have new commits
            bits.append(("↓?", "dim"))
        if st.ahead:
            bits.append((f"↑{st.ahead}", "yellow"))
        if st.dirty:
            bits.append(("✎", "yellow"))
        if not st.has_upstream:
            bits.append(("no-upstream", "yellow"))
        if bits:
            label.append("  ")
            for i, (text, style) in enumerate(bits):
                if i:
                    label.append(" ", style="yellow")
                label.append(text, style=style)
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
