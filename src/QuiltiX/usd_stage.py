import os
import pathlib
import logging

from qtpy import QtCore # type: ignore

from pxr import Usd, UsdLux, Sdf, Tf, UsdGeom,  UsdShade, Gf  # noqa: E402 # type: ignore
from pxr.Usdviewq._usdviewq import Utils # type: ignore

import MaterialX as mx

from QuiltiX import mx_node
# TODO: decouple from QxNode
from QuiltiX.qx_node import QxNode


logger = logging.getLogger(__name__)


def set_pxr_mtlx_stdlib_search_paths():
    """Usd searches in PXR_MTLX_STDLIB_SEARCH_PATHS for the MaterialX standard library of nodes.
    If it is not set, find the stdlib and set PXR_MTLX_STDLIB_SEARCH_PATHS.
    """
    if not os.getenv("PXR_MTLX_STDLIB_SEARCH_PATHS"):
        os.environ["PXR_MTLX_STDLIB_SEARCH_PATHS"] = ";".join(mx_node.get_mx_stdlib_paths())

    logger.info("stdlib loaded from: %s" % os.environ["PXR_MTLX_STDLIB_SEARCH_PATHS"])


def get_stage_from_file(path):
    if os.path.splitext(path)[1] == ".abc":
        stage = create_empty_stage()
        add_layer_to_stage_root(stage, path)
    else:
        stage = Usd.Stage.Open(path, Usd.Stage.LoadAll)

    return stage


def create_empty_stage():
    return Usd.Stage.CreateInMemory()


def create_stage_with_hdri(hdri_file_path, hdri_parent_path="/lights"):
    stage = create_empty_stage()
    hdri_name = pathlib.Path(hdri_file_path).stem
    hdri_stage_path = "/".join((hdri_parent_path, hdri_name))
    hdri = UsdLux.DomeLight.Define(stage, Sdf.Path(hdri_stage_path))
    hdri.CreateTextureFileAttr(hdri_file_path)
    hdri.CreateTextureFormatAttr("latlong")
    prim = stage.GetPrimAtPath(Sdf.Path(hdri_stage_path))
    attr = prim.CreateAttribute("karma:light:renderlightgeo", Sdf.ValueTypeNames.Bool)
    attr.Set(True)
    # stage.SetDefaultPrim(hdri.GetPrim())
    return stage


def add_layer_to_stage_root(stage, layer_path):
    root = stage.GetRootLayer()
    root.subLayerPaths.insert(0, layer_path)


