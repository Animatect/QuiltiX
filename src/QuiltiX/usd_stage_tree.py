import os
import sys

from qtpy import QtWidgets, QtCore, QtGui  # type: ignore


class _WideGripStyle(QtWidgets.QProxyStyle):
    """Widens the interactive resize handle zone on QHeaderView sections."""
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QtWidgets.QStyle.PM_HeaderGripMargin:
            return 10
        return super().pixelMetric(metric, option, widget)

from pxr import Usd, UsdGeom, Kind
from QuiltiX import usd_stage
from QuiltiX.constants import ROOT

EYE_VISABLE = os.path.join(ROOT, "resources", "icons", "eye_visible.svg")
EYE_INVISABLE = os.path.join(ROOT, "resources", "icons", "eye_invisible.svg")


class PrimVisButton(QtWidgets.QToolButton):
    def __init__(self, parent=None):
        super(PrimVisButton, self).__init__()
        self.setStyleSheet("padding: 0px; margin: 0px; background-color: rgba(255, 255, 255, 0);")

        # TODO: only have one QIcon for all buttons
        self.vis_icon = QtGui.QIcon(EYE_VISABLE)
        self.invis_icon = QtGui.QIcon(EYE_INVISABLE)

        self.vis = True
        self.setIcon(self.vis_icon)
        self.setFixedSize(14, 14)
        # self.clicked.connect(self.toggle_visibility)

    def toggle_visibility(self):
        self.vis = not self.vis
        self.update_vis_icon()
        return self.vis

    def update_vis_icon(self):
        if self.vis:
            self.setIcon(self.vis_icon)
        else:
            self.setIcon(self.invis_icon)

    def set_visibility(self, visibility):
        self.vis = visibility
        self.update_vis_icon()
        return self.vis


class PrimItemWidget(QtWidgets.QTreeWidgetItem):
    def __init__(self, prim):
        super(PrimItemWidget, self).__init__()
        self.prim = prim

    def data(self, column, role):
        if column == 0:
            if role == QtCore.Qt.DisplayRole:
                return self.prim.GetName()
        elif column == 2:
            if role == QtCore.Qt.DisplayRole:
                imageable = UsdGeom.Imageable(self.prim)
                if imageable:
                    purpose = imageable.ComputePurpose()
                    return str(purpose) if purpose != UsdGeom.Tokens.default_ else ""
        elif column == 3:
            if role == QtCore.Qt.DisplayRole:
                return self.prim.GetTypeName()
        elif column == 4:
            if role == QtCore.Qt.DisplayRole:
                kind = Usd.ModelAPI(self.prim).GetKind()
                return kind if kind else ""
        return super().data(column, role)


