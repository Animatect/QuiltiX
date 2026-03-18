"""Prism2 bridge — initialise PrismCore and expose product-browser helpers.

This module is intentionally self-contained so it can be imported independently
of the QuiltiX plugin system for testing or scripting.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_core = None  # singleton PrismCore instance

PRISM_ROOT = os.environ.get(
    "PRISM_ROOT", r"C:\Program Files\Prism2"
)


def _ensure_prism_on_path():
    """Add Prism's Scripts and PythonLibs to sys.path if not already present."""
    scripts = os.path.join(PRISM_ROOT, "Scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)

    libs_root = os.environ.get("PRISM_LIBS", PRISM_ROOT)

    cp_libs = os.path.join(libs_root, "PythonLibs", "CrossPlatform")
    if cp_libs not in sys.path:
        sys.path.append(cp_libs)

    py_ver = f"Python{sys.version_info.major}{sys.version_info.minor}"
    ver_libs = os.path.join(libs_root, "PythonLibs", py_ver)
    if os.path.isdir(ver_libs) and ver_libs not in sys.path:
        sys.path.append(ver_libs)

    py3_libs = os.path.join(libs_root, "PythonLibs", "Python3")
    if py3_libs not in sys.path:
        sys.path.append(py3_libs)


def get_core():
    """Return a singleton PrismCore instance (lazy-init)."""
    global _core
    if _core is not None:
        return _core

    _ensure_prism_on_path()

    import PrismCore  # noqa: E402

    _core = PrismCore.PrismCore(app="Standalone", prismArgs=["noUI", "noProjectBrowser"])
    # startup() triggers appPlugin.startup() → changeProject() which loads
    # the user's current project and sets core.projectName.
    _core.startup()

    # If no project was loaded (no current project in prefs), ensure the
    # attribute exists so ProductBrowser doesn't crash.  The user can pick
    # a project through the browser itself.
    if not hasattr(_core, "projectName"):
        curPrj = _core.getConfig("globals", "current project")
        if curPrj:
            _core.changeProject(curPrj)
        else:
            _core.projectName = ""
            _core.projectPath = ""

    logger.info("Prism core initialised — project: %s", getattr(_core, "projectName", "?"))
    return _core


def open_product_browser(parent=None):
    """Open the Prism ProductBrowser dialog and return it.

    The caller should connect to ``productBrowser.productPathSet`` or
    call ``productBrowser.exec_()`` and inspect selection afterwards.
    """
    core = get_core()

    from ProjectScripts import ProductBrowser  # noqa: E402

    pb = ProductBrowser.ProductBrowser(core=core)
    if parent is not None:
        core.parentWindow(pb, parent)

    # In Standalone mode Prism doesn't wire double-click → loadVersion.
    # Connect it ourselves so the user can double-click a version row.
    pb.tw_versions.doubleClicked.connect(pb.loadVersion)

    return pb


def get_latest_product_path(entity, product_name):
    """Resolve the latest version filepath for *product_name* under *entity*.

    Parameters
    ----------
    entity : dict
        Entity context dict (as returned by ProductBrowser.getCurrentEntity).
    product_name : str
        Product identifier (e.g. ``"ASSET"``).

    Returns
    -------
    str or None
        Absolute path to the preferred file, or ``None``.
    """
    core = get_core()
    return core.products.getLatestVersionpathFromProduct(
        product=product_name,
        entity=entity,
    )


def get_next_version_path(entity, product_name, extension=".usda"):
    """Build the filepath for the *next* version of a product.

    Uses Prism's own ``generateProductPath`` so the path follows the
    project's template structure exactly.

    Returns
    -------
    str or None
    """
    core = get_core()
    output_path = core.products.generateProductPath(
        entity=entity,
        task=product_name,
        extension=extension,
    )
    if not output_path:
        return None

    # Ensure the version directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    return output_path


def set_master_version(version_dir):
    """Promote *version_dir* to master.

    Uses ``getLatestVersionFromPath`` to resolve the version, then
    ``updateMasterVersion`` with a file path to create the master copy.
    Falls back to manual copy if Prism's method fails.
    """
    import shutil

    core = get_core()

    # Find the first product file in the version dir
    filepath = None
    if os.path.isdir(version_dir):
        for f in sorted(os.listdir(version_dir)):
            full = os.path.join(version_dir, f)
            if os.path.isfile(full) and not f.startswith("versioninfo"):
                filepath = full
                break

    if not filepath:
        logger.warning("set_master_version: no files in %s", version_dir)
        return

    # Try Prism's updateMasterVersion first
    try:
        core.products.updateMasterVersion(filepath)
        logger.info("Master version set via Prism: %s", filepath)
        return
    except Exception:
        logger.debug("updateMasterVersion failed, using fallback", exc_info=True)

    # Fallback: manually copy files to a 'master' sibling directory
    # version_dir is like .../Export/product_name/v0001/
    # master dir is      .../Export/product_name/master/
    product_dir = os.path.dirname(version_dir)
    master_dir = os.path.join(product_dir, "master")

    if os.path.isdir(master_dir):
        shutil.rmtree(master_dir)
    os.makedirs(master_dir, exist_ok=True)

    for f in os.listdir(version_dir):
        src = os.path.join(version_dir, f)
        dst = os.path.join(master_dir, f)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
        elif os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)

    logger.info("Master version set via fallback copy: %s → %s", version_dir, master_dir)


