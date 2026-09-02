import os
import datetime
import math
from qgis.PyQt.QtWidgets import (
    QAction, QFileDialog, QApplication, QDialog, 
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QSizePolicy,
    QGridLayout, QCheckBox, QFontComboBox, QSpinBox, QDoubleSpinBox
)
from qgis.PyQt.QtGui import QPixmap, QColor, QImage, QIcon
from qgis.PyQt.QtCore import Qt, QSize
from qgis.core import (
    QgsProject, 
    QgsLayout, 
    QgsLayoutItemMap, 
    QgsLayoutItemScaleBar, 
    QgsLayoutItemPicture,
    QgsLayoutItemMapGrid,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutExporter, 
    QgsLayoutSize, 
    QgsUnitTypes, 
    QgsLayoutPoint, 
    QgsLayoutItemPage,
    QgsLayoutItem,
    QgsRectangle,
    QgsWkbTypes,
    QgsPointXY,
    QgsTextFormat,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry
)
from qgis.gui import QgsMapTool, QgsRubberBand

# ---------------------------------------------------------------------------
# 1. CUSTOM CRASH-PROOF MAP TOOL
# ---------------------------------------------------------------------------
class ExportRectangleTool(QgsMapTool):
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback = callback
        self.is_drawing = False
        self.start_pt = None
        
        self.rb = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self.rb.setColor(QColor(255, 0, 0, 50))
        self.rb.setStrokeColor(QColor(255, 0, 0, 255))
        self.rb.setWidth(1)

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.is_drawing = True
            self.start_pt = e.mapPoint()
            self.rb.reset(QgsWkbTypes.PolygonGeometry)

    def canvasMoveEvent(self, e):
        if not self.is_drawing:
            return
            
        end_pt = e.mapPoint()
        self.rb.reset(QgsWkbTypes.PolygonGeometry)
        
        p1 = QgsPointXY(self.start_pt.x(), self.start_pt.y())
        p2 = QgsPointXY(end_pt.x(), self.start_pt.y())
        p3 = QgsPointXY(end_pt.x(), end_pt.y())
        p4 = QgsPointXY(self.start_pt.x(), end_pt.y())
        
        self.rb.addPoint(p1, False)
        self.rb.addPoint(p2, False)
        self.rb.addPoint(p3, False)
        self.rb.addPoint(p4, True)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.is_drawing:
            self.is_drawing = False
            end_pt = e.mapPoint()
            
            self.rb.reset(QgsWkbTypes.PolygonGeometry) 
            
            rect = QgsRectangle(self.start_pt, end_pt)
            rect.normalize() 
            
            if rect.width() > 0 and rect.height() > 0:
                self.callback(rect)

    def deactivate(self):
        self.rb.reset(QgsWkbTypes.PolygonGeometry)
        super().deactivate()


