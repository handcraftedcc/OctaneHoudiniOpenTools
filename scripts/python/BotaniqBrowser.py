"""Small Houdini UI helpers for selecting a batch of Botaniq blend assets."""
from __future__ import print_function

from pathlib import Path

import openToolsUtils


SETTINGS_TOOL_NAME = "BotaniqImporter"


def _value(record, name):
    if isinstance(record, dict):
        return record[name]
    return getattr(record, name)


def _source_key(record):
    """Use the catalog's canonical source string; never resolve it per click."""
    return str(_value(record, "source"))


def _directory_selection(title, start_directory, parent=None):
    import hou
    try:
        from PySide6 import QtWidgets
    except ImportError:
        try:
            from PySide2 import QtWidgets
        except ImportError:
            QtWidgets = None

    # A Houdini top-level file chooser cannot receive input while this modal
    # browser is active.  A Qt child dialog remains interactive.
    if QtWidgets is not None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            parent or hou.qt.mainWindow(), title, str(start_directory or ""))
    else:
        selected = hou.ui.selectFile(
            title=title,
            file_type=hou.fileType.Directory,
            start_directory=str(start_directory or ""),
        )
    if not selected:
        return None
    return Path(hou.expandString(selected)).expanduser().resolve()


def _save(settings, key, value):
    settings[key] = str(value)
    openToolsUtils.setToolSettings(SETTINGS_TOOL_NAME, settings)


def _tree_path(record):
    # ``relative`` is already rooted at the selected library.  Keeping that
    # hierarchy makes category branches useful while a filename remains a leaf.
    return Path(str(_value(record, "relative"))).with_suffix("").as_posix()


def _select_records_tree(records):
    import hou
    paths = []
    by_path = {}
    for record in records:
        path = _tree_path(record)
        if path not in by_path:
            paths.append(path)
            by_path[path] = record

    selected = hou.ui.selectFromTree(
        paths,
        message="Select botaniq assets to export",
        title="Botaniq Assets",
        clear_on_cancel=True,
        width=700,
        height=600,
        allow_branch_selection=True,
        allow_compound_selection=True,
    )
    if not selected:
        return None

    import BotaniqBatch
    return BotaniqBatch.expand_selection(records, selected) or None


