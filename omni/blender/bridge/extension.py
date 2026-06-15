# SPDX-License-Identifier: Apache-2.0

import omni.ext

from .panel import BlenderBridgePanel


class BlenderBridgeExtension(omni.ext.IExt):
    """Blender Bridge Extension - Mesh editing and mask baking via Blender"""
    
    def on_startup(self, ext_id: str):
        print(f"[omni.blender.bridge] Blender Bridge starting up (ext_id: {ext_id})")
        self._panel = BlenderBridgePanel()
    
    def on_shutdown(self):
        print("[omni.blender.bridge] Blender Bridge shutting down")
        if self._panel:
            self._panel.shutdown()
            self._panel = None
