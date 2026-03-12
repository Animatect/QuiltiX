from qtpy import QtCore, QtWidgets  # type: ignore


class MaterialManagerWidget(QtWidgets.QWidget):
    material_activated = QtCore.Signal(str)       # user selected a material to edit
    material_added = QtCore.Signal(str)           # new material created
    material_removed = QtCore.Signal(str)         # material deleted
    looks_scope_changed = QtCore.Signal(str)      # user changed the looks scope path
    material_export_requested = QtCore.Signal(str) # user wants to export a material copy

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Looks scope path
        scope_layout = QtWidgets.QHBoxLayout()
        scope_label = QtWidgets.QLabel("Scope:")
        scope_label.setFixedWidth(42)
        self._scope_edit = QtWidgets.QLineEdit()
        self._scope_edit.setPlaceholderText("/MaterialX/Materials  (default)")
        self._scope_edit.setToolTip(
            "Optional USD path under which reference material prims are created.\n"
            "E.g. /World/Looks — materials appear at /World/Looks/{name} in the hierarchy.\n"
            "Leave empty to bind directly from /MaterialX/Materials/."
        )
        self._scope_edit.editingFinished.connect(self._on_scope_changed)
        scope_layout.addWidget(scope_label)
        scope_layout.addWidget(self._scope_edit)
        layout.addLayout(scope_layout)

        self._list = QtWidgets.QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_list_context_menu)
        layout.addWidget(self._list)

        btn_layout = QtWidgets.QHBoxLayout()
        self._btn_add = QtWidgets.QPushButton("+ New")
        self._btn_del = QtWidgets.QPushButton("- Delete")
        self._btn_add.clicked.connect(self._on_add_clicked)
        self._btn_del.clicked.connect(self._on_delete_clicked)
        btn_layout.addWidget(self._btn_add)
        btn_layout.addWidget(self._btn_del)
        layout.addLayout(btn_layout)

        self._counter = 1

    def _show_list_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        menu = QtWidgets.QMenu(self)
        export_action = menu.addAction("Export copy...")
        action = menu.exec_(self._list.viewport().mapToGlobal(pos))
        if action == export_action:
            self.material_export_requested.emit(item.text())

    def _on_scope_changed(self):
        self.looks_scope_changed.emit(self._scope_edit.text().strip())

    def get_looks_scope(self):
        return self._scope_edit.text().strip()

    def _on_selection_changed(self, current, previous):
        if current:
            self.material_activated.emit(current.text())

    def _on_add_clicked(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Material", "Material name:", text=f"Material{self._counter}"
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if self._list.findItems(name, QtCore.Qt.MatchExactly):
            QtWidgets.QMessageBox.warning(self, "Duplicate", f'Material "{name}" already exists.')
            return
        self._counter += 1
        # Emit added BEFORE selection change so the layer exists when activated fires
        self.material_added.emit(name)
        self.add_material(name, set_active=True)

    def _on_delete_clicked(self):
        item = self._list.currentItem()
        if not item:
            return
        name = item.text()
        row = self._list.row(item)
        self._list.takeItem(row)
        self.material_removed.emit(name)

    def add_material(self, name, set_active=True):
        """Add a material to the list. If set_active, select it (fires material_activated)."""
        matches = self._list.findItems(name, QtCore.Qt.MatchExactly)
        if matches:
            if set_active:
                self._list.setCurrentItem(matches[0])
            return
        item = QtWidgets.QListWidgetItem(name)
        self._list.addItem(item)
        if set_active:
            self._list.setCurrentItem(item)

    def set_active_material(self, name):
        """Select a material without firing material_activated."""
        matches = self._list.findItems(name, QtCore.Qt.MatchExactly)
        if matches:
            self._list.blockSignals(True)
            self._list.setCurrentItem(matches[0])
            self._list.blockSignals(False)

    def get_active_material(self):
        item = self._list.currentItem()
        return item.text() if item else None

    def get_all_materials(self):
        return [self._list.item(i).text() for i in range(self._list.count())]
