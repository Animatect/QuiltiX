"""
Asset workflow utilities for QuiltiX.

Handles reading/writing material libraries and MTL layers that follow the
studio's USD layering convention:
  - materialLibrary.usda  — Scope "mtl" aggregating .mtlx references
  - usdlayer_mtl.usda     — standalone 'over' opinions: mtl scope + bindings
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Tuple

from pxr import Sdf, Usd, UsdShade, UsdGeom


@dataclass
class AssetSession:
    asset_name: str = ""
    geo_layer_path: str = ""
    material_library_path: str = ""
    mtlx_dir: str = ""
    # name -> absolute .mtlx path (populated from library on load, updated on save)
    material_paths: Dict[str, str] = field(default_factory=dict)
    # name -> reference prim path inside the .mtlx (e.g. "/MaterialX/Materials/USD_Default")
    # preserves original ref target so re-saving doesn't break existing libraries
    material_ref_prims: Dict[str, str] = field(default_factory=dict)
    # path to an existing MTL layer to restore assignments from on load
    mtl_layer_path: str = ""


# ---------------------------------------------------------------------------
# Layer introspection helpers
# ---------------------------------------------------------------------------

def detect_asset_name(layer_path: str) -> str:
    """Return the defaultPrim name from a USD layer file."""
    layer = Sdf.Layer.FindOrOpen(layer_path)
    return (layer.defaultPrim or "") if layer else ""


def read_material_library(library_path: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Parse a material library USDA.
    Returns (mat_paths, mat_ref_prims):
      mat_paths:     {mat_name: absolute_mtlx_path}
      mat_ref_prims: {mat_name: ref_prim_path}  e.g. "/MaterialX/Materials/USD_Default"

    Expected structure:
        def Scope "mtl" {
            def "MatName" (prepend references = @rel/path.mtlx@</MaterialX/Materials/X>) {}
        }
    """
    layer = Sdf.Layer.FindOrOpen(library_path)
    if not layer:
        return {}, {}

    lib_dir = os.path.dirname(os.path.abspath(library_path))
    mat_paths: Dict[str, str] = {}
    mat_ref_prims: Dict[str, str] = {}

    for root_spec in layer.rootPrims:
        for mat_spec in root_spec.nameChildren.values():
            refs = mat_spec.referenceList.prependedItems
            if refs:
                ref = refs[0]
                asset_path = ref.assetPath
                if asset_path:
                    if not os.path.isabs(asset_path):
                        asset_path = os.path.normpath(
                            os.path.join(lib_dir, asset_path)
                        )
                    mat_paths[mat_spec.name] = asset_path
                    # Capture the reference prim path (e.g. /MaterialX/Materials/USD_Default)
                    if ref.primPath:
                        mat_ref_prims[mat_spec.name] = str(ref.primPath)

    return mat_paths, mat_ref_prims