def _preview_path(root, record):
    """Resolve a pack preview from a model record without requiring mapr."""
    if isinstance(record, dict) and record.get("preview_cached"):
        cached = record.get("preview")
        return Path(cached) if cached and Path(cached).is_file() else None
    source = Path(_value(record, "source")).resolve()
    relative = Path(str(_value(record, "relative"))).with_suffix(".png")
    preview_kind = 'particles' if isinstance(record, dict) and record.get('kind') == 'collection' else 'models'
    # The normal pack layout is blends/{models,particles}/... with matching previews.
    for parent in (source.parent,) + tuple(source.parents):
        if parent.name.lower() in ('models', 'particles') and parent.parent.name.lower() == "blends":
            candidate = parent.parent.parent / "previews" / preview_kind / source.relative_to(parent).with_suffix(".png")
            if candidate.is_file():
                return candidate
    root = Path(root).resolve()
    candidates = [root / "previews" / preview_kind / relative]
    if root.name.lower() == "models":
        candidates.append(root.parent / "previews" / preview_kind / relative)
        if root.parent.name.lower() == "blends":
            candidates.append(root.parent.parent / "previews" / preview_kind / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _folder_paths(records):
    folders = {""}
    for record in records:
        category = str(_value(record, "category") or "").replace("\\", "/").strip("/")
        parts = category.split("/") if category else []
        folders.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return sorted(folders, key=lambda value: (value.count("/"), value.casefold()))


def _sections(records):
    """Group assets into top-level folder sections for the thumbnail browser."""
    grouped = {}
    for record in records:
        category = str(_value(record, "category") or "").replace("\\", "/").strip("/")
        section = category.split("/", 1)[0] if category else ""
        grouped.setdefault(section, []).append(record)
    return [(section, grouped[section])
            for section in sorted(grouped, key=lambda value: (not bool(value), value.casefold()))]


def _select_records_thumbnails(root, records, title="Botaniq Assets", single_selection=False,
                               recache_callback=None, change_library_callback=None):
    """Show one scrollable, sectioned thumbnail selector in Houdini's Qt host."""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

    import hou

    class ThumbnailBrowser(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super(ThumbnailBrowser, self).__init__(parent)
            self.setWindowTitle(title)
            self.resize(1100, 760)
            self._tab_mode = isinstance(records, dict)
            self._tab_specs = ([('assets', 'Assets', list(records.get('assets', [])), False),
                                ('collections', 'Collections', list(records.get('collections', [])), True)]
                               if self._tab_mode else [('assets', 'Assets', list(records), single_selection)])
            self._tab_key = None
            self._selected_by_tab = {key: set() for key, _label, _records, _single in self._tab_specs}
            self.records = []
            self._ordered_sources = []
            self._source_indices = {}
            self.selected = set()
            self._single_selection = single_selection
            self._last_clicked = None
            self._tiles = {}
            self._section_headers = {}
            self._section_records = {}
            self._section_labels = {}
            self._section_grids = {}
            self._section_previews_loaded = set()
            self._source_sections = {}
            self._section_selected_counts = {}
            self._section_complete = {}
            self._selected_brush = None
            self._unselected_brush = None

            self._root = QtWidgets.QLineEdit(str(root), self)
            self._root.setReadOnly(True)
            self.selected_root = Path(root)
            self._change_library = QtWidgets.QPushButton('Change Library', self)
            self._change_library.setVisible(change_library_callback is not None)
            self._change_library.clicked.connect(self._change_library_root)
            self._recache = QtWidgets.QPushButton('Recache Library', self)
            self._recache.setVisible(recache_callback is not None)
            self._recache.clicked.connect(self._recache_library)

            self._scroll = QtWidgets.QScrollArea(self)
            self._scroll.setWidgetResizable(True)
            self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            self._contents = None
            self._contents_layout = None

            self._status = QtWidgets.QLabel("Expand a category to browse its tiles. Shift selects a range; Ctrl toggles tiles.", self)
            self._select_all = QtWidgets.QPushButton("Select All", self)
            self._select_all.clicked.connect(self._select_everything)
            self._deselect_all = QtWidgets.QPushButton("Deselect All", self)
            self._deselect_all.clicked.connect(lambda: self._set_selected(set()))
            self._done = QtWidgets.QPushButton("Import Collection" if single_selection else "Import Selected", self)
            self._done.clicked.connect(self.accept)
            self._cancel = QtWidgets.QPushButton("Cancel", self)
            self._cancel.clicked.connect(self.reject)

            root_row = QtWidgets.QHBoxLayout()
            root_row.addWidget(QtWidgets.QLabel("Source:"), 0)
            root_row.addWidget(self._root, 1)
            root_row.addWidget(self._change_library, 0)
            root_row.addWidget(self._recache, 0)
            footer = QtWidgets.QHBoxLayout()
            footer.addWidget(self._status, 1)
            footer.addWidget(self._select_all)
            footer.addWidget(self._deselect_all)
            footer.addWidget(self._done)
            footer.addWidget(self._cancel)
            layout = QtWidgets.QVBoxLayout(self)
            layout.addLayout(root_row)
            self._tabs = None
            if self._tab_mode:
                self._tabs = QtWidgets.QTabBar(self)
                for _key, label, tab_records, _single in self._tab_specs:
                    index = self._tabs.addTab('{} ({})'.format(label, len(tab_records)))
                    self._tabs.setTabEnabled(index, bool(tab_records))
                self._tabs.currentChanged.connect(self._activate_tab)
                layout.addWidget(self._tabs)
            layout.addWidget(self._scroll, 1)
            layout.addLayout(footer)
            self._activate_tab(0)

        def _recache_library(self):
            self._recache.setEnabled(False)
            self._status.setText('Recaching library…')
            QtWidgets.QApplication.processEvents()
            try:
                library = recache_callback(self.selected_root)
                self._tab_specs = [('assets', 'Assets', list(library.get('assets', [])), False),
                                   ('collections', 'Collections', list(library.get('collections', [])), True)]
                self._selected_by_tab = {key: set() for key, _label, _records, _single in self._tab_specs}
                self._tab_key = None
                self._tabs.blockSignals(True)
                while self._tabs.count():
                    self._tabs.removeTab(0)
                for _key, label, tab_records, _single in self._tab_specs:
                    index = self._tabs.addTab('{} ({})'.format(label, len(tab_records)))
                    self._tabs.setTabEnabled(index, bool(tab_records))
                self._tabs.blockSignals(False)
                self._activate_tab(0)
                self._status.setText('Library recached.')
            except Exception as exc:
                self._status.setText('Recache failed: {}'.format(exc))
            finally:
                self._recache.setEnabled(True)

        def _change_library_root(self):
            changed = change_library_callback(self.selected_root, self)
            if not changed:
                return
            root, library = changed
            self.selected_root = Path(root)
            self._root.setText(str(root))
            self._tab_specs = [('assets', 'Assets', list(library.get('assets', [])), False),
                               ('collections', 'Collections', list(library.get('collections', [])), True)]
            self._selected_by_tab = {key: set() for key, _label, _records, _single in self._tab_specs}
            self._tab_key = None
            self._tabs.blockSignals(True)
            while self._tabs.count():
                self._tabs.removeTab(0)
            for _key, label, tab_records, _single in self._tab_specs:
                index = self._tabs.addTab('{} ({})'.format(label, len(tab_records)))
                self._tabs.setTabEnabled(index, bool(tab_records))
            self._tabs.blockSignals(False)
            self._activate_tab(0)
            self._status.setText('Library changed.')

        def _activate_tab(self, index):
            if index < 0 or index >= len(self._tab_specs):
                return
            if self._tab_key is not None:
                self._selected_by_tab[self._tab_key] = set(self.selected)
            key, _label, tab_records, is_single = self._tab_specs[index]
            self._tab_key = key
            self.records = tab_records
            self._single_selection = is_single
            self.selected = set(self._selected_by_tab[key])
            self._ordered_sources = [_source_key(record) for record in self.records]
            self._source_indices = {source: order for order, source in enumerate(self._ordered_sources)}
            self._last_clicked = None
            self._tiles = {}
            self._section_headers = {}
            self._section_records = {}
            self._section_labels = {}
            self._section_grids = {}
            self._section_previews_loaded = set()
            self._source_sections = {}
            self._section_selected_counts = {}
            self._section_complete = {}
            self._contents = QtWidgets.QWidget(self._scroll)
            self._contents_layout = QtWidgets.QVBoxLayout(self._contents)
            self._contents_layout.setContentsMargins(2, 2, 2, 2)
            self._contents_layout.setSpacing(8)
            self._scroll.setWidget(self._contents)
            self._done.setText('Import Collection' if self._single_selection else 'Import Selected')
            self._build_sections()

        def _build_sections(self):
            for section, section_records in _sections(self.records):
                label = section.replace("/", " / ") if section else "Other Assets"
                frame = QtWidgets.QFrame(self._contents)
                frame_layout = QtWidgets.QVBoxLayout(frame)
                frame_layout.setContentsMargins(0, 0, 0, 0)
                frame_layout.setSpacing(4)

                header_row = QtWidgets.QHBoxLayout()
                header = QtWidgets.QPushButton(label, frame)
                header.setText(label)
                header.setMinimumHeight(30)
                arrow = QtWidgets.QPushButton("▶", frame)
                arrow.setFixedWidth(28)
                arrow.setToolTip("Expand section")
                header_row.addWidget(header, 1)
                header_row.addWidget(arrow, 0)
                frame_layout.addLayout(header_row)

                tiles = QtWidgets.QListWidget(frame)
                tiles.setViewMode(QtWidgets.QListView.IconMode)
                tiles.setResizeMode(QtWidgets.QListView.Adjust)
                tiles.setMovement(QtWidgets.QListView.Static)
                tiles.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
                tiles.setIconSize(QtCore.QSize(180, 122))
                tiles.setGridSize(QtCore.QSize(200, 168))
                tiles.setSpacing(6)
                tiles.setWordWrap(True)
                tiles.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
                tiles.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
                tiles.itemClicked.connect(self._item_clicked)
                for index, record in enumerate(section_records):
                    source = _source_key(record)
                    tile = QtWidgets.QListWidgetItem()
                    category = str(_value(record, "category") or "").replace("\\", "/")
                    text = str(_value(record, "name"))
                    if "/" in category:
                        text += "\n" + category.split("/", 1)[1]
                    tile.setText(text)
                    tile.setTextAlignment(QtCore.Qt.AlignHCenter)
                    tile.setData(QtCore.Qt.UserRole, source)
                    tile.setToolTip(str(_value(record, "source")))
                    preview = record.get("preview") if isinstance(record, dict) else None
                    if not preview:
                        preview = _preview_path(root, record)
                    tile.setData(QtCore.Qt.UserRole + 1, str(preview) if preview else "")
                    self._tiles[source] = tile
                    self._source_sections[source] = section
                    tiles.addItem(tile)
                tiles.setFixedHeight(max(180, ((len(section_records) + 4) // 5) * 174))
                tiles.setVisible(False)
                frame_layout.addWidget(tiles)
                arrow.clicked.connect(lambda _checked=False, widget=tiles, button=arrow: self._toggle_section(widget, button))
                if self._single_selection:
                    header.clicked.connect(lambda _checked=False, widget=tiles, button=arrow: self._toggle_section(widget, button))
                else:
                    header.clicked.connect(lambda _checked=False, key=section: self._select_section(key))
                self._section_headers[section] = header
                self._section_grids[section] = tiles
                self._section_records[section] = [_source_key(record)
                                                  for record in section_records]
                self._section_labels[section] = label
                self._section_selected_counts[section] = sum(source in self.selected
                                                             for source in self._section_records[section])
                self._section_complete[section] = False
                self._contents_layout.addWidget(frame)
            self._contents_layout.addStretch(1)
            self._refresh_selection()

        def _toggle_section(self, widget, button):
            visible = not widget.isVisible()
            if visible:
                section = next(key for key, grid in self._section_grids.items() if grid is widget)
                self._load_section_previews(section)
            widget.setVisible(visible)
            button.setText("▼" if visible else "▶")
            button.setToolTip("Collapse section" if visible else "Expand section")

        def _load_section_previews(self, section):
            if section in self._section_previews_loaded:
                return
            tiles = self._section_grids[section]
            for index in range(tiles.count()):
                tile = tiles.item(index)
                preview = tile.data(QtCore.Qt.UserRole + 1)
                if not preview:
                    continue
                pixmap = QtGui.QPixmap(str(preview))
                if not pixmap.isNull():
                    tile.setIcon(QtGui.QIcon(pixmap.scaled(tiles.iconSize(), QtCore.Qt.KeepAspectRatio,
                                                           QtCore.Qt.SmoothTransformation)))
            self._section_previews_loaded.add(section)

        def _item_clicked(self, item):
            source = item.data(QtCore.Qt.UserRole)
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            current = self._source_indices[source]
            if modifiers & QtCore.Qt.ShiftModifier and self._last_clicked in self._source_indices:
                anchor = self._source_indices[self._last_clicked]
                range_selection = set(self._ordered_sources[min(anchor, current):max(anchor, current) + 1])
                selected = self.selected | range_selection if modifiers & QtCore.Qt.ControlModifier else range_selection
            elif modifiers & QtCore.Qt.ControlModifier and not self._single_selection:
                selected = set(self.selected)
                if source in selected:
                    selected.remove(source)
                else:
                    selected.add(source)
            else:
                selected = {source}
            self._last_clicked = source
            self._set_selected(selected)

        def _select_section(self, section):
            section_sources = set(self._section_records[section])
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            if modifiers & QtCore.Qt.ControlModifier:
                selected = set(self.selected)
                if section_sources.issubset(selected):
                    selected.difference_update(section_sources)
                else:
                    selected.update(section_sources)
            else:
                selected = section_sources
            self._set_selected(selected)

        def _select_everything(self):
            self._set_selected({self._ordered_sources[0]} if self._single_selection and self._ordered_sources
                               else set(self._tiles))

        def _set_selected(self, selected):
            if self._single_selection and selected:
                selected = {next(iter(selected))}
            selected = set(selected)
            changed = self.selected.symmetric_difference(selected)
            if not changed:
                self._status.setText("{} selected".format(len(self.selected)))
                return
            for source in changed:
                section = self._source_sections[source]
                self._section_selected_counts[section] += 1 if source in selected else -1
            self.selected = selected
            self._selected_by_tab[self._tab_key] = set(selected)
            self._refresh_selection(changed)

        def _refresh_selection(self, changed=None):
            if self._selected_brush is None:
                self._selected_brush = QtGui.QBrush(QtGui.QColor(45, 122, 66))
                self._unselected_brush = QtGui.QBrush(QtGui.QColor(48, 48, 48))
            sources = self._tiles if changed is None else changed
            changed_sections = set()
            for source in sources:
                self._tiles[source].setBackground(self._selected_brush if source in self.selected
                                                  else self._unselected_brush)
                changed_sections.add(self._source_sections[source])
            for section in (self._section_headers if changed is None else changed_sections):
                complete = (self._section_selected_counts[section] == len(self._section_records[section]))
                if complete != self._section_complete[section]:
                    self._section_complete[section] = complete
                    header = self._section_headers[section]
                    header.setText(("✓ " if complete else "") + self._section_labels[section])
                    header.setStyleSheet(
                        "QPushButton { background-color: #2d7a42; color: white; border: 1px solid #78c780; }"
                        if complete else "")
                    header.update()
            self._status.setText("{} selected".format(len(self.selected)))

        def chosen(self):
            selected = self.selected
            result = [record for record in self.records if _source_key(record) in selected]
            return (self.selected_root, self._tab_key, result) if self._tab_mode else result

    dialog = ThumbnailBrowser(parent=hou.qt.mainWindow())
    exec_method = getattr(dialog, "exec_", None) or dialog.exec
    if exec_method() != QtWidgets.QDialog.Accepted:
        return None
    return dialog.chosen() or None


def _select_records(root, records):
    try:
        _qt_modules = __import__("PySide6")
    except ImportError:
        try:
            _qt_modules = __import__("PySide2")
        except ImportError:
            return _select_records_tree(records)
    return _select_records_thumbnails(root, records)


def _choose_browser_tab(root, assets, collections):
    """Choose the thumbnail browser through a real Assets/Collections tab UI."""
    try:
        from PySide6 import QtWidgets
    except ImportError:
        try:
            from PySide2 import QtWidgets
        except ImportError:
            return 'assets' if assets else ('collections' if collections else None)
    import hou

    class BrowserTab(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super(BrowserTab, self).__init__(parent)
            self.setWindowTitle('Botaniq Browser')
            self.resize(520, 180)
            self.choice = None
            layout = QtWidgets.QVBoxLayout(self)
            tabs = QtWidgets.QTabWidget(self)
            layout.addWidget(tabs, 1)
            for key, label, records, message in (
                    ('assets', 'Assets', assets, 'Browse and batch import individual models.'),
                    ('collections', 'Collections', collections,
                     'Browse particle collections. One collection imports all of its referenced models.')):
                page = QtWidgets.QWidget(tabs)
                page_layout = QtWidgets.QVBoxLayout(page)
                page_layout.addWidget(QtWidgets.QLabel(message, page))
                count = QtWidgets.QLabel('{} available'.format(len(records)), page)
                page_layout.addWidget(count)
                button = QtWidgets.QPushButton('Browse {}'.format(label), page)
                button.setEnabled(bool(records))
                button.clicked.connect(lambda _checked=False, selected=key: self._choose(selected))
                page_layout.addWidget(button)
                page_layout.addStretch(1)
                tabs.addTab(page, label)
            cancel = QtWidgets.QPushButton('Cancel', self)
            cancel.clicked.connect(self.reject)
            layout.addWidget(cancel)

        def _choose(self, key):
            self.choice = key
            self.accept()

    dialog = BrowserTab(hou.qt.mainWindow())
    exec_method = getattr(dialog, 'exec_', None) or dialog.exec
    return dialog.choice if exec_method() == QtWidgets.QDialog.Accepted else None


def _choose_root(settings):
    import hou
    root = Path(settings['source_root']).expanduser() if settings.get('source_root') else None
    if root is not None and root.is_dir():
        return root.resolve()
    else:
        root = _directory_selection('Choose botaniq pack or blends/models folder', root)
    return root.resolve() if root is not None else None


def _change_library(start_directory, parent=None):
    """Choose a different library from the persistent browser header action."""
    root = _directory_selection('Choose botaniq pack or blends/models folder', start_directory, parent)
    if root is None:
        return None
    import BotaniqCatalogCache
    library, _state = BotaniqCatalogCache.load_library(root)
    return root, library


def choose_assets(settings):
    """Return ``(root_path, records)`` selected from BotaniqBatch, or ``None``."""
    import hou
    import BotaniqBatch

    root = _choose_root(settings)
    if root is None:
        return None

    root = root.resolve()
    import BotaniqCatalogCache
    records, _cache_state = BotaniqCatalogCache.load_or_build(root)
    if not records:
        hou.ui.displayMessage("No .blend assets were found below {0}.".format(root), title="Botaniq Assets")
        return None
    _save(settings, "source_root", root)
    selected = _select_records(root, records)
    return (root, selected) if selected else None


def choose_import(settings):
    """Choose either batch model assets or one particle collection."""
    import hou
    import BotaniqCatalogCache
    import BotaniqCollections

    root = _choose_root(settings)
    if root is None:
        return None
    library, _cache_state = BotaniqCatalogCache.load_library(root)
    assets, collections = library['assets'], library['collections']
    if not assets and not collections:
        hou.ui.displayMessage('No botaniq assets or collections were found below {0}.'.format(root),
                              title='Botaniq Browser')
        return None
    _save(settings, 'source_root', root)
    try:
        __import__('PySide6')
    except ImportError:
        try:
            __import__('PySide2')
        except ImportError:
            selected = _select_records_tree(assets)
            return (root, 'assets', selected) if selected else None
    selection = _select_records_thumbnails(
        root, {'assets': assets, 'collections': collections}, title='Botaniq Browser',
        recache_callback=lambda current_root: BotaniqCatalogCache.load_library(current_root, force=True)[0],
        change_library_callback=lambda current_root, parent: _change_library(current_root, parent))
    if not selection:
        return None
    root, kind, selected = selection
    _save(settings, 'source_root', root)
    return (root, kind, selected) if selected else None


def choose_output(settings):
    """Return the output placement description, or ``None`` on cancel."""
    import hou

    choice = hou.ui.displayMessage(
        "Where should exported Botaniq packages be written?",
        buttons=("Next to Each Blend", "Choose Output Folder", "Cancel"),
        default_choice=0,
        close_choice=2,
        title="Botaniq Output",
    )
    if choice == 2:
        return None
    if choice == 0:
        return {"mode": "source", "custom_root": None}

    default = settings.get("custom_root") or hou.expandString("$HIP/assets/botaniq")
    root = _directory_selection("Choose Botaniq export folder", default)
    if root is None:
        return None
    _save(settings, "custom_root", root)
    return {"mode": "custom", "custom_root": root}
