"""Browse the model registry from inside the TUI (`m`) — `runectl models list`, as a table.

Pure in-process read (`MODEL_REGISTRY` + `providers.keys.list_keys()`), no
subprocess: this screen only displays data, same as `models_cmd.models_list`,
just reformatted as rows instead of tab-separated lines.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from runectl.providers.keys import list_keys
from runectl.providers.registry import MODEL_REGISTRY

_COLUMNS = ("id", "provider", "context", "thinking", "$in/1M", "$out/1M", "key")


class ModelsScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ModelsScreen {
        align: center middle;
    }
    #models-box {
        width: 90%;
        height: auto;
        max-height: 90%;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="models-box"):
            yield Static("[b]Model registry[/b] — press escape to close")
            yield DataTable(id="models-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#models-table", DataTable)
        table.add_columns(*_COLUMNS)
        present = list_keys()
        for model in sorted(MODEL_REGISTRY.values(), key=lambda m: (m.provider, m.id)):
            thinking = f"yes (max {model.max_thinking_level})" if model.supports_thinking else "no"
            key = "yes" if present.get(model.provider) else "no"
            table.add_row(
                model.id, model.provider, str(model.context_window), thinking,
                f"{model.price_in:.2f}", f"{model.price_out:.2f}", key,
            )
