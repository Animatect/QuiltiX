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
