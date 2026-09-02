def classFactory(iface):
    from .quick_export import QuickCanvasExport
    return QuickCanvasExport(iface)