def create_asset(asset_path_relative):
    """Create a Prism asset if it doesn't exist yet.

    Parameters
    ----------
    asset_path_relative : str
        Asset path relative to core.assetPath (e.g. ``"Environment/MyAsset"``).

    Returns
    -------
    dict
        Entity dict ``{"type": "asset", "asset_path": ...}``, or None on error.
    """
    core = get_core()
    entity = {"type": "asset", "asset_path": asset_path_relative}
    result = core.entities.createEntity(entity, silent=True)
    if result and "error" not in result:
        return result["entity"]
    return entity  # return the dict even if it already existed


def get_entity_for_asset_name(asset_name, parent_folder_rel=""):
    """Look up or build an entity dict for an asset by name.

    If *parent_folder_rel* is given, the asset is assumed to live under that
    folder relative to ``core.assetPath``.
    """
    core = get_core()
    # Try Prism's own lookup first
    full_path = core.entities.getAssetPathFromAssetName(asset_name)
    if full_path and os.path.isdir(full_path):
        rel = core.entities.getAssetRelPathFromPath(full_path)
        return {"type": "asset", "asset_path": rel}

    # Build from parent folder
    if parent_folder_rel:
        rel = parent_folder_rel.rstrip("/\\") + "/" + asset_name
    else:
        rel = asset_name
    return {"type": "asset", "asset_path": rel}


def get_materials_folder_rel(asset_entity):
    """Derive the relative path for the 'materials' sibling folder.

    The materials folder lives next to the ``ASSETS`` folder in the parent
    asset group, e.g.::

        Props/PalacioSurInt/ASSETS/Columna_sur_1  ← asset
        Props/PalacioSurInt/materials              ← materials folder

    If no ``ASSETS`` component is found, falls back to the parent of the
    asset path.
    """
    asset_path = asset_entity.get("asset_path", "").replace("\\", "/")
    parts = asset_path.split("/")

    # Walk backwards to find the 'ASSETS' component and go one level above it
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].upper() == "ASSETS":
            parent = "/".join(parts[:i])
            return parent + "/materials" if parent else "materials"

    # Fallback: one level up from the asset
    parent = os.path.dirname(asset_path)
    return parent + "/materials" if parent else "materials"


def ingest_files(files, entity, product_name, comment=None):
    """Ingest *files* into *product_name* under *entity* as the next version.

    Ensures the product exists, creates a properly versioned directory, copies
    files, writes version info, and returns paths for master-version promotion.

    Returns
    -------
    tuple(str, str)
        (version_dir, version_name) or (None, None) on failure.
    """
    core = get_core()

    # Ensure the product directory exists
    core.products.createProduct(entity, product_name)

    version = core.products.getNextAvailableVersion(entity=entity, product=product_name)
    output_path = core.products.generateProductPath(
        entity=entity,
        task=product_name,
        extension=os.path.splitext(files[0])[1] if files else ".usda",
        version=version,
    )
    if not output_path:
        return None, None

    version_dir = os.path.dirname(output_path)
    os.makedirs(version_dir, exist_ok=True)

    import shutil
    for f in files:
        dst = os.path.join(version_dir, os.path.basename(f))
        if os.path.isdir(f):
            shutil.copytree(f, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(f, dst)

    return version_dir, version