class MxStageController(QtCore.QObject):

    signal_stage_changed = QtCore.Signal(object)
    signal_stage_updated = QtCore.Signal()

    def __init__(
        self,
        editor=None,
        stage=None,
    ):
        super(MxStageController, self).__init__()
        self.editor = editor
        self._material_layers = {}   # manager_name -> Sdf.Layer
        self._active_material = None
        self._material_mx_names = {}  # manager_name -> actual MaterialX element name
        self._looks_scope = ""        # optional USD path prefix for material reference prims
        self._geometry_path = None    # currently loaded geometry sublayer path

        # Create a persistent working stage — geometry is a swappable sublayer at the bottom,
        # so loading new geometry never tears down material layers or assignments.
        self.stage = Usd.Stage.CreateInMemory()
        self.stage_root = self.stage.GetRootLayer()

        in_memory = os.getenv("QUILTIX_WRITE_TMP_TO_DISK", "0") == "0"
        if in_memory:
            self._assignments_layer = Sdf.Layer.CreateAnonymous("_tmp_quiltix_assignments.usd")
            self._assignments_idf = self._assignments_layer.identifier
        else:
            self._assignments_idf = os.path.join(os.environ["TEMP"], "_tmp_quiltix_assignments.usd")
            self._assignments_layer = Sdf.Layer.CreateNew(self._assignments_idf)

        self.stage_root.subLayerPaths.insert(0, self._assignments_idf)
        # Use assignments layer as the default edit target so all interactive edits
        # (visibility, material bindings, etc.) are written there instead of the
        # session layer, which gets cleared on every material XML refresh.
        self.stage.SetEditTarget(Usd.EditTarget(self._assignments_layer))

    def set_geometry(self, path):
        """Swap the geometry sublayer without disturbing materials, assignments, or lights."""
        if self._geometry_path and self._geometry_path in self.stage_root.subLayerPaths:
            self.stage_root.subLayerPaths.remove(self._geometry_path)
        if path:
            self.stage_root.subLayerPaths.append(path)
        self._geometry_path = path
        # Emit signal_stage_changed so the view widget resets camera / BBox
        self.signal_stage_changed.emit(self.stage)

    def clear_session(self):
        """Reset all material state (layers, cache, assignments) without touching geometry."""
        for layer in list(self._material_layers.values()):
            if layer.identifier in self.stage_root.subLayerPaths:
                self.stage_root.subLayerPaths.remove(layer.identifier)
        self._material_layers = {}
        self._active_material = None
        self._material_mx_names = {}
        self._assignments_layer.Clear()
        # Re-insert assignments layer if it was somehow removed
        if self._assignments_idf not in self.stage_root.subLayerPaths:
            self.stage_root.subLayerPaths.insert(0, self._assignments_idf)

    def set_stage(self, stage):
        """Legacy helper: extracts the file path from an opened stage and calls set_geometry."""
        geo_path = stage.GetRootLayer().realPath
        if not geo_path:
            # .abc or anonymous-root stages: check sublayers
            for sl in stage.GetRootLayer().subLayerPaths:
                if sl:
                    geo_path = sl
                    break
        self.set_geometry(geo_path or None)

    def get_all_geo_prims(self):
        return Utils._GetAllPrimsOfType(self.stage, Tf.Type.Find(UsdGeom.Gprim))

    def add_material_layer(self, name):
        """Create an isolated Sdf.Layer for a named material and insert it into the stage."""
        in_memory = os.getenv("QUILTIX_WRITE_TMP_TO_DISK", "0") == "0"
        if in_memory:
            layer = Sdf.Layer.CreateAnonymous(f"_tmp_quiltix_mat_{name}.mtlx")
        else:
            idf = os.path.join(os.environ["TEMP"], f"_tmp_quiltix_mat_{name}.mtlx")
            layer = Sdf.Layer.CreateNew(idf)

        self._material_layers[name] = layer
        # Insert after assignments layer so geometry layers stay at the bottom
        self.stage_root.subLayerPaths.insert(1, layer.identifier)
        return layer

    def remove_material_layer(self, name):
        """Remove a material's layer from the stage."""
        if name not in self._material_layers:
            return
        layer = self._material_layers.pop(name)
        if layer.identifier in self.stage_root.subLayerPaths:
            self.stage_root.subLayerPaths.remove(layer.identifier)
        if self._active_material == name:
            self._active_material = next(iter(self._material_layers), None)
        self.signal_stage_updated.emit()

    def set_active_material(self, name):
        """Set which material is currently being authored in the node graph."""
        self._active_material = name

    def get_active_material(self):
        return self._active_material

    def set_looks_scope(self, path):
        """Set an optional USD scope path under which reference material prims are placed.

        E.g. '/World/Looks' → materials become addressable as /World/Looks/{name}
        while their definitions stay at /MaterialX/Materials/{name}.
        Pass an empty string to disable (binds directly from /MaterialX/Materials/).
        """
        self._looks_scope = path.strip().rstrip("/")

    def get_looks_scope(self):
        return self._looks_scope

    def apply_first_material_to_all_prims(self):
        if not self._active_material:
            return
        prims = self.get_all_geo_prims()
        if prims:
            self.apply_material_to_prims(self._active_material, prims)

    def refresh_mx_file(self, mx_data, emit=True):
        if not mx_data:
            return

        # Determine target material — infer from XML if no active material yet (backward compat)
        material_name = self._active_material
        if not material_name:
            tmp_doc = mx.createDocument()
            try:
                mx.readFromXmlString(tmp_doc, mx_data)
            except Exception:
                return
            materials = tmp_doc.getMaterials()
            if not materials:
                return
            material_name = materials[0].getName()
            self._active_material = material_name

        # Create the layer for this material if it doesn't exist yet
        if material_name not in self._material_layers:
            self.add_material_layer(material_name)

        self._material_layers[material_name].ImportFromString(mx_data)

        # Record the actual MaterialX element name so update_parameter can find the right prim
        try:
            tmp = mx.createDocument()
            mx.readFromXmlString(tmp, mx_data)
            materials = tmp.getMaterials()
            if materials:
                self._material_mx_names[material_name] = materials[0].getName()
        except Exception:
            pass

        if emit:
            self.signal_stage_updated.emit()

    def update_parameter(self, qx_node, property_name, property_value):
        property_name = QxNode.get_mx_input_name_from_property_name(qx_node, property_name)

        if not self._active_material:
            return

        # Try the fast path: direct USD attribute set on the material layer.
        mat_layer = self._material_layers.get(self._active_material)
        if self._try_fast_parameter_update(qx_node, property_name, property_value, mat_layer):
            self.signal_stage_updated.emit()
            return

        # Fast path failed — fall back to a full XML reimport of the material
        # layer.  This is the same path taken when switching materials and
        # guarantees the MX file-format plugin re-translates the graph.
        if self.editor:
            xml = self.editor.qx_node_graph.get_mx_xml_data_from_graph()
            if xml:
                self.refresh_mx_file(xml)

    def _try_fast_parameter_update(self, qx_node, property_name, property_value, mat_layer):
        """Attempt to set a single USD attribute directly.  Returns True on success."""
        if qx_node.type_ == "Other.QxGroupNode":
            ng_name = qx_node.name()
            sub_graph = qx_node.get_sub_graph()
            if not sub_graph:
                return False

            in_port_node = sub_graph.get_input_port_nodes()[0]
            out_port = in_port_node.get_output(property_name)
            cports = out_port.connected_ports()
            if not cports:
                return False

            mx_stage_path = f"/MaterialX/NodeGraphs/{ng_name}/" + cports[0].node().name()
            property_name = cports[0].name()
        elif qx_node.current_mx_def.getNodeGroup() in ["material", "pbr", "shader"]:
            mx_elem_name = self._material_mx_names.get(self._active_material, self._active_material)
            mx_stage_path = f"/MaterialX/Materials/{mx_elem_name}"
        else:
            if qx_node.graph.is_root:
                ng_name = "NG_main"
            else:
                ng_name = qx_node.graph.node.name()

            mx_stage_path = f"/MaterialX/NodeGraphs/{ng_name}/" + qx_node.NODE_NAME

        prim = self.stage.GetPrimAtPath(mx_stage_path)
        if not prim.IsValid():
            return False

        usdinput = UsdShade.Shader(prim).GetInput(property_name)
        if not usdinput:
            return False
        attr = usdinput.GetAttr()
        if not attr.IsValid():
            return False

        if type(property_value) in [list, tuple]:
            if len(property_value) == 4:
                property_value = property_value[
                    :3
                ]  # temporary fix, the RGB color picker widgets emits a list of 4 values

            if len(property_value) == 3:
                property_value = Gf.Vec3f(property_value)
            elif len(property_value) == 2:
                property_value = Gf.Vec2f(property_value)

        # Write to the material layer so the edit lives alongside the MX content
        prev_target = self.stage.GetEditTarget()
        if mat_layer:
            self.stage.SetEditTarget(Usd.EditTarget(mat_layer))
        try:
            attr.Set(property_value)
        finally:
            self.stage.SetEditTarget(prev_target)
        return True

    def apply_material_to_prims(self, material_name, prims):
        mx_material_stage_path = "/".join(("", "MaterialX", "Materials", material_name))
        if not self.stage.GetPrimAtPath(mx_material_stage_path).IsValid():
            logger.warning("invalid material: " + mx_material_stage_path)
            return

        prev_target = self.stage.GetEditTarget()
        self.stage.SetEditTarget(Usd.EditTarget(self._assignments_layer))

        # If a looks scope is set, create a reference material prim under that scope
        # so materials appear at the user-specified path in the hierarchy.
        if self._looks_scope:
            bind_path = Sdf.Path(self._looks_scope + "/" + material_name)
            ref_mat = UsdShade.Material.Define(self.stage, bind_path)
            ref_mat.GetPrim().GetReferences().AddInternalReference(mx_material_stage_path)
            material = ref_mat
        else:
            material = UsdShade.Material.Get(self.stage, mx_material_stage_path)

        for prim in prims:
            prim.ApplyAPI(UsdShade.MaterialBindingAPI)
            UsdShade.MaterialBindingAPI(prim).UnbindAllBindings()
            UsdShade.MaterialBindingAPI(prim).Bind(material)
            logger.info("applied material %s to %s" % (material.GetPath(), prim.GetPath()))

        self.stage.SetEditTarget(prev_target)
        self.signal_stage_updated.emit()

    def get_assignments(self):
        """
        Return {abs_prim_path_str: manager_name} from _assignments_layer.
        Reads every prim spec that carries a material:binding relationship.
        The returned material name is the Material Manager name (library prim
        name), not the internal MaterialX element name — so that MTL layer
        bindings target the correct library prim.
        """
        # Build reverse mapping: mx_element_name -> manager_name
        mx_to_manager = {v: k for k, v in self._material_mx_names.items()}

        result = {}

        def visit(path):
            spec = self._assignments_layer.GetObjectAtPath(path)
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
                mx_name = str(targets[0]).split("/")[-1]
                # Map back to manager name if possible
                manager_name = mx_to_manager.get(mx_name, mx_name)
                result[str(path)] = manager_name

        self._assignments_layer.Traverse(Sdf.Path("/"), visit)
        return result

    def load_assignments_from_mtl_layer(self, assignments):
        """
        Apply a {abs_prim_path: mat_name} dict to the live stage as bindings.
        assignments is the output of asset_session.read_mtl_layer_assignments().
        Material prims are expected at /MaterialX/Materials/{mat_name}.
        """
        prev_target = self.stage.GetEditTarget()
        self.stage.SetEditTarget(Usd.EditTarget(self._assignments_layer))

        for prim_path_str, mat_name in assignments.items():
            prim = self.stage.GetPrimAtPath(prim_path_str)
            if not prim.IsValid():
                logger.warning(f"load_assignments: prim not found: {prim_path_str}")
                continue
            # Use the actual MX element name (from .mtlx) rather than the
            # manager/library prim name — they can differ.
            mx_name = self._material_mx_names.get(mat_name, mat_name)
            mat_stage_path = f"/MaterialX/Materials/{mx_name}"
            material = UsdShade.Material.Get(self.stage, mat_stage_path)
            if not material.GetPrim().IsValid():
                logger.warning(f"load_assignments: material not found: {mat_stage_path}")
                continue
            prim.ApplyAPI(UsdShade.MaterialBindingAPI)
            UsdShade.MaterialBindingAPI(prim).Bind(material)
            logger.info(f"restored binding {mat_name} -> {prim_path_str}")

        self.stage.SetEditTarget(prev_target)
        self.signal_stage_updated.emit()

    def about_to_close(self):
        for layer in self._material_layers.values():
            if layer.identifier in self.stage_root.subLayerPaths:
                self.stage_root.subLayerPaths.remove(layer.identifier)
        self._material_layers.clear()
        if self._geometry_path and self._geometry_path in self.stage_root.subLayerPaths:
            self.stage_root.subLayerPaths.remove(self._geometry_path)
