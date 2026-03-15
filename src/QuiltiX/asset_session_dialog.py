"""
Dialog for creating or loading an Asset Workflow session in QuiltiX.

Two modes (tabs):
  New  — pick a geo layer + material library (existing or new) + .mtlx directory
  Load — pick an existing MTL layer + geo layer for viewport preview
"""

import os

from qtpy import QtWidgets, QtCore  # type: ignore

from QuiltiX.asset_session import AssetSession, detect_asset_name, read_material_library


class AssetSessionDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Asset Session")
        self.setMinimumWidth(640)
        self._session = None

        layout = QtWidgets.QVBoxLayout(self)

        self._tabs = QtWidgets.QTabWidget()
        layout.addWidget(self._tabs)

        self._build_new_tab()
        self._build_load_tab()

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_new_tab(self):
        tab = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(tab)
        form.setLabelAlignment(QtCore.Qt.AlignRight)

        self._new_geo_edit = QtWidgets.QLineEdit()
        self._new_geo_edit.editingFinished.connect(self._auto_name_from_geo)
        form.addRow("Geo Layer:", self._row(self._new_geo_edit, "Browse...", self._browse_new_geo))

        self._new_asset_name = QtWidgets.QLineEdit()
        form.addRow("Asset Name:", self._new_asset_name)

        self._new_lib_edit = QtWidgets.QLineEdit()
        lib_btns = QtWidgets.QWidget()
        lib_btn_layout = QtWidgets.QHBoxLayout(lib_btns)
        lib_btn_layout.setContentsMargins(0, 0, 0, 0)
        lib_btn_layout.addWidget(self._new_lib_edit)
        browse_lib = QtWidgets.QPushButton("Browse existing...")
        browse_lib.clicked.connect(self._browse_existing_lib)
        lib_btn_layout.addWidget(browse_lib)
        create_lib = QtWidgets.QPushButton("Create new...")
        create_lib.clicked.connect(self._create_new_lib)
        lib_btn_layout.addWidget(create_lib)
        form.addRow("Material Library:", lib_btns)

        self._new_mtlx_dir_edit = QtWidgets.QLineEdit()
        form.addRow(".mtlx Directory:", self._row(self._new_mtlx_dir_edit, "Browse...", self._browse_mtlx_dir))

        self._tabs.addTab(tab, "New Asset Session")

    def _build_load_tab(self):
        tab = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(tab)
        form.setLabelAlignment(QtCore.Qt.AlignRight)

        self._load_mtl_edit = QtWidgets.QLineEdit()
        self._load_mtl_edit.editingFinished.connect(self._auto_detect_from_mtl)
        form.addRow("MTL Layer:", self._row(self._load_mtl_edit, "Browse...", self._browse_load_mtl))

        self._load_geo_edit = QtWidgets.QLineEdit()
        form.addRow("Geo Layer (preview):", self._row(self._load_geo_edit, "Browse...", self._browse_load_geo))

        self._load_asset_name = QtWidgets.QLineEdit()
        self._load_asset_name.setReadOnly(True)
        self._load_asset_name.setPlaceholderText("auto-detected from MTL layer")
        form.addRow("Asset Name:", self._load_asset_name)

        self._load_lib_edit = QtWidgets.QLineEdit()
        self._load_lib_edit.setReadOnly(True)
        self._load_lib_edit.setPlaceholderText("auto-detected from MTL layer")
        form.addRow("Material Library:", self._load_lib_edit)

        self._tabs.addTab(tab, "Load from MTL Layer")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(widget, btn_label, callback):
        container = QtWidgets.QWidget()
        hbox = QtWidgets.QHBoxLayout(container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.addWidget(widget)
        btn = QtWidgets.QPushButton(btn_label)
        btn.clicked.connect(callback)
        hbox.addWidget(btn)
        return container

    # ------------------------------------------------------------------
    # Browse slots — New tab
    # ------------------------------------------------------------------

    def _browse_new_geo(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Geo Layer", "", "USD Files (*.usd *.usda *.usdc);;All Files (*)"
        )
        if path:
            self._new_geo_edit.setText(path)
            self._auto_name_from_geo()

    def _auto_name_from_geo(self):
        path = self._new_geo_edit.text().strip()
        if path and os.path.exists(path):
            name = detect_asset_name(path)
            if name:
                self._new_asset_name.setText(name)

    def _browse_existing_lib(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Material Library", "", "USD Files (*.usda *.usd *.usdc);;All Files (*)"
        )
        if path:
            self._new_lib_edit.setText(path)
            if not self._new_mtlx_dir_edit.text():
                self._new_mtlx_dir_edit.setText(os.path.dirname(path))

    def _create_new_lib(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Create Material Library", "", "USD Assembly (*.usda);;All Files (*)"
        )
        if path:
            self._new_lib_edit.setText(path)
            if not self._new_mtlx_dir_edit.text():
                self._new_mtlx_dir_edit.setText(os.path.dirname(path))

    def _browse_mtlx_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select .mtlx Output Directory")
        if path:
            self._new_mtlx_dir_edit.setText(path)

    # ------------------------------------------------------------------
    # Browse slots — Load tab
    # ------------------------------------------------------------------

    def _browse_load_mtl(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select MTL Layer", "", "USD Files (*.usda *.usd *.usdc);;All Files (*)"
        )
        if path:
            self._load_mtl_edit.setText(path)
            self._auto_detect_from_mtl()

    def _auto_detect_from_mtl(self):
        mtl_path = self._load_mtl_edit.text().strip()
        if not mtl_path or not os.path.exists(mtl_path):
            return

        from pxr import Sdf
        layer = Sdf.Layer.FindOrOpen(mtl_path)
        if not layer:
            return

        self._load_asset_name.setText(layer.defaultPrim or "")

        # Find the material library path from the 'mtl' scope reference
        mtl_dir = os.path.dirname(os.path.abspath(mtl_path))
        for root_spec in layer.rootPrims:
            if "mtl" in root_spec.nameChildren:
                mtl_spec = root_spec.nameChildren["mtl"]
                refs = mtl_spec.referenceList.prependedItems
                if refs:
                    asset_path = refs[0].assetPath
                    if asset_path:
                        if not os.path.isabs(asset_path):
                            asset_path = os.path.normpath(
                                os.path.join(mtl_dir, asset_path)
                            )
                        self._load_lib_edit.setText(asset_path)
                break

    def _browse_load_geo(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Geo Layer (preview)", "", "USD Files (*.usd *.usda *.usdc);;All Files (*)"
        )
        if path:
            self._load_geo_edit.setText(path)

    # ------------------------------------------------------------------
    # Accept / result
    # ------------------------------------------------------------------

    def _on_accept(self):
        if self._tabs.currentIndex() == 0:
            self._accept_new()
        else:
            self._accept_load()

    def _accept_new(self):
        name = self._new_asset_name.text().strip()
        lib = self._new_lib_edit.text().strip()
        geo = self._new_geo_edit.text().strip()
        mtlx_dir = self._new_mtlx_dir_edit.text().strip()

        if not name:
            QtWidgets.QMessageBox.warning(self, "Asset Session", "Asset name is required.")
            return
        if not lib:
            QtWidgets.QMessageBox.warning(self, "Asset Session", "Material library path is required.")
            return
        if not mtlx_dir:
            mtlx_dir = os.path.dirname(lib)

        mat_paths = {}
        mat_ref_prims = {}
        if os.path.exists(lib):
            mat_paths, mat_ref_prims = read_material_library(lib)

        self._session = AssetSession(
            asset_name=name,
            geo_layer_path=geo,
            material_library_path=lib,
            mtlx_dir=mtlx_dir,
            material_paths=mat_paths,
            material_ref_prims=mat_ref_prims,
            mtl_layer_path="",
        )
        self.accept()

    def _accept_load(self):
        mtl_path = self._load_mtl_edit.text().strip()
        geo = self._load_geo_edit.text().strip()
        asset_name = self._load_asset_name.text().strip()
        lib_path = self._load_lib_edit.text().strip()

        if not mtl_path:
            QtWidgets.QMessageBox.warning(self, "Asset Session", "MTL layer path is required.")
            return
        if not os.path.exists(mtl_path):
            QtWidgets.QMessageBox.warning(self, "Asset Session", f"MTL layer not found:\n{mtl_path}")
            return
        if not asset_name:
            QtWidgets.QMessageBox.warning(self, "Asset Session", "Could not detect asset name from MTL layer.")
            return

        mat_paths = {}
        mat_ref_prims = {}
        mtlx_dir = ""
        if lib_path and os.path.exists(lib_path):
            mat_paths, mat_ref_prims = read_material_library(lib_path)
            mtlx_dir = os.path.dirname(lib_path)

        self._session = AssetSession(
            asset_name=asset_name,
            geo_layer_path=geo,
            material_library_path=lib_path,
            mtlx_dir=mtlx_dir,
            material_paths=mat_paths,
            material_ref_prims=mat_ref_prims,
            mtl_layer_path=mtl_path,
        )
        self.accept()

    def get_session(self) -> AssetSession:
        return self._session
