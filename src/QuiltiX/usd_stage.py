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

    def set_stage(self, stage):
        self.stage = stage
        self.stage_root = self.stage.GetRootLayer()
        self.stage.SetEditTarget(Usd.EditTarget(self.stage.GetSessionLayer()))

        self._material_layers = {}
        self._active_material = None
        self._material_mx_names = {}

        in_memory = os.getenv("QUILTIX_WRITE_TMP_TO_DISK", "0") == "0"
        if in_memory:
            idf = "_tmp_quiltix_assignments.usd"
            self._assignments_layer = Sdf.Layer.CreateAnonymous(idf)
            self._assignments_idf = self._assignments_layer.identifier
        else:
            self._assignments_idf = os.path.join(os.environ["TEMP"], "_tmp_quiltix_assignments.usd")
            self._assignments_layer = Sdf.Layer.CreateNew(self._assignments_idf)

        self.stage_root.subLayerPaths.insert(0, self._assignments_idf)
        self.signal_stage_changed.emit(self.stage)

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

        self.stage.GetSessionLayer().Clear()
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

        if qx_node.type_ == "Other.QxGroupNode":
            ng_name = qx_node.name()
            sub_graph = qx_node.get_sub_graph()
            if not sub_graph:
                return

            in_port_node = sub_graph.get_input_port_nodes()[0]
            out_port = in_port_node.get_output(property_name)
            cports = out_port.connected_ports()
            if not cports:
                return

            mx_stage_path = f"/MaterialX/NodeGraphs/{ng_name}/" + cports[0].node().name()
            property_name = cports[0].name()
            prim = self.stage.GetPrimAtPath(mx_stage_path)
        elif qx_node.current_mx_def.getNodeGroup() in ["material", "pbr", "shader"]:
            mx_elem_name = self._material_mx_names.get(self._active_material, self._active_material)
            mx_stage_path = f"/MaterialX/Materials/{mx_elem_name}"
            prim = self.stage.GetPrimAtPath(mx_stage_path)
        else:
            if qx_node.graph.is_root:
                ng_name = "NG_main"
            else:
                ng_name = qx_node.graph.node.name()

            mx_stage_path = f"/MaterialX/NodeGraphs/{ng_name}/" + qx_node.NODE_NAME
            prim = self.stage.GetPrimAtPath(mx_stage_path)

        if not prim.IsValid():
            logger.warning("invalid prim at path: " + mx_stage_path)
            return

        usdinput = UsdShade.Shader(prim).GetInput(property_name)
        attr = usdinput.GetAttr()
        if not attr.IsValid():
            logger.warning(f"Invalid attribute {property_name} on prim {mx_stage_path}")
            return

        if type(property_value) in [list, tuple]:
            if len(property_value) == 4:
                property_value = property_value[
                    :3
                ]  # temporary fix, the RGB color picker widgets emits a list of 4 values

            if len(property_value) == 3:
                property_value = Gf.Vec3f(property_value)
            elif len(property_value) == 2:
                property_value = Gf.Vec2f(property_value)                

        usdinput.GetAttr().Set(property_value)
        self.signal_stage_updated.emit()

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

    def about_to_close(self):
        for layer in self._material_layers.values():
            if layer.identifier in self.stage_root.subLayerPaths:
                self.stage_root.subLayerPaths.remove(layer.identifier)
        self._material_layers.clear()
