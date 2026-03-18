"""QuiltiX plugin — Prism2 asset browser integration.

Adds a "Prism" menu to QuiltiX with:
  - **Load Asset from Prism** — opens the Prism Product Browser, lets the user
    pick an asset product, resolves the latest version, and feeds it into the
    asset-session dialog with all fields pre-filled.
  - **Export MTL to Prism** — exports the current MTL layer as the next version
    of a selected Prism product.
  - **Save Materials to Prism** — saves toggled materials as Prism asset products
    and updates the material library version when structure changes.

Set ``QUILTIX_PLUGIN_PATHS`` to include this directory to activate.

IMPORTANT: All qtpy / pxr imports are deferred (inside functions) because this
module is loaded by the plugin manager *before* pxr is imported.  Importing
PySide6 at module level would break pxr DLL initialisation on Windows.
"""

import logging
import os
import tempfile
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

        prism_menu.addSeparator()

        act_save_mats = QAction("Save Materials to Prism...", editor)
        act_save_mats.triggered.connect(self._on_save_materials)
        prism_menu.addAction(act_save_mats)

        prism_menu.addSeparator()

        act_open_mat_folder = QAction("Open Materials Folder", editor)
        act_open_mat_folder.triggered.connect(self._on_open_materials_folder)
        prism_menu.addAction(act_open_mat_folder)

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

        # Pre-fill the mtlx dir with the resolved Prism materials folder
        if self._last_entity and hasattr(dlg, "_new_mtlx_dir_edit"):
            try:
                bridge = _get_bridge()
                core = bridge.get_core()
                mat_rel = bridge.get_materials_folder_rel(self._last_entity)
                mat_abs = os.path.normpath(os.path.join(core.assetPath, mat_rel))
                dlg._new_mtlx_dir_edit.setText(mat_abs)
            except Exception:
                pass

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

        # Save toggled materials first so the library references are up to date
        saved_mats, lib_updated, mat_errors = self._save_toggled_materials()

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
            version_dir = os.path.dirname(next_path)
            logger.info("Exported MTL layer to Prism: %s", next_path)
        except Exception:
            logger.exception("Failed to export MTL layer")
            QMessageBox.critical(
                self.editor, "Prism",
                "Failed to export MTL layer. Check the log for details.",
            )
            return

        # --- Post-export options dialog ---
        from qtpy.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QLabel, QDialogButtonBox

        dlg = QDialog(self.editor)
        dlg.setWindowTitle("Export MTL — Options")
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel(f"MTL layer exported to:\n{next_path}"))

        if saved_mats:
            lay.addWidget(QLabel(
                "\nMaterials saved:\n" + "\n".join(f"  • {s}" for s in saved_mats)
            ))
        if mat_errors:
            lay.addWidget(QLabel(
                "\nErrors:\n" + "\n".join(f"  • {e}" for e in mat_errors)
            ))

        cb_master_mtl = QCheckBox("Set MTL layer as master")
        cb_master_mtl.setChecked(True)
        lay.addWidget(cb_master_mtl)

        cb_save_lib = QCheckBox("Save new version of material library")
        cb_save_lib.setChecked(lib_updated)  # pre-checked if materials were saved
        lay.addWidget(cb_save_lib)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return

        if cb_master_mtl.isChecked():
            try:
                bridge.set_master_version(version_dir)
                logger.info("Master version updated from: %s", version_dir)
            except Exception:
                logger.exception("Failed to update master version")
                QMessageBox.warning(
                    self.editor, "Prism",
                    "Exported successfully but failed to update master version.",
                )

        extra_lib_saved = False
        if cb_save_lib.isChecked() and not lib_updated:
            # Library wasn't already saved by _save_toggled_materials — save now
            materials_folder_rel = bridge.get_materials_folder_rel(self._last_entity)
            core = bridge.get_core()
            if session.mtlx_dir and os.path.isabs(session.mtlx_dir):
                rel = core.entities.getAssetRelPathFromPath(
                    os.path.normpath(session.mtlx_dir)
                )
                if rel:
                    materials_folder_rel = rel
            extra_lib_saved = self._save_library_to_prism(
                bridge, session, materials_folder_rel,
            )

        # Final summary
        summary = [f"MTL layer: {next_path}"]
        if cb_master_mtl.isChecked():
            summary.append("  → set as master")
        if saved_mats:
            summary.append("\nMaterials saved:")
            for s in saved_mats:
                summary.append(f"  • {s}")
        if lib_updated or extra_lib_saved:
            summary.append(f"\nLibrary updated: {session.material_library_path}")
        if mat_errors:
            summary.append("\nErrors:")
            for e in mat_errors:
                summary.append(f"  • {e}")

        QMessageBox.information(self.editor, "Export Summary", "\n".join(summary))

    # ------------------------------------------------------------------
    # Save Materials to Prism
    # ------------------------------------------------------------------
    def _on_save_materials(self):
        from qtpy.QtWidgets import QMessageBox

        session = getattr(self.editor, "_asset_session", None)
        if not session:
            QMessageBox.information(
                self.editor, "Prism",
                "No active asset session.\nLoad an asset first.",
            )
            return

        if not self._last_entity:
            QMessageBox.information(
                self.editor, "Prism",
                "No asset entity context.\n"
                "Load an asset from Prism first so the entity is known.",
            )
            return

        session = self.editor._asset_session
        saved, library_updated, errors = self._save_toggled_materials()

        # Summary
        summary = []
        if saved:
            summary.append("Materials saved:")
            for s in saved:
                summary.append(f"  • {s}")
            summary.append("")  # blank line
            # Show updated paths
            for mat_name in self.editor.material_manager_widget.get_all_materials():
                path = session.material_paths.get(mat_name)
                if path and any(mat_name in s for s in saved):
                    summary.append(f"  {mat_name}: {path}")
        if library_updated:
            summary.append(f"\nLibrary updated: {session.material_library_path}")
        if errors:
            summary.append("\nErrors:")
            for e in errors:
                summary.append(f"  • {e}")

        QMessageBox.information(
            self.editor, "Save Materials",
            "\n".join(summary) if summary else "No materials were checked for saving.",
        )

    def _save_toggled_materials(self):
        """Save all toggled materials to Prism and update the library.

        Returns (saved_materials, library_updated, errors).
        Can be called from both 'Save Materials' and 'Export MTL' flows.
        """
        session = self.editor._asset_session
        bridge = _get_bridge()

        toggled = self.editor.material_manager_widget.get_toggled_materials()
        if not toggled:
            return [], False, []

        # Snapshot the active material graph
        active = self.editor.stage_ctrl.get_active_material()
        if active:
            self.editor._material_xml_cache[active] = (
                self.editor.qx_node_graph.get_mx_xml_data_from_graph()
            )

        # Determine materials folder — use session.mtlx_dir if the user set it
        # in the dialog, otherwise derive from the Prism entity.
        core = bridge.get_core()
        if session.mtlx_dir and os.path.isabs(session.mtlx_dir):
            materials_folder_rel = core.entities.getAssetRelPathFromPath(
                os.path.normpath(session.mtlx_dir)
            )
            if not materials_folder_rel:
                materials_folder_rel = os.path.relpath(
                    session.mtlx_dir, core.assetPath
                ).replace("\\", "/")
        else:
            materials_folder_rel = bridge.get_materials_folder_rel(self._last_entity)

        saved_materials = []
        errors = []

        for mat_name in toggled:
            xml = self.editor._material_xml_cache.get(mat_name, "")
            if not xml:
                errors.append(f"{mat_name}: no data in cache")
                continue

            try:
                mat_asset_rel = materials_folder_rel + "/" + mat_name
                mat_entity = bridge.create_asset(mat_asset_rel)

                # Write .mtlx directly to the Prism version path (same drive)
                target_path = bridge.get_next_version_path(
                    mat_entity, "materialX", extension=".mtlx",
                )
                if not target_path:
                    errors.append(f"{mat_name}: could not determine version path")
                    continue

                version_dir = os.path.dirname(target_path)
                mtlx_path = os.path.join(version_dir, f"{mat_name}.mtlx")
                with open(mtlx_path, "w", encoding="utf-8") as fh:
                    fh.write(xml)

                bridge.set_master_version(version_dir)
                saved_materials.append(f"{mat_name} → {os.path.basename(version_dir)}")

                session.material_paths[mat_name] = mtlx_path
                session.material_ref_prims[mat_name] = (
                    f"/MaterialX/Materials/{mat_name}"
                )

            except Exception as exc:
                logger.exception("Failed to save material %s", mat_name)
                errors.append(f"{mat_name}: {exc}")

        # Uncheck saved materials
        for mat_name in toggled:
            if any(mat_name in s for s in saved_materials):
                self.editor.material_manager_widget.set_save_toggle(mat_name, False)

        # Update library if materials were saved
        library_updated = False
        if saved_materials:
            library_updated = self._save_library_to_prism(
                bridge, session, materials_folder_rel,
            )

        return saved_materials, library_updated, errors

    # ------------------------------------------------------------------
    # Open Materials Folder
    # ------------------------------------------------------------------
    def _on_open_materials_folder(self):
        from qtpy.QtWidgets import QMessageBox

        session = getattr(self.editor, "_asset_session", None)

        # Prefer session.mtlx_dir (user-editable), fall back to entity-derived
        abs_path = None
        if session and session.mtlx_dir and os.path.isabs(session.mtlx_dir):
            abs_path = os.path.normpath(session.mtlx_dir)

        if not abs_path:
            if not self._last_entity:
                QMessageBox.information(
                    self.editor, "Prism",
                    "No asset entity context.\n"
                    "Load an asset from Prism first.",
                )
                return
            try:
                bridge = _get_bridge()
                core = bridge.get_core()
                materials_folder_rel = bridge.get_materials_folder_rel(self._last_entity)
                abs_path = os.path.normpath(
                    os.path.join(core.assetPath, materials_folder_rel)
                )
            except Exception:
                QMessageBox.warning(self.editor, "Prism", "Could not initialise Prism.")
                return

        if not os.path.isdir(abs_path):
            QMessageBox.information(
                self.editor, "Prism",
                f"Materials folder does not exist yet:\n{abs_path}",
            )
            return

        # Open in system file explorer
        import subprocess
        subprocess.Popen(["explorer", abs_path])
        logger.info("Opened materials folder: %s", abs_path)

    # ------------------------------------------------------------------
    # Library save helper
    # ------------------------------------------------------------------
    def _save_library_to_prism(self, bridge, session, materials_folder_rel):
        """Save the material library USDA as a new Prism product version.

        Returns True if the library was saved.
        """
        from QuiltiX import asset_session as amod

        # Determine the library asset — it's the entity whose product contains
        # the current material_library_path.  If we can infer the library asset
        # name from the existing path, use it; otherwise use "materialLibrary".
        lib_asset_name = "materialLibrary"
        if session.material_library_path:
            # e.g. .../PALACIO_SUR_INT/Export/materialLibrary/v0001/file.usda
            # The asset name is the component before "Export"
            parts = session.material_library_path.replace("\\", "/").split("/")
            for i, p in enumerate(parts):
                if p.lower() == "export" and i > 0:
                    lib_asset_name = parts[i - 1]
                    break

        lib_asset_rel = materials_folder_rel.rsplit("/", 1)[0] + "/" + lib_asset_name
        lib_entity = bridge.create_asset(lib_asset_rel)

        # Ensure all material ref prims are current
        for name in self.editor.material_manager_widget.get_all_materials():
            if name not in session.material_ref_prims:
                session.material_ref_prims[name] = f"/MaterialX/Materials/{name}"

        try:
            # Get the next version path directly on the target drive so that
            # write_material_library can compute correct relative .mtlx paths.
            lib_filename = f"{session.asset_name}_{lib_asset_name}.usda"
            target_path = bridge.get_next_version_path(
                lib_entity, "materialLibrary", extension=".usda",
            )
            if not target_path:
                logger.warning("Could not determine library version path")
                return False

            version_dir = os.path.dirname(target_path)
            # Write library directly to the version dir (correct drive for relpath)
            lib_path = os.path.join(version_dir, lib_filename)
            amod.write_material_library(
                lib_path,
                session.material_paths,
                session.material_ref_prims,
            )

            bridge.set_master_version(version_dir)
            session.material_library_path = lib_path
            logger.info("Library saved to Prism: %s", lib_path)
            return True
        except Exception:
            logger.exception("Failed to save material library")

        return False


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