class UsdStageTreeWidget(QtWidgets.QTreeWidget):
    assign_material_to_selected = QtCore.Signal(str)
    prim_visibility_changed = QtCore.Signal()

    def __init__(self, stage=None, parent=None):
        super(UsdStageTreeWidget, self).__init__(parent=parent)
        # TODO: cleanup settings
        __qtreewidgetitem = QtWidgets.QTreeWidgetItem()
        __qtreewidgetitem.setText(0, "Name")
        __qtreewidgetitem.setText(2, "Purpose")
        __qtreewidgetitem.setText(3, "Type")
        __qtreewidgetitem.setText(4, "Kind")
        self.setHeaderItem(__qtreewidgetitem)
        self.setColumnCount(5)
        self.header().setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.header().setStretchLastSection(False)
        self.header().setVisible(True)
        self.header().setMinimumSectionSize(20)
        for col in range(5):
            self.header().setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)
        self.header().setStyle(_WideGripStyle())
        self.header().setStyleSheet(
            "QHeaderView::section { border-right: 1px solid palette(mid); padding-left: 4px; }"
        )
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFrameShadow(QtWidgets.QFrame.Plain)
        self.setLineWidth(0)
        self.setMidLineWidth(0)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setUniformRowHeights(True)
        self.setColumnWidth(0, 200)
        self.setColumnWidth(1, 20)
        self.setColumnWidth(2, 60)
        self.setColumnWidth(3, 80)
        self.setColumnWidth(4, 80)
        self._prim_to_item_map = {}
        self.get_materials_func = None
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.set_stage(stage)

    def set_stage(self, stage):
        self.stage = stage
        self.refresh_tree()

    def _get_expanded_paths(self, item=None):
        """Collect prim paths of all currently expanded items."""
        paths = set()
        if item is None:
            item = self.invisibleRootItem()
        for i in range(item.childCount()):
            child = item.child(i)
            if isinstance(child, PrimItemWidget) and child.isExpanded():
                paths.add(str(child.prim.GetPath()))
                paths.update(self._get_expanded_paths(child))
        return paths

    def _restore_expanded_paths(self, paths, item=None):
        """Re-expand items whose prim paths were previously expanded."""
        if item is None:
            item = self.invisibleRootItem()
        for i in range(item.childCount()):
            child = item.child(i)
            if isinstance(child, PrimItemWidget) and str(child.prim.GetPath()) in paths:
                child.setExpanded(True)
                self._restore_expanded_paths(paths, child)

    def refresh_tree(self):
        # mods = QtWidgets.QApplication.keyboardModifiers()
        # if mods != QtCore.Qt.ControlModifier:
        #     return

        # Save expanded state before clearing
        expanded = self._get_expanded_paths()

        self.clear()
        if not self.stage:
            return

        stage_root = self.stage.GetPseudoRoot()
        invisible_root_item = self.invisibleRootItem()
        self.populate_item_tree(stage_root, invisible_root_item)

        if expanded:
            self._restore_expanded_paths(expanded)
        else:
            self.expandToDepth(0)

    def create_item_from_prim(self, prim):
        item = PrimItemWidget(prim)
        item.emitDataChanged()
        self._prim_to_item_map[prim] = item
        return item

    def populate_item_tree(self, prim, parent_item):
        created_item = self.create_item_from_prim(prim)
        parent_item.addChild(created_item)

        # FIXME: this will probably not work in all cases
        if bool(UsdGeom.Imageable(prim).GetVisibilityAttr()):
            vis_button = PrimVisButton(prim)
            vis_button.clicked.connect(lambda: self.toggle_hierarchy_visibility(created_item))
            self.setItemWidget(created_item, 1, vis_button)

        prim_children = self._get_filtered_prim_children(prim)
        for prim_child in prim_children:
            self.populate_item_tree(prim_child, created_item)

        return created_item

    def _get_filtered_prim_children(self, prim):
        return prim.GetFilteredChildren(Usd.PrimIsActive)

    def toggle_hierarchy_visibility(self, item, set_visibility_to=None):
        is_root_call = set_visibility_to is None
        item_vis_button = self.itemWidget(item, 1)
        if is_root_call:
            set_visibility_to = item_vis_button.toggle_visibility()
        else:
            item_vis_button.set_visibility(set_visibility_to)

        if isinstance(item, PrimItemWidget):
            imageable = UsdGeom.Imageable(item.prim)
            if set_visibility_to:
                imageable.MakeVisible()
            else:
                imageable.MakeInvisible()

        for i in range(item.childCount()):
            child_item = item.child(i)
            self.toggle_hierarchy_visibility(child_item, set_visibility_to)

        if is_root_call:
            self.prim_visibility_changed.emit()

    def _show_context_menu(self, pos):
        selected_prims = self.get_selected_prims()
        if not selected_prims or self.get_materials_func is None:
            return

        materials = self.get_materials_func()
        if not materials:
            return

        menu = QtWidgets.QMenu(self)
        assign_menu = menu.addMenu("Assign Material")
        for mat_name in materials:
            action = assign_menu.addAction(mat_name)
            action.triggered.connect(lambda checked=False, name=mat_name: self.assign_material_to_selected.emit(name))

        menu.exec_(self.viewport().mapToGlobal(pos))

    def get_selected_prims(self):
        items = self.selectedItems()
        prims = [item.prim for item in items]
        return prims


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)

    stage_file = os.path.join(ROOT, "resources", "geometry", "plane_uv.usda")
    stage = usd_stage.get_stage_from_file(stage_file)
    tree_widget = UsdStageTreeWidget(stage)
    tree_widget.show()
    app.exec_()