def read_mtl_layer_assignments(mtl_layer_path: str) -> Dict[str, str]:
    """
    Parse a MTL layer USDA.
    Returns {absolute_prim_path: mat_name}.

    Finds every prim spec that carries a material:binding relationship and
    extracts the material name from the bound path's last component.
    """
    layer = Sdf.Layer.FindOrOpen(mtl_layer_path)
    if not layer:
        return {}

    assignments: Dict[str, str] = {}

    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if not isinstance(spec, Sdf.PrimSpec):
            return
        if "material:binding" not in spec.relationships:
            return
        rel = spec.relationships["material:binding"]
        targets = (
            list(rel.targetPathList.explicitItems)
            or list(rel.targetPathList.prependedItems)
            or list(rel.targetPathList.addedItems)
        )
        if targets:
            mat_name = str(targets[0]).split("/")[-1]
            assignments[str(path)] = mat_name

    layer.Traverse(Sdf.Path("/"), visit)
    return assignments


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def write_material_library(
    library_path: str,
    mat_paths: Dict[str, str],
    mat_ref_prims: Dict[str, str] = None,
):
    """Write/overwrite a material library USDA.

    mat_ref_prims: optional {mat_name: prim_path_inside_mtlx}.
      When provided, the reference target uses that prim path instead of
      assuming </MaterialX/Materials/{mat_name}>.  This preserves existing
      libraries where the internal material node is named differently
      (e.g. USD_Default).
    """
    mat_ref_prims = mat_ref_prims or {}
    lib_dir = os.path.dirname(os.path.abspath(library_path))
    os.makedirs(lib_dir, exist_ok=True)

    lines = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "mtl"',
        "    framesPerSecond = 24",
        "    metersPerUnit = 1",
        "    timeCodesPerSecond = 24",
        '    upAxis = "Y"',
        ")",
        "",
        'def Scope "mtl"',
        "{",
    ]

    for mat_name, mtlx_path in sorted(mat_paths.items()):
        rel = os.path.relpath(mtlx_path, lib_dir).replace("\\", "/")
        ref_prim = mat_ref_prims.get(mat_name, f"/MaterialX/Materials/{mat_name}")
        lines += [
            f'    def "{mat_name}" (',
            f"        prepend references = @{rel}@<{ref_prim}>",
            f"    )",
            f"    {{",
            f"    }}",
            "",
        ]

    lines += ["}", ""]

    with open(library_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def export_mtl_layer(
    asset_name: str,
    material_library_path: str,
    assignments: Dict[str, str],
    output_path: str,
    geo_meta: dict = None,
):
    """
    Write the standalone MTL layer USDA.

    assignments: {abs_prim_path: mat_name}
      e.g. {"/Columna_sur_1/geo/render/Columna_sur_1/Mesh": "Estuco_Muro"}

    The emitted file contains:
      - 'over "{asset_name}"' as root
      - 'def Scope "mtl"' referencing the material library
      - Nested 'over' prim hierarchy for every assigned prim
    """
    meta = geo_meta or {}
    fps = meta.get("framesPerSecond", 24)
    mpu = meta.get("metersPerUnit", 1)
    tcs = meta.get("timeCodesPerSecond", 24)
    upaxis = meta.get("upAxis", "Y")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    lib_rel = os.path.relpath(material_library_path, output_dir).replace("\\", "/")

    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{asset_name}"',
        f"    framesPerSecond = {fps}",
        f"    metersPerUnit = {mpu}",
        f"    timeCodesPerSecond = {tcs}",
        f'    upAxis = "{upaxis}"',
        ")",
        "",
        f'over "{asset_name}"',
        "{",
        f'    def Scope "mtl" (',
        f"        prepend references = @{lib_rel}@",
        f"    )",
        f"    {{",
        f"    }}",
    ]

    # Filter assignments that belong to this asset
    asset_assignments = {
        p: m for p, m in assignments.items()
        if p.strip("/").split("/")[0] == asset_name
    }

    if asset_assignments:
        lines.append("")
        lines.extend(_build_over_block(asset_name, asset_assignments, indent=4))

    lines += ["}", ""]

    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _build_over_block(asset_name: str, assignments: Dict[str, str], indent: int) -> list:
    """
    Build the nested 'over' lines for the given assignments.
    Returns a list of USDA text lines.
    """
    # Build a path tree: tree[part] = {} of children
    tree: dict = {}
    for path_str in assignments:
        parts = path_str.strip("/").split("/")[1:]  # skip asset_name
        node = tree
        for part in parts:
            node = node.setdefault(part, {})

    def emit(node: dict, path_prefix: str, depth: int) -> list:
        out = []
        pad = " " * (indent + depth * 4)
        for name in sorted(node):
            children = node[name]
            full_path = path_prefix + "/" + name
            mat_name = assignments.get(full_path)

            if mat_name:
                out += [
                    f'{pad}over "{name}" (',
                    f'{pad}    prepend apiSchemas = ["MaterialBindingAPI"]',
                    f"{pad})",
                    f"{pad}{{",
                    f"{pad}    rel material:binding = </{asset_name}/mtl/{mat_name}>",
                ]
                if children:
                    out.extend(emit(children, full_path, depth + 1))
                out.append(f"{pad}}}")
            else:
                out += [f'{pad}over "{name}"', f"{pad}{{"]
                out.extend(emit(children, full_path, depth + 1))
                out.append(f"{pad}}}")
        return out

    root_prefix = "/" + asset_name
    return emit(tree, root_prefix, 0)
