"""Transform editor panel for the selected USD prim.

Edits are written to a dedicated anonymous layer so they are visible in the
viewport but never saved to any persistent layer or export.
"""

import logging

from pxr import Gf, Usd, UsdGeom, Sdf

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QDoubleSpinBox, QPushButton, QSizePolicy,
)

logger = logging.getLogger(__name__)

_AXES = ("X", "Y", "Z")
_AXIS_COLORS = ("#c04040", "#40a040", "#4080c0")
_ROT_ORDER = UsdGeom.XformCommonAPI.RotationOrderXYZ


def _spin(lo=-1e6, hi=1e6, decimals=4, step=0.01):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setButtonSymbols(QDoubleSpinBox.NoButtons)
    s.setMinimumWidth(72)
    s.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return s


class _Row(QWidget):
    """Label + three spinboxes for one transform component."""

    changed = Signal(float, float, float)

    def __init__(self, label, lo=-1e6, hi=1e6, decimals=4, step=0.01, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        lbl = QLabel(label)
        lbl.setFixedWidth(18)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(lbl)

        self._spins = []
        for i, axis in enumerate(_AXES):
            axis_lbl = QLabel(axis)
            axis_lbl.setFixedWidth(10)
            axis_lbl.setStyleSheet(f"color: {_AXIS_COLORS[i]}; font-weight: bold;")
            layout.addWidget(axis_lbl)

            s = _spin(lo, hi, decimals, step)
            s.valueChanged.connect(self._emit)
            layout.addWidget(s)
            self._spins.append(s)

    def _emit(self):
        self.changed.emit(*self.values())

    def values(self):
        return tuple(s.value() for s in self._spins)

    def set_values(self, xyz):
        for s, v in zip(self._spins, xyz):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)

    def set_enabled(self, enabled):
        for s in self._spins:
            s.setEnabled(enabled)


class TransformPanel(QWidget):
    """Compact T/R/S editor that writes to an ephemeral anonymous layer."""

    prim_edited = Signal(str)

    def __init__(self, stage_ctrl, parent=None):
        super().__init__(parent)
        self._stage_ctrl = stage_ctrl
        self._prim_path = None
        self._prim = None
        self._applying = False

        # Dedicated anonymous layer — highest priority sublayer so transform
        # overrides win, but never included in any export or save.
        self._xform_layer = Sdf.Layer.CreateAnonymous("_tmp_quiltix_xform.usd")
        self._xform_idf = self._xform_layer.identifier
        stage_ctrl.stage_root.subLayerPaths.insert(0, self._xform_idf)

        self._build_ui()
        self._set_enabled(False)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        header = QHBoxLayout()
        self._prim_label = QLabel("\u2014")
        self._prim_label.setStyleSheet("color: #aaa; font-size: 10px;")
        header.addWidget(self._prim_label)
        header.addStretch()
        btn_reset = QPushButton("Reset")
        btn_reset.setFixedHeight(18)
        btn_reset.setToolTip("Reset transform to identity")
        btn_reset.clicked.connect(self._on_reset)
        header.addWidget(btn_reset)
        root.addLayout(header)

        self._t_row = _Row("T", step=0.1, decimals=4)
        self._r_row = _Row("R", lo=-360, hi=360, step=0.5, decimals=3)
        self._s_row = _Row("S", lo=-1e4, hi=1e4, step=0.01, decimals=4)
        self._s_row.set_values((1.0, 1.0, 1.0))

        self._t_row.changed.connect(lambda x, y, z: self._apply(translate=(x, y, z)))
        self._r_row.changed.connect(lambda x, y, z: self._apply(rotate=(x, y, z)))
        self._s_row.changed.connect(lambda x, y, z: self._apply(scale=(x, y, z)))

        root.addWidget(self._t_row)
        root.addWidget(self._r_row)
        root.addWidget(self._s_row)

    def _set_enabled(self, enabled):
        self._t_row.set_enabled(enabled)
        self._r_row.set_enabled(enabled)
        self._s_row.set_enabled(enabled)

    # ------------------------------------------------------------------ public

    def set_prim_path(self, prim_path_str):
        """Select a prim by path string."""
        stage = self._stage_ctrl.stage
        if not stage or not prim_path_str:
            self._prim = None
            self._prim_path = None
            self._prim_label.setText("\u2014")
            self._set_enabled(False)
            return

        try:
            prim = stage.GetPrimAtPath(Sdf.Path(prim_path_str))
        except Exception:
            prim = None

        if not prim or not prim.IsValid() or not UsdGeom.Xformable(prim):
            self._prim = None
            self._prim_label.setText(f"{prim_path_str}  (not xformable)")
            self._set_enabled(False)
            return

        self._prim = prim
        self._prim_path = prim_path_str
        self._prim_label.setText(prim_path_str.split("/")[-1] or prim_path_str)
        self._set_enabled(True)
        self._load_from_prim()

    # ------------------------------------------------------------------ internals

    def _load_from_prim(self):
        """Populate spinboxes from the prim's current transform."""
        api = UsdGeom.XformCommonAPI(self._prim)
        try:
            t, r, s, _pivot, _order = api.GetXformVectors(Usd.TimeCode.Default())
            t = tuple(float(v) for v in t)
            r = tuple(float(v) for v in r)
            s = tuple(float(v) for v in s)
        except Exception:
            t, r, s = (0, 0, 0), (0, 0, 0), (1, 1, 1)

        self._applying = True
        try:
            self._t_row.set_values(t)
            self._r_row.set_values(r)
            self._s_row.set_values(s)
        finally:
            self._applying = False

    def _apply(self, translate=None, rotate=None, scale=None):
        """Write the changed component to the ephemeral xform layer."""
        if self._applying or not self._prim:
            return

        prev_target = self._stage_ctrl.stage.GetEditTarget()
        self._stage_ctrl.stage.SetEditTarget(Usd.EditTarget(self._xform_layer))
        try:
            api = UsdGeom.XformCommonAPI(self._prim)
            if translate is not None:
                api.SetTranslate(Gf.Vec3d(*translate))
            if rotate is not None:
                api.SetRotate(Gf.Vec3f(*rotate), _ROT_ORDER)
            if scale is not None:
                api.SetScale(Gf.Vec3f(*scale))
        except Exception:
            logger.exception("Failed to apply transform")
        finally:
            self._stage_ctrl.stage.SetEditTarget(prev_target)

        self._stage_ctrl.signal_stage_updated.emit()
        if self._prim_path:
            self.prim_edited.emit(self._prim_path)

    def _on_reset(self):
        if not self._prim:
            return

        prev_target = self._stage_ctrl.stage.GetEditTarget()
        self._stage_ctrl.stage.SetEditTarget(Usd.EditTarget(self._xform_layer))
        try:
            api = UsdGeom.XformCommonAPI(self._prim)
            api.SetTranslate(Gf.Vec3d(0, 0, 0))
            api.SetRotate(Gf.Vec3f(0, 0, 0), _ROT_ORDER)
            api.SetScale(Gf.Vec3f(1, 1, 1))
        except Exception:
            logger.exception("Failed to reset transform")
        finally:
            self._stage_ctrl.stage.SetEditTarget(prev_target)

        self._load_from_prim()
        self._stage_ctrl.signal_stage_updated.emit()
        if self._prim_path:
            self.prim_edited.emit(self._prim_path)

    def about_to_close(self):
        """Remove the xform layer from the stage on shutdown."""
        if self._xform_idf in self._stage_ctrl.stage_root.subLayerPaths:
            self._stage_ctrl.stage_root.subLayerPaths.remove(self._xform_idf)
