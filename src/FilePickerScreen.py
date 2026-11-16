from pathlib import Path

from textual import on
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Input, Label, Button


class SessionFiles(DirectoryTree):
    def filter_paths(self, paths):
        return [path for path in paths if not path.name.startswith(".")
                and (path.is_dir() or path.suffix.lower() in {".har", ".json"})]


class FilePickerScreen(ModalScreen[Path | None]):
    DEFAULT_CSS = """
    FilePickerScreen { align: center middle; }
    #file-dialog { width: 88; max-width: 95%; height: 90%; padding: 1 2; border: round $accent; background: $surface; }
    #file-dialog Horizontal { height: auto; }
    #file-folder { width: 1fr; }
    #file-tree { height: 1fr; }
    #file-error { height: auto; color: $error; }
    """

    def __init__(self, directory=None):
        super().__init__()
        self.directory = Path(directory or Path.home())

    def compose(self):
        with Vertical(id="file-dialog"):
            yield Label("选择本人久事会话文件")
            with Horizontal():
                yield Input(str(self.directory), id="file-folder")
                yield Button("打开文件夹", id="file-open-folder")
            yield SessionFiles(self.directory, id="file-tree")
            yield Label("", id="file-error", markup=False)
            yield Button("取消", id="file-cancel")

    @on(Input.Submitted, "#file-folder")
    @on(Button.Pressed, "#file-open-folder")
    def open_folder(self):
        path = Path(self.query_one("#file-folder", Input).value.strip().strip('"')).expanduser()
        if not path.is_dir():
            self.query_one("#file-error", Label).update("文件夹不存在，请检查路径。")
            return
        self.query_one("#file-error", Label).update("")
        self.query_one("#file-tree", SessionFiles).path = path

    @on(DirectoryTree.FileSelected)
    def choose_file(self, event):
        self.dismiss(event.path)

    @on(Button.Pressed, "#file-cancel")
    def cancel(self):
        self.dismiss(None)
