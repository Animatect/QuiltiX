"""QuiltiX plugin — Prism2 asset browser integration.

Adds a "Prism" menu to QuiltiX with:
  - **Load Asset from Prism** — opens the Prism Product Browser, lets the user
    pick an asset product, resolves the latest version, and feeds it into the
    asset-session dialog with all fields pre-filled.
  - **Export MTL to Prism** — exports the current MTL layer as the next version
    of a selected Prism product.

Set ``QUILTIX_PLUGIN_PATHS`` to include this directory to activate.

IMPORTANT: All qtpy / pxr imports are deferred (inside functions) because this
module is loaded by the plugin manager *before* pxr is imported.  Importing
PySide6 at module level would break pxr DLL initialisation on Windows.
"""

import logging
import os
from typing import TYPE_CHECKING

from QuiltiX import qx_plugin

if TYPE_CHECKING:
    from QuiltiX.quiltix import QuiltiXWindow

logger = logging.getLogger(__name__)

# Lazy-import the bridge so Prism sys.path setup only happens on first use.
_bridge = None

SUPPORTED_EXTENSIONS = {".usd", ".usda", ".usdc", ".usdz"}


def _get_bridge():
    global _bridge
    if _bridge is None:
        import importlib.util
        _here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "prism_bridge", os.path.join(_here, "prism_bridge.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _bridge = mod
    return _bridge


class PrismAssetIntegration:
    """Wires Prism product browsing into a QuiltiX editor instance."""

    def __init__(self, editor: "QuiltiXWindow"):
        from qtpy.QtWidgets import QAction  # deferred import

        self.editor = editor
        self._last_entity = None

        # --- Prism menu ---------------------------------------------------
        prism_menu = editor.menuBar().addMenu("&Prism")

        act_load = QAction("Load Asset from Prism...", editor)
        act_load.triggered.connect(self._on_load_asset)
        prism_menu.addAction(act_load)

        act_export = QAction("Export MTL to Prism...", editor)
        act_export.triggered.connect(self._on_export_mtl)
        prism_menu.addAction(act_export)

    # ------------------------------------------------------------------
    # Load asset
    # ------------------------------------------------------------------
    def _on_load_asset(self):
        from qtpy.QtWidgets import QMessageBox

        try:
            bridge = _get_bridge()
        except Exception:
            logger.exception("Failed to initialise Prism bridge")
            QMessageBox.warning(
                self.editor, "Prism",
                "Could not initialise Prism.\n\n"
                "Make sure Prism2 is installed and PRISM_ROOT is set.",
            )
            return

        pb = bridge.open_product_browser(parent=self.editor)

        # Connect double-click (productPathSet) to our handler
        pb.productPathSet.connect(lambda path: self._on_product_selected(pb, path))
        pb.show()

    def _on_product_selected(self, product_browser, path):
        """Called when the user double-clicks a version in the Product Browser."""
        from qtpy.QtWidgets import QMessageBox

        product_browser.close()

        if not path:
            return

        # If path is a directory, find the first supported USD file inside it
        if os.path.isdir(path):
            for f in sorted(os.listdir(path)):
                if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS:
                    path = os.path.join(path, f)
                    break
            else:
                QMessageBox.warning(
                    self.editor, "Prism",
                    f"No USD files found in:\n{path}",
                )
                return

        if not os.path.isfile(path):
            QMessageBox.warning(self.editor, "Prism", f"File not found:\n{path}")
            return

        # Remember entity context for later exports
        try:
            self._last_entity = product_browser.getCurrentEntity()
        except Exception:
            self._last_entity = None

        logger.info("Prism product selected: %s", path)

        # Feed the selected asset file into the asset session dialog,
        # pre-filling the "Asset File" field so discovery runs automatically.
        self._open_asset_session_with_file(path)

    def _open_asset_session_with_file(self, asset_path):
        """Open the Asset Session dialog with *asset_path* pre-filled."""
        from QuiltiX.asset_session_dialog import AssetSessionDialog

        dlg = AssetSessionDialog(self.editor)

        # Pre-fill the asset file field and trigger auto-discovery
        if hasattr(dlg, "_new_asset_file_edit"):
            dlg._new_asset_file_edit.setText(asset_path)
            dlg._auto_fill_from_asset(asset_path)

        if dlg.exec_() and dlg._session:
            self.editor._enter_asset_mode(dlg._session)

    # ------------------------------------------------------------------
    # Export MTL
    # ------------------------------------------------------------------
    def _on_export_mtl(self):
        from qtpy.QtWidgets import QMessageBox

        session = getattr(self.editor, "_asset_session", None)
        if not session:
            QMessageBox.information(
                self.editor, "Prism",
                "No active asset session.\nLoad an asset first.",
            )
            return

        try:
            bridge = _get_bridge()
        except Exception:
            logger.exception("Failed to initialise Prism bridge")
            QMessageBox.warning(self.editor, "Prism", "Could not initialise Prism.")
            return

        if not self._last_entity:
            # Let the user pick an entity/product via the browser
            pb = bridge.open_product_browser(parent=self.editor)
            pb.productPathSet.connect(lambda path: self._do_export_mtl(pb))
            pb.show()
            return

        self._do_export_mtl_with_entity(self._last_entity)

    def _do_export_mtl(self, product_browser):
        entity = product_browser.getCurrentEntity()
        product_browser.close()
        if entity:
            self._do_export_mtl_with_entity(entity)

    def _do_export_mtl_with_entity(self, entity):
        from qtpy.QtWidgets import QMessageBox

        session = self.editor._asset_session
        bridge = _get_bridge()

        # Determine product name from the MTL layer convention
        product_name = "usdlayer_mtl"

        next_path = bridge.get_next_version_path(
            entity, product_name, extension=".usda",
        )
        if not next_path:
            QMessageBox.warning(
                self.editor, "Prism",
                "Could not determine the next version path.",
            )
            return

        # Use the existing export logic
        try:
            self.editor._export_mtl_layer_to_path(next_path)
            logger.info("Exported MTL layer to Prism: %s", next_path)
        except Exception:
            logger.exception("Failed to export MTL layer")
            QMessageBox.critical(
                self.editor, "Prism",
                "Failed to export MTL layer. Check the log for details.",
            )
            return

        # Prompt to set as master version
        version_dir = os.path.dirname(next_path)
        reply = QMessageBox.question(
            self.editor, "Prism",
            f"MTL layer exported to:\n{next_path}\n\n"
            "Set this version as master?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            try:
                bridge.get_core().products.updateMasterVersion(version_dir)
                logger.info("Master version updated from: %s", version_dir)
            except Exception:
                logger.exception("Failed to update master version")
                QMessageBox.warning(
                    self.editor, "Prism",
                    "Exported successfully but failed to update master version.",
                )


# ======================================================================
# QuiltiX plugin interface
# ======================================================================

@qx_plugin.hookimpl
def after_ui_init(editor: "QuiltiXWindow"):
    editor.prism_integration = PrismAssetIntegration(editor)


def plugin_name() -> str:
    return "Prism Asset Browser"


def is_valid() -> bool:
    """Plugin is valid if Prism2 is installed."""
    root = os.environ.get("PRISM_ROOT", r"C:\Program Files\Prism2")
    return os.path.isfile(os.path.join(root, "Scripts", "PrismCore.py"))
