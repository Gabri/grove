"""Modal screens: confirm, vault create/unlock, workspace + credential mgmt."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

from .models import ProviderCred, Workspace
from .vault import Vault

PROVIDERS = ["gitlab", "github", "bitbucket"]
_DEFAULT_ROOT_KEY = {"gitlab": "group", "github": "org", "bitbucket": "workspace"}


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses with True/False."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, confirm_label: str = "Confirm"):
        super().__init__()
        self._title = title
        self._body = body
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            yield Static(self._body, id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                yield Button(self._confirm_label, variant="primary", id="ok")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


class CreateVaultScreen(ModalScreen[str | None]):
    """First run: choose a master password. Returns the password (or None)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label("Create encrypted vault", id="form-title")
            yield Static(
                "Choose a master password. It encrypts all your keys. "
                "There is no recovery if you forget it.",
                id="form-help",
            )
            yield Input(password=True, placeholder="master password", id="pw1")
            yield Input(password=True, placeholder="confirm password", id="pw2")
            yield Static("", id="form-error")
            with Horizontal(id="confirm-buttons"):
                yield Button("Create", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        pw1 = self.query_one("#pw1", Input).value
        pw2 = self.query_one("#pw2", Input).value
        err = self.query_one("#form-error", Static)
        if len(pw1) < 4:
            err.update("[red]password too short (min 4 chars)[/]")
            return
        if pw1 != pw2:
            err.update("[red]passwords do not match[/]")
            return
        self.dismiss(pw1)

    def action_cancel(self) -> None:
        self.dismiss(None)


class UnlockScreen(ModalScreen[str | None]):
    """Prompt for the master password. Returns it (or None on cancel)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, error: str | None = None):
        super().__init__()
        self._error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label("Unlock vault", id="form-title")
            if self._error:
                yield Static(f"[red]{self._error}[/]", id="form-error")
            yield Input(password=True, placeholder="master password", id="pw")
            with Horizontal(id="confirm-buttons"):
                yield Button("Unlock", variant="primary", id="ok")
                yield Button("Quit", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#pw", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.dismiss(self.query_one("#pw", Input).value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextPromptScreen(ModalScreen[str | None]):
    """Single-line prompt prefilled with an initial value. Returns str or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, help_text: str, initial: str = ""):
        super().__init__()
        self._title = title
        self._help = help_text
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label(self._title, id="form-title")
            yield Static(self._help, id="form-help")
            yield Input(value=self._initial, id="value")
            with Horizontal(id="confirm-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#value", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class WorkspaceFormScreen(ModalScreen[Workspace | None]):
    """Create a new workspace. Returns a Workspace (no keys yet) or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label("New workspace", id="form-title")
            yield Input(placeholder="name (e.g. clientx)", id="name")
            yield Input(
                placeholder="local folder to sync into "
                "(e.g. ~/repos/clientx; empty = vault default ~/repos)",
                id="base",
            )
            yield Static("", id="form-error")
            with Horizontal(id="confirm-buttons"):
                yield Button("Create", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        name = self.query_one("#name", Input).value.strip()
        if not name:
            self.query_one("#form-error", Static).update("[red]name required[/]")
            return
        base = self.query_one("#base", Input).value.strip() or None
        self.dismiss(Workspace(name=name, clone_base=base))

    def action_cancel(self) -> None:
        self.dismiss(None)


class CredentialFormScreen(ModalScreen[ProviderCred | None]):
    """Add a provider key (label + token + roots) to a workspace."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form-box-wide"):
            yield Label("Add provider key", id="form-title")
            yield Select(
                [(p, p) for p in PROVIDERS],
                prompt="provider",
                id="provider",
                value="gitlab",
            )
            yield Input(placeholder="label (e.g. work, personal)", id="label")
            yield Input(
                placeholder="base_url — REQUIRED for self-hosted/SSO GitLab "
                "(e.g. https://gitlab.client.com); empty = gitlab.com",
                id="base_url",
            )
            yield Input(
                placeholder="user (bitbucket only)", id="user"
            )
            yield Input(password=True, placeholder="token / app password", id="token")
            yield Label("roots — REMOTE groups/orgs to scan (one per line):",
                        classes="field-label")
            yield Static(
                "[dim]the remote group path, NOT a local folder. "
                "e.g. 'team/platform' (gitlab group + subgroups), "
                "'acme' or 'org=acme' (github org), 'user=me', 'workspace=ws'[/]",
                classes="field-help",
            )
            yield TextArea(id="roots")
            yield Static("", id="form-error")
            with Horizontal(id="confirm-buttons"):
                yield Button("Add", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def _parse_roots(self, provider: str, text: str) -> list[dict]:
        roots: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                roots.append({key.strip(): val.strip()})
            else:
                roots.append({_DEFAULT_ROOT_KEY[provider]: line})
        return roots

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        err = self.query_one("#form-error", Static)
        provider = self.query_one("#provider", Select).value
        if provider == Select.BLANK:
            err.update("[red]pick a provider[/]")
            return
        label = self.query_one("#label", Input).value.strip()
        token = self.query_one("#token", Input).value.strip()
        if not label:
            err.update("[red]label required[/]")
            return
        if not token:
            err.update("[red]token required[/]")
            return
        roots = self._parse_roots(
            provider, self.query_one("#roots", TextArea).text
        )
        if not roots:
            err.update("[red]add at least one root[/]")
            return
        self.dismiss(
            ProviderCred(
                provider=provider,
                label=label,
                token=token,
                user=self.query_one("#user", Input).value.strip() or None,
                base_url=self.query_one("#base_url", Input).value.strip() or None,
                roots=roots,
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class WorkspaceManagerScreen(ModalScreen[str | None]):
    """List/switch/create/delete workspaces and manage their keys.

    Mutates the live vault and saves in place. Dismisses with the name of the
    workspace to make active (or None if unchanged).
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("n", "new_workspace", "New workspace"),
        ("a", "add_key", "Add key"),
        ("b", "set_base", "Set local dir"),
        ("d", "delete", "Delete"),
        ("enter", "switch", "Switch to"),
    ]

    def __init__(self, vault: Vault):
        super().__init__()
        self.vault = vault
        self._result: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="ws-box"):
            yield Label("Workspaces", id="form-title")
            yield Static(
                "[dim]enter=switch  n=new  a=add key  b=local dir  "
                "d=delete  esc=close[/]",
                id="ws-hint",
            )
            yield ListView(id="ws-list")
            yield Static("", id="ws-detail")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#ws-list", ListView).focus()

    def _refresh_list(self, highlight: str | None = None) -> None:
        lv = self.query_one("#ws-list", ListView)
        lv.clear()
        active = self.vault.data.active_workspace
        default_base = self.vault.data.default_clone_base
        for ws in self.vault.data.workspaces:
            marker = "[green]●[/] " if ws.name == active else "  "
            base = ws.clone_base or default_base
            label = (
                f"{marker}{ws.name}  [dim]{ws.provider_summary()}"
                f"   ↧ {base}[/]"
            )
            item = ListItem(Label(label))
            item.ws_name = ws.name  # type: ignore[attr-defined]
            lv.append(item)
        if self.vault.data.workspaces:
            names = [w.name for w in self.vault.data.workspaces]
            target = highlight if highlight in names else names[0]
            lv.index = names.index(target)
        else:
            self.query_one("#ws-detail", Static).update(
                "[yellow]No workspaces yet. Press 'n' to create one.[/]"
            )

    def _selected_name(self) -> str | None:
        lv = self.query_one("#ws-list", ListView)
        item = lv.highlighted_child
        name = getattr(item, "ws_name", None) if item else None
        if name is None and len(self.vault.data.workspaces) == 1:
            return self.vault.data.workspaces[0].name
        return name

    def action_close(self) -> None:
        self.dismiss(self._result)

    def action_new_workspace(self) -> None:
        def done(ws: Workspace | None) -> None:
            if ws is None:
                return
            if self.vault.data.get_workspace(ws.name):
                self.notify(f"workspace '{ws.name}' exists", severity="error")
                return
            self.vault.data.workspaces.append(ws)
            if self.vault.data.active_workspace is None:
                self.vault.data.active_workspace = ws.name
            self.vault.save()
            self._refresh_list(highlight=ws.name)
            self.notify(f"created workspace '{ws.name}' — press 'a' to add a key")

        self.app.push_screen(WorkspaceFormScreen(), done)

    def action_add_key(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)

        def done(cred: ProviderCred | None) -> None:
            if cred is None or ws is None:
                return
            # one key per provider per workspace: replace if it already exists
            existing = next(
                (p for p in ws.providers if p.provider == cred.provider), None
            )
            verb = "added"
            if existing is not None:
                ws.providers.remove(existing)
                verb = "replaced"
            ws.providers.append(cred)
            self.vault.save()
            self._refresh_list(highlight=name)
            self.notify(f"{verb} {cred.provider}:{cred.label} on '{name}'")

        self.app.push_screen(CredentialFormScreen(), done)

    def action_set_base(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)
        current = (ws.clone_base if ws else "") or self.vault.data.default_clone_base

        def done(value: str | None) -> None:
            if value is None or ws is None:
                return
            ws.clone_base = value.strip() or None
            self.vault.save()
            self._refresh_list(highlight=name)
            shown = ws.clone_base or self.vault.data.default_clone_base
            self.notify(f"'{name}' syncs into {shown}")

        self.app.push_screen(
            TextPromptScreen(
                "Local sync folder",
                "Absolute or ~-path where this workspace's repos live / are cloned. "
                "Existing clones are matched by their remote anywhere below it.",
                current,
            ),
            done,
        )

    def action_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return

        def done(ok: bool) -> None:
            if not ok:
                return
            self.vault.data.workspaces = [
                w for w in self.vault.data.workspaces if w.name != name
            ]
            if self.vault.data.active_workspace == name:
                self.vault.data.active_workspace = (
                    self.vault.data.workspaces[0].name
                    if self.vault.data.workspaces
                    else None
                )
                self._result = self.vault.data.active_workspace
            self.vault.save()
            self._refresh_list()

        self.app.push_screen(
            ConfirmScreen("Delete workspace", f"Delete '{name}'?", "Delete"), done
        )

    def action_switch(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        ws = self.vault.data.get_workspace(name)
        if ws and not ws.providers:
            self.notify("workspace has no keys — press 'a' first", severity="warning")
            return
        self.vault.data.active_workspace = name
        self.vault.save()
        self.dismiss(name)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_switch()