# ---------------------------------------------------------------------------
# 2. UNIFIED PREVIEW DIALOG
# ---------------------------------------------------------------------------
class ExportPreviewDialog(QDialog):
    def __init__(self, iface, extent, active_layer, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.extent = extent
        self.active_layer = active_layer
        
        self.layout = None
        self.map_item = None
        self.scalebar = None
        self.north_arrow = None
        self.grid = None
        
        self.map_width_mm = 0
        self.map_height_mm = 0
        
        self.final_image = QImage()
        
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Map Export")
        self.resize(800, 750)
        
        main_layout = QVBoxLayout()
        
        # --- TOP CONTROLS REORGANIZED ---
        controls_layout = QGridLayout()
        
        # Row 0: Global Settings
        self.transparent_cb = QCheckBox("Transparent Bg")
        self.transparent_cb.stateChanged.connect(self.update_preview)
        controls_layout.addWidget(self.transparent_cb, 0, 0)
        
        self.north_arrow_cb = QCheckBox("Add North Arrow")
        self.north_arrow_cb.setChecked(False)
        self.north_arrow_cb.stateChanged.connect(self.update_preview)
        controls_layout.addWidget(self.north_arrow_cb, 0, 1)

        controls_layout.addWidget(QLabel("Global Font:"), 0, 2)
        self.font_combo = QFontComboBox()
        self.font_combo.currentFontChanged.connect(self.update_preview)
        controls_layout.addWidget(self.font_combo, 0, 3, 1, 3)

        # Row 1: Scalebar Options
        controls_layout.addWidget(QLabel("Scalebar Units:"), 1, 0)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["No Scalebar", "Kilometers", "Miles", "Nautical Miles", "Meters", "Feet"])
        self.unit_combo.currentIndexChanged.connect(self.update_preview)
        controls_layout.addWidget(self.unit_combo, 1, 1)
        
        self.auto_scale_cb = QCheckBox("Auto SB Size")
        self.auto_scale_cb.setChecked(True)
        self.auto_scale_cb.stateChanged.connect(self.update_preview)
        controls_layout.addWidget(self.auto_scale_cb, 1, 2)

        # Row 2: Scalebar Tuning
        controls_layout.addWidget(QLabel("SB Font Size:"), 2, 0)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setValue(12)
        self.font_size_spin.valueChanged.connect(self.update_preview)
        controls_layout.addWidget(self.font_size_spin, 2, 1)
        
        controls_layout.addWidget(QLabel("SB Ticks:"), 2, 2)
        self.ticks_spin = QSpinBox()
        self.ticks_spin.setMinimum(1)
        self.ticks_spin.setValue(4)
        self.ticks_spin.valueChanged.connect(self.on_manual_scale_change)
        controls_layout.addWidget(self.ticks_spin, 2, 3)
        
        controls_layout.addWidget(QLabel("SB Max Val:"), 2, 4)
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setMaximum(999999999)
        self.max_spin.setDecimals(2)
        self.max_spin.valueChanged.connect(self.on_manual_scale_change)
        controls_layout.addWidget(self.max_spin, 2, 5)

        # Row 3: Map Frame Options
        controls_layout.addWidget(QLabel("Map Frame:"), 3, 0)
        self.frame_combo = QComboBox()
        self.frame_combo.addItems(["None", "Decimal Degrees"])
        self.frame_combo.currentIndexChanged.connect(self.on_frame_type_changed)
        controls_layout.addWidget(self.frame_combo, 3, 1)

        self.auto_grid_cb = QCheckBox("Auto Frame Tick")
        self.auto_grid_cb.setChecked(True)
        self.auto_grid_cb.stateChanged.connect(self.update_preview)
        controls_layout.addWidget(self.auto_grid_cb, 3, 2)

        # Row 4: Map Frame Tuning
        controls_layout.addWidget(QLabel("Frame Font:"), 4, 0)
        self.grid_font_size_spin = QSpinBox()
        self.grid_font_size_spin.setValue(10)
        self.grid_font_size_spin.valueChanged.connect(self.update_preview)
        controls_layout.addWidget(self.grid_font_size_spin, 4, 1)
        
        controls_layout.addWidget(QLabel("Frame Interval:"), 4, 2)
        self.grid_interval_spin = QDoubleSpinBox()
        self.grid_interval_spin.setRange(0.000001, 999999999)
        self.grid_interval_spin.setDecimals(6)
        self.grid_interval_spin.valueChanged.connect(self.on_manual_grid_change)
        controls_layout.addWidget(self.grid_interval_spin, 4, 3, 1, 3)
        
        main_layout.addLayout(controls_layout)
        
        # --- IMAGE PREVIEW ---
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("""
            background-color: #e0e0e0; 
            border: 1px solid #aaa;
            background-image: url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAHElEQVQYV2NkYGAwYkADjDgkMcqgKkYXwygKBAAA5OQEApYx6v8AAAAASUVORK5CYII=");
        """)
        self.preview_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        main_layout.addWidget(self.preview_label, 1) 
        
        # --- BOTTOM BUTTONS ---
        btn_layout = QHBoxLayout()
        btn_copy = QPushButton("Copy to Clipboard")
        btn_copy.clicked.connect(self.copy_image)
        btn_export = QPushButton("Export to File")
        btn_export.clicked.connect(self.export_image)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        
        btn_layout.addWidget(btn_copy)
        btn_layout.addWidget(btn_export)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        main_layout.addLayout(btn_layout)
        
        self.setLayout(main_layout)
        
        # Initialize rendering
        self.setup_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_display()

    def get_qgs_unit(self):
        text = self.unit_combo.currentText()
        if text == "Kilometers": return QgsUnitTypes.DistanceKilometers
        if text == "Miles": return QgsUnitTypes.DistanceMiles
        if text == "Nautical Miles": return QgsUnitTypes.DistanceNauticalMiles
        if text == "Meters": return QgsUnitTypes.DistanceMeters
        if text == "Feet": return QgsUnitTypes.DistanceFeet
        return None

    def on_manual_scale_change(self):
        if self.auto_scale_cb.isChecked():
            self.auto_scale_cb.blockSignals(True)
            self.auto_scale_cb.setChecked(False)
            self.auto_scale_cb.blockSignals(False)
        self.update_preview()

    def on_manual_grid_change(self):
        if self.auto_grid_cb.isChecked():
            self.auto_grid_cb.blockSignals(True)
            self.auto_grid_cb.setChecked(False)
            self.auto_grid_cb.blockSignals(False)
        self.update_preview()

    def on_frame_type_changed(self):
        self.auto_grid_cb.blockSignals(True)
        self.auto_grid_cb.setChecked(True)
        self.auto_grid_cb.blockSignals(False)
        self.update_preview()

    def get_nice_interval(self, raw_interval):
        if raw_interval <= 0: 
            return 1.0
        magnitude = 10 ** math.floor(math.log10(raw_interval))
        residual = raw_interval / magnitude
        if residual > 5: tick = 5.0
        elif residual > 2: tick = 2.0
        else: tick = 1.0
        return tick * magnitude

    def setup_layout(self):
        project = QgsProject.instance()
        canvas = self.iface.mapCanvas()
        
        self.layout = QgsLayout(project)
        self.layout.initializeDefaults()
        
        dpi = canvas.mapSettings().outputDpi()
        width_ratio = self.extent.width() / canvas.extent().width()
        self.map_width_mm = (canvas.width() * width_ratio) / (dpi / 25.4)
        aspect_ratio = self.extent.height() / self.extent.width()
        self.map_height_mm = self.map_width_mm * aspect_ratio
        
        if self.map_width_mm < 40.0 or self.map_height_mm < 20.0:
            scale_factor = max(40.0 / self.map_width_mm, 20.0 / self.map_height_mm)
            self.map_width_mm *= scale_factor
            self.map_height_mm *= scale_factor
        
        page = QgsLayoutItemPage(self.layout)
        page.setPageSize(QgsLayoutSize(self.map_width_mm, self.map_height_mm, QgsUnitTypes.LayoutMillimeters))
        self.layout.pageCollection().addPage(page)
        
        self.map_item = QgsLayoutItemMap(self.layout)
        self.map_item.attemptMove(QgsLayoutPoint(0, 0, QgsUnitTypes.LayoutMillimeters))
        self.map_item.attemptResize(QgsLayoutSize(self.map_width_mm, self.map_height_mm, QgsUnitTypes.LayoutMillimeters))
        self.map_item.setExtent(self.extent)
        self.map_item.setCrs(canvas.mapSettings().destinationCrs())
        
        # Enforce map scale so tiles render at correct LOD even at 300 DPI
        self.map_item.setScale(canvas.scale())
        
        if self.active_layer:
            visible_layers = [lyr for lyr in canvas.layers() if lyr.id() != self.active_layer.id()]
        else:
            visible_layers = canvas.layers()
            
        self.map_item.setLayers(visible_layers)
        self.map_item.setKeepLayerSet(True)
        self.layout.addLayoutItem(self.map_item)

        # Map Grid
        self.grid = QgsLayoutItemMapGrid("Coordinate Grid", self.map_item)
        self.map_item.grids().addGrid(self.grid)
        
        # Scalebar
        self.scalebar = QgsLayoutItemScaleBar(self.layout)
        self.scalebar.setLinkedMap(self.map_item)
        self.scalebar.setStyle('Single Box')
        self.scalebar.setBackgroundEnabled(False) 
        self.layout.addLayoutItem(self.scalebar)

        # North Arrow
        self.north_arrow = QgsLayoutItemPicture(self.layout)
        self.north_arrow.setPicturePath(':/images/north_arrows/layout_default_north_arrow.svg')
        self.north_arrow.setLinkedMap(self.map_item)
        self.north_arrow.setNorthMode(QgsLayoutItemPicture.TrueNorth)
        self.north_arrow.attemptResize(QgsLayoutSize(15, 15, QgsUnitTypes.LayoutMillimeters))
        self.north_arrow.setBackgroundEnabled(False)
        self.layout.addLayoutItem(self.north_arrow)
        
        self.update_preview()

    def update_preview(self):
        unit_type = self.get_qgs_unit()
        show_north_arrow = self.north_arrow_cb.isChecked()
        frame_type = self.frame_combo.currentText()
        frame_enabled = (frame_type != "None")
        
        page = self.layout.pageCollection().page(0)
        
        # MASSIVE horizontal padding to ensure long text (e.g. 118.4521° W) is never pushed off the edge
        pad_t = 25.0 if frame_enabled else 0.0
        pad_l = 55.0 if frame_enabled else 0.0
        pad_r = 55.0 if frame_enabled else 0.0
        pad_b = 25.0 if frame_enabled else 0.0
        
        needs_bottom_pane = (unit_type is not None) or show_north_arrow
        if needs_bottom_pane:
            pad_b += 15.0
            
        total_width_mm = self.map_width_mm + pad_l + pad_r
        total_height_mm = self.map_height_mm + pad_t + pad_b
            
        page.setPageSize(QgsLayoutSize(total_width_mm, total_height_mm, QgsUnitTypes.LayoutMillimeters))
        self.map_item.attemptMove(QgsLayoutPoint(pad_l, pad_t, QgsUnitTypes.LayoutMillimeters))
        
        # Background Transparency
        is_transparent = self.transparent_cb.isChecked()
        page_symbol = QgsFillSymbol.createSimple({'outline_style': 'no'})
        if is_transparent:
            page_symbol.setColor(QColor(Qt.transparent))
            self.map_item.setBackgroundEnabled(False)
        else:
            page_symbol.setColor(QColor(Qt.white))
            self.map_item.setBackgroundEnabled(True)
            self.map_item.setBackgroundColor(QColor(Qt.white))
            
        page.setPageStyleSymbol(page_symbol)
        page.setBackgroundEnabled(False) 
        
        # Configure Grid/Ticks
        if frame_enabled:
            self.grid.setEnabled(True)
            self.grid.setAnnotationEnabled(True)
            self.grid.setFrameStyle(QgsLayoutItemMapGrid.ExteriorTicks)
            self.grid.setAnnotationFrameDistance(2.0)
            
            # Force standard horizontal text on all sides so QGIS layout engine doesn't break
            self.grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Left)
            self.grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Right)
            self.grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Top)
            self.grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Bottom)
            
            line_sym = QgsLineSymbol.createSimple({'color': 'transparent'})
            self.grid.setLineSymbol(line_sym)
            
            grid_font = self.font_combo.currentFont()
            grid_font.setPointSize(self.grid_font_size_spin.value())
            text_format = QgsTextFormat()
            text_format.setFont(grid_font)
            text_format.setSize(self.grid_font_size_spin.value())
            self.grid.setAnnotationTextFormat(text_format)
            
            crs = QgsCoordinateReferenceSystem("EPSG:4326")
            self.grid.setCrs(crs)
            
            transform = QgsCoordinateTransform(self.map_item.crs(), crs, QgsProject.instance())
            ll_ext = transform.transformBoundingBox(self.extent)
            calc_interval = self.get_nice_interval(ll_ext.width() / 4.0)

            # Added spaces before N/S/E/W for cleaner styling
            self.grid.setAnnotationFormat(QgsLayoutItemMapGrid.CustomFormat)
            zero_trim_expression = (
                "regexp_replace(regexp_replace(format_number(abs(@grid_number), 6), '0+$', ''), '\\\\.$', '') || '°' || "
                "CASE "
                "WHEN @grid_number = 0 THEN '' "
                "WHEN @grid_axis = 'x' AND @grid_number > 0 THEN ' E' "
                "WHEN @grid_axis = 'x' AND @grid_number < 0 THEN ' W' "
                "WHEN @grid_axis = 'y' AND @grid_number > 0 THEN ' N' "
                "WHEN @grid_axis = 'y' AND @grid_number < 0 THEN ' S' "
                "END"
            )
            self.grid.setAnnotationExpression(zero_trim_expression)

            if self.auto_grid_cb.isChecked():
                interval = calc_interval
                self.grid_interval_spin.blockSignals(True)
                self.grid_interval_spin.setValue(interval)
                self.grid_interval_spin.blockSignals(False)
            else:
                interval = self.grid_interval_spin.value()
                if interval <= 0:
                    interval = calc_interval

            self.grid.setIntervalX(interval)
            self.grid.setIntervalY(interval)
        else:
            self.grid.setEnabled(False)

        # Position North Arrow
        self.north_arrow.setVisibility(show_north_arrow)
        if show_north_arrow:
            self.north_arrow.setReferencePoint(QgsLayoutItem.Middle)
            na_y = pad_t + self.map_height_mm + (15.0 if frame_enabled else 0.0) + 7.5
            na_x = pad_l + self.map_width_mm - 10
            self.north_arrow.attemptMove(QgsLayoutPoint(na_x, na_y, QgsUnitTypes.LayoutMillimeters))

        # Position Scalebar
        if unit_type is None:
            self.scalebar.setVisibility(False)
        else:
            self.scalebar.setVisibility(True)
            self.scalebar.setReferencePoint(QgsLayoutItem.Middle)
            sb_y = pad_t + self.map_height_mm + (15.0 if frame_enabled else 0.0) + 7.5
            sb_x = pad_l + (self.map_width_mm / 2)
            self.scalebar.attemptMove(QgsLayoutPoint(sb_x, sb_y, QgsUnitTypes.LayoutMillimeters))
            
            text_format = QgsTextFormat()
            font = self.font_combo.currentFont()
            font.setPointSize(self.font_size_spin.value())
            text_format.setFont(font)
            text_format.setSize(self.font_size_spin.value())
            self.scalebar.setTextFormat(text_format)
            
            unit_labels = {
                QgsUnitTypes.DistanceKilometers: "km",
                QgsUnitTypes.DistanceMiles: "mi",
                QgsUnitTypes.DistanceNauticalMiles: "NM",
                QgsUnitTypes.DistanceMeters: "m",
                QgsUnitTypes.DistanceFeet: "ft"
            }
            if unit_type in unit_labels:
                self.scalebar.setUnitLabel(unit_labels[unit_type])
            
            if self.auto_scale_cb.isChecked():
                self.scalebar.applyDefaultSize(unit_type)
                if unit_type in unit_labels:
                    self.scalebar.setUnitLabel(unit_labels[unit_type])
                
                self.ticks_spin.blockSignals(True)
                self.max_spin.blockSignals(True)
                self.ticks_spin.setValue(self.scalebar.numberOfSegments())
                self.max_spin.setValue(self.scalebar.unitsPerSegment() * self.scalebar.numberOfSegments())
                self.ticks_spin.blockSignals(False)
                self.max_spin.blockSignals(False)
            else:
                self.scalebar.setUnits(unit_type)
                ticks = self.ticks_spin.value()
                max_val = self.max_spin.value()
                
                self.scalebar.setNumberOfSegments(ticks)
                self.scalebar.setNumberOfSegmentsLeft(0)
                
                if ticks > 0:
                    self.scalebar.setUnitsPerSegment(max_val / ticks)
            
        # Force QGIS to recount bounding boxes to absolutely prevent clipping before export
        self.map_item.updateBoundingRect()
        self.layout.updateBounds()
        
        exporter = QgsLayoutExporter(self.layout)
        
        # Render the image using the layout's native 300 DPI high-res export
        self.final_image = exporter.renderPageToImage(0)
        self.refresh_display()

    def refresh_display(self):
        if not self.final_image.isNull():
            pixmap = QPixmap.fromImage(self.final_image)
            label_size = self.preview_label.size()
            if label_size.width() > 0 and label_size.height() > 0:
                self.preview_label.setPixmap(pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # --- CLIPBOARD AND EXPORT ACTIONS ---
    def copy_image(self):
        if not self.final_image.isNull():
            QApplication.clipboard().setPixmap(QPixmap.fromImage(self.final_image))
            self.iface.messageBar().pushMessage("Success", "Map copied to clipboard.", level=0, duration=3)

    def export_image(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Map", "", "PNG Format (*.png);;JPEG Format (*.jpg *.jpeg)")
        if file_path and not self.final_image.isNull():
            self.final_image.save(file_path)
            self.iface.messageBar().pushMessage("Success", f"Saved to {file_path}", level=0, duration=5)


# ---------------------------------------------------------------------------
# 3. MAIN PLUGIN CLASS
# ---------------------------------------------------------------------------
class QuickCanvasExport:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action_draw = None
        self.tool = None
        self.dlg = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.svg')
        icon = QIcon(icon_path)
        
        self.action_draw = QAction(icon, "Draw Export Region", self.iface.mainWindow())
        self.action_draw.triggered.connect(self.start_drawing)
        self.iface.addToolBarIcon(self.action_draw)
        
        self.tool = ExportRectangleTool(self.iface.mapCanvas(), self.on_extent_drawn)

    def unload(self):
        self.iface.removeToolBarIcon(self.action_draw)
        del self.action_draw

    def start_drawing(self):
        self.iface.mapCanvas().setMapTool(self.tool)
        self.iface.messageBar().pushMessage(
            "Info", "Click and drag to draw an export rectangle on the map.", level=0, duration=5
        )

    def on_extent_drawn(self, extent):
        active_layer = self.create_extent_layer(extent)
        
        if self.dlg:
            try:
                self.dlg.close()
            except RuntimeError:
                pass 

        self.dlg = ExportPreviewDialog(self.iface, extent, active_layer, self.iface.mainWindow())
        self.dlg.show()
        self.dlg.raise_() 

    def create_extent_layer(self, extent):
        """Creates an in-memory polygon layer matching the drawn extent."""
        project = QgsProject.instance()
        
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        layer_name = f"Export Region {timestamp}"
            
        crs_authid = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
        mem_layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", layer_name, "memory")
        
        if mem_layer.isValid():
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromRect(extent))
            mem_layer.dataProvider().addFeatures([feat])
            mem_layer.updateExtents()
            
            symbol = QgsFillSymbol.createSimple({
                'color': 'transparent', 
                'outline_color': 'red', 
                'outline_width': '0.6'
            })
            mem_layer.renderer().setSymbol(symbol)
            
            project.addMapLayer(mem_layer)
            return mem_layer
            
        return None