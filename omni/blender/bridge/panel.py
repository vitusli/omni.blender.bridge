# SPDX-License-Identifier: Apache-2.0
"""
Blender Bridge Panel

Main UI for Blender integration:
- Global Settings (Blender path)
- Edit Mesh (interactive mesh editing in Blender GUI)
"""

import asyncio
import os
import subprocess
from typing import List, Optional, Tuple

import carb
import carb.settings
import omni.kit.menu.utils
import omni.ui as ui
import omni.usd
from omni.kit.menu.utils import MenuItemDescription
from pxr import Usd, UsdGeom

from .exporter import export_meshes_to_usd
from .styles import (
    ADD_BTN_STYLE,
    EXECUTE_BTN_STYLE,
    FIELD_HEIGHT,
    FIELD_STYLE,
    LABEL_WIDTH,
    RESET_DOT_SIZE,
    SECTION_STYLE,
    SPACING,
    TEXT_NORMAL,
)


WINDOW_TITLE = "Blender Bridge"
MENU_PATH = "vtools/Blender Bridge"


def detect_blender_paths() -> List[Tuple[str, str]]:
    """
    Detect installed Blender paths on Windows.
    Returns list of (label, path) tuples.
    """
    import getpass
    username = getpass.getuser()
    
    candidates = [
        ("Scoop", f"C:\\Users\\{username}\\scoop\\apps\\blender\\current\\blender.exe"),
        ("Program Files", "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe"),
        ("Program Files", "C:\\Program Files\\Blender Foundation\\Blender 4.3\\blender.exe"),
        ("Program Files", "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe"),
    ]
    
    found = []
    for label, path in candidates:
        if os.path.exists(path):
            found.append((label, path))
    
    return found


class BlenderBridgePanel:
    """Main UI Panel for Blender Bridge operations"""
    
    def __init__(self):
        self._settings = carb.settings.get_settings()
        self._window = ui.Window(
            WINDOW_TITLE, 
            width=420, 
            height=0,  # Auto-size
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
            dockPreference=ui.DockPreference.RIGHT_TOP,
        )
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        
        # State for Edit Mesh session
        self._edit_mesh_process: Optional[subprocess.Popen] = None
        self._edit_mesh_export_path: Optional[str] = None
        self._edit_mesh_prim_paths: List[str] = []
        self._edit_mesh_count: int = 0
        
        # Detect Blender installations
        self._blender_paths = detect_blender_paths()
        
        self._build_ui()
        self._register_menu()
        
        carb.log_info(f"[BlenderBridge] Initialized")
    
    def _register_menu(self):
        """Register window in menu"""
        self._menu_items = [
            MenuItemDescription(
                name="Blender Bridge",
                ticked=True,
                ticked_fn=lambda: self._window is not None and self._window.visible,
                onclick_fn=lambda *_: self._toggle_window(),
            )
        ]
        omni.kit.menu.utils.add_menu_items(self._menu_items, "vtools")

    def _toggle_window(self):
        if self._window:
            self._window.visible = not self._window.visible
    
    def _build_ui(self):
        """Build the main UI matching Scene Optimizer style"""
        with self._window.frame:
            with ui.VStack(spacing=0, style={"margin": 6}):
                
                # === Settings Section (collapsed) ===
                with ui.CollapsableFrame("Settings", collapsed=True, height=0, style=SECTION_STYLE):
                    with ui.VStack(spacing=SPACING):
                        ui.Spacer(height=2)
                        self._build_blender_settings_row()
                        ui.Spacer(height=4)
                
                ui.Spacer(height=4)
                
                # === Edit Mesh Section ===
                with ui.CollapsableFrame("Edit Mesh", collapsed=False, height=0, style=SECTION_STYLE):
                    with ui.VStack(spacing=SPACING):
                        ui.Spacer(height=2)
                        
                        # --- Geometry to Edit ---
                        self._build_edit_geometry_row()
                        
                        ui.Spacer(height=4)
                        
                        # --- Execute/Done Button ---
                        self._edit_btn = ui.Button(
                            "Execute - Edit Mesh",
                            height=32,
                            clicked_fn=self._execute_edit_mesh,
                            style=EXECUTE_BTN_STYLE
                        )
    
    # === Settings Section ===
    
    def _build_blender_settings_row(self):
        """Build Blender path settings with auto-detect dropdown"""
        with ui.VStack(spacing=SPACING):
            with ui.HStack(height=FIELD_HEIGHT, spacing=SPACING):
                ui.Label("Blender", width=LABEL_WIDTH, style={"color": TEXT_NORMAL})
                
                # Build dropdown options
                options = []
                for label, path in self._blender_paths:
                    options.append(f"{label}: {os.path.basename(os.path.dirname(path))}")
                options.append("Custom...")
                
                self._blender_combo = ui.ComboBox(0, *options)
                self._blender_combo.model.add_item_changed_fn(self._on_blender_selection_changed)
                
                ui.Circle(width=RESET_DOT_SIZE, height=RESET_DOT_SIZE,
                          style={"background_color": 0xFF4A4A4A})
            
            # Custom path field (hidden by default)
            self._custom_blender_stack = ui.HStack(height=FIELD_HEIGHT, spacing=SPACING, visible=False)
            with self._custom_blender_stack:
                ui.Label("Custom Path", width=LABEL_WIDTH, style={"color": TEXT_NORMAL})
                self._blender_field = ui.StringField(style=FIELD_STYLE)
                self._blender_field.model.set_value(
                    self._settings.get("/exts/omni.blender.bridge/blender_path") or "")
                ui.Button("...", width=24, clicked_fn=self._browse_blender, style=ADD_BTN_STYLE)
    
    def _on_blender_selection_changed(self, model, item):
        """Handle Blender dropdown selection change"""
        idx = model.get_item_value_model().get_value_as_int()
        is_custom = idx >= len(self._blender_paths)
        self._custom_blender_stack.visible = is_custom
        
        if not is_custom and idx < len(self._blender_paths):
            # Auto-detected path selected
            _, path = self._blender_paths[idx]
            self._settings.set("/exts/omni.blender.bridge/blender_path", path)
    
    def _get_blender_path(self) -> str:
        """Get currently selected Blender path"""
        idx = self._blender_combo.model.get_item_value_model().get_value_as_int()
        if idx < len(self._blender_paths):
            return self._blender_paths[idx][1]
        return self._blender_field.model.get_value_as_string()
    
    # === Edit Mesh Section ===
    
    def _build_edit_geometry_row(self):
        """Build the geometry selection row for editing"""
        with ui.HStack(height=FIELD_HEIGHT, spacing=SPACING):
            ui.Label("Geometry to Edit", width=LABEL_WIDTH, style={"color": TEXT_NORMAL})
            ui.Button("+ Add", width=50, clicked_fn=self._add_edit_from_selection,
                      style=ADD_BTN_STYLE, tooltip="Add selected prims")
            self._edit_prims_field = ui.StringField(style=FIELD_STYLE)
            self._edit_prims_field.model.set_value("")
            ui.Button("⎀", width=24, clicked_fn=self._pick_edit_from_stage,
                      style=ADD_BTN_STYLE, tooltip="Replace with selection")
            ui.Circle(width=RESET_DOT_SIZE, height=RESET_DOT_SIZE, 
                      style={"background_color": 0xFF4A4A4A})
    
    def _get_edit_prim_paths(self) -> List[str]:
        """Get prim paths from the edit text field"""
        text = self._edit_prims_field.model.get_value_as_string().strip()
        if not text:
            return []
        paths = [p.strip() for p in text.replace('\n', ',').split(',')]
        return [p for p in paths if p]
    
    def _set_edit_prim_paths(self, paths: List[str]):
        """Set prim paths in the edit text field"""
        self._edit_prims_field.model.set_value(", ".join(paths))
    
    def _add_edit_from_selection(self):
        """Add prims from current stage selection to edit field"""
        self._add_prims_to_field(self._get_edit_prim_paths, self._set_edit_prim_paths)
    
    def _pick_edit_from_stage(self):
        """Replace edit field with current stage selection"""
        self._pick_prims_to_field(self._set_edit_prim_paths)
    
    # === Shared Prim Helpers ===
    
    def _add_prims_to_field(self, getter, setter):
        """Add prims from selection to a field"""
        ctx = omni.usd.get_context()
        selection = ctx.get_selection()
        paths = selection.get_selected_prim_paths()
        
        if not paths:
            carb.log_info("[BlenderBridge] No prims selected")
            return
        
        stage = ctx.get_stage()
        current = getter()
        added = 0
        
        for path in paths:
            if path in current:
                continue
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                if prim.IsA(UsdGeom.Xform) or prim.IsA(UsdGeom.Mesh):
                    current.append(path)
                    added += 1
        
        setter(current)
        carb.log_info(f"[BlenderBridge] Added {added} prim(s)")
    
    def _pick_prims_to_field(self, setter):
        """Replace field with current stage selection"""
        ctx = omni.usd.get_context()
        selection = ctx.get_selection()
        paths = selection.get_selected_prim_paths()
        
        if paths:
            setter(list(paths))
            carb.log_info(f"[BlenderBridge] Set {len(paths)} prim(s)")
    
    def _browse_blender(self):
        """Open file picker for Blender executable"""
        try:
            from omni.kit.window.filepicker import FilePickerDialog
            dialog = FilePickerDialog(
                "Select Blender Executable",
                apply_button_label="Select",
                click_apply_handler=self._on_blender_selected,
                file_extension_options=[("*.exe", "Executable")]
            )
            dialog.show()
        except Exception as e:
            carb.log_warn(f"[BlenderBridge] File picker error: {e}")
    
    def _on_blender_selected(self, filename: str, dirname: str):
        """Callback when Blender path is selected"""
        path = os.path.join(dirname, filename)
        self._blender_field.model.set_value(path)
        self._settings.set("/exts/omni.blender.bridge/blender_path", path)
    
    # === Helpers ===
    
    def _get_default_output_dir(self) -> str:
        """Get default output directory based on current stage"""
        from urllib.parse import urlparse, unquote
        ctx = omni.usd.get_context()
        stage_url = ctx.get_stage_url()
        if stage_url and not stage_url.startswith("anon:"):
            parsed = urlparse(stage_url)
            if parsed.scheme == "file":
                # file:///C:/path or file:/C:/path -> C:/path
                local_path = unquote(parsed.path)
                # On Windows, strip leading slash from /C:/...
                if len(local_path) > 2 and local_path[0] == '/' and local_path[2] == ':':
                    local_path = local_path[1:]
                stage_dir = os.path.dirname(local_path)
            elif parsed.scheme == "omniverse":
                stage_dir = os.path.dirname(parsed.path)
            else:
                return ""
            return os.path.normpath(os.path.join(stage_dir, "Textures"))
        return ""
    
    # === Edit Mesh Execution ===
    
    def _execute_edit_mesh(self):
        """Handle Edit Mesh button click - either start editing or finish"""
        if self._edit_mesh_process is not None:
            # Currently editing - finish and import
            asyncio.ensure_future(self._finish_edit_mesh())
        else:
            # Start editing
            asyncio.ensure_future(self._start_edit_mesh())
    
    async def _start_edit_mesh(self):
        """Export meshes and open Blender GUI"""
        prim_paths = self._get_edit_prim_paths()
        if not prim_paths:
            carb.log_warn("[BlenderBridge] No geometry specified for editing")
            return
        
        blender_path = self._get_blender_path()
        if not blender_path or not os.path.exists(blender_path):
            carb.log_warn("[BlenderBridge] Blender path not set or invalid")
            return
        
        try:
            self._edit_btn.text = "Exporting..."
            
            ctx = omni.usd.get_context()
            stage = ctx.get_stage()
            
            # Count meshes before export for validation
            self._edit_mesh_count = self._count_meshes(stage, prim_paths)
            self._edit_mesh_prim_paths = prim_paths
            
            # Export to temp location
            output_dir = self._get_default_output_dir()
            if not output_dir:
                import tempfile
                output_dir = tempfile.mkdtemp(prefix="blender_bridge_")
            os.makedirs(output_dir, exist_ok=True)
            
            export_path = os.path.join(output_dir, "edit_mesh_export.usd")
            
            # Export meshes
            self._edit_mesh_export_path = await export_meshes_to_usd(
                stage, prim_paths, output_path=export_path
            )
            
            if not self._edit_mesh_export_path:
                self._edit_btn.text = "Error: Export failed"
                await asyncio.sleep(2.0)
                self._edit_btn.text = "Execute - Edit Mesh"
                return
            
            # Get script path (resolve symlinks: panel.py -> bridge -> blender -> omni -> omni.blender.bridge -> exts -> root)
            real_file = os.path.realpath(__file__)
            ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(real_file)))))  # exts/
            baker_dir = os.path.dirname(ext_dir)  # baker/
            script_path = os.path.join(baker_dir, "scripts", "edit_mesh_session.py")
            carb.log_info(f"[BlenderBridge] Script path: {script_path}")
            carb.log_info(f"[BlenderBridge] Script exists: {os.path.exists(script_path)}")
            carb.log_info(f"[BlenderBridge] Export path: {self._edit_mesh_export_path}")
            carb.log_info(f"[BlenderBridge] Export exists: {os.path.exists(self._edit_mesh_export_path)}")
            
            if not os.path.exists(script_path):
                self._edit_btn.text = "Error: Script not found"
                carb.log_error(f"[BlenderBridge] Script not found at: {script_path}")
                await asyncio.sleep(2.0)
                self._edit_btn.text = "Execute - Edit Mesh"
                return
            
            # Start Blender with GUI
            self._edit_btn.text = "Opening Blender..."
            
            cmd = [
                blender_path,
                "--python", script_path,
                "--",
                "--input", self._edit_mesh_export_path,
            ]
            
            carb.log_info(f"[BlenderBridge] Starting Blender: {' '.join(cmd)}")
            self._edit_mesh_process = subprocess.Popen(cmd)
            
            # Change button to "Done editing"
            self._edit_btn.text = "Done editing"
            
            # Start watching for Blender close
            asyncio.ensure_future(self._watch_blender_process())
            
        except Exception as e:
            carb.log_error(f"[BlenderBridge] Edit mesh error: {e}")
            self._edit_btn.text = f"Error: {e}"
            await asyncio.sleep(2.0)
            self._edit_btn.text = "Execute - Edit Mesh"
            self._edit_mesh_process = None
    
    async def _watch_blender_process(self):
        """Watch Blender process and reset button when it closes externally"""
        while True:
            await asyncio.sleep(0.5)
            proc = self._edit_mesh_process
            if proc is None:
                # Process was cleared by _finish_edit_mesh, stop watching
                break
            if proc.poll() is not None:
                # Blender was closed externally (not via "Done editing")
                carb.log_warn("[BlenderBridge] Blender closed without completing edit - original mesh unchanged")
                
                # Keep backup path for user reference
                backup_path = self._edit_mesh_export_path
                if backup_path:
                    carb.log_info(f"[BlenderBridge] Backup available at: {backup_path}")
                
                self._edit_mesh_process = None
                self._edit_mesh_export_path = None
                self._edit_btn.text = "Canceled - Original kept"
                await asyncio.sleep(2.0)
                self._edit_btn.text = "Execute - Edit Mesh"
                break
    
    async def _finish_edit_mesh(self):
        """Import edited meshes back and close Blender"""
        try:
            self._edit_btn.text = "Importing..."
            
            # Export path for edited mesh
            output_dir = os.path.dirname(self._edit_mesh_export_path)
            edited_path = os.path.join(output_dir, "edit_mesh_result.usd")
            
            # Tell Blender to export and quit via signal file
            signal_file = os.path.join(output_dir, ".export_signal")
            with open(signal_file, "w") as f:
                f.write(edited_path)
            
            # Wait for Blender to export (poll for result file)
            for _ in range(60):  # 30 second timeout
                await asyncio.sleep(0.5)
                if os.path.exists(edited_path):
                    break
            
            # Kill Blender if still running
            if self._edit_mesh_process and self._edit_mesh_process.poll() is None:
                self._edit_mesh_process.terminate()
                await asyncio.sleep(0.5)
            
            self._edit_mesh_process = None
            
            # Validate mesh count
            if os.path.exists(edited_path):
                from pxr import Usd
                edited_stage = Usd.Stage.Open(edited_path)
                edited_count = sum(1 for p in edited_stage.Traverse() if p.IsA(UsdGeom.Mesh))
                
                if edited_count != self._edit_mesh_count:
                    self._edit_btn.text = f"Error: Mesh count changed ({self._edit_mesh_count}→{edited_count})"
                    await asyncio.sleep(3.0)
                    self._edit_btn.text = "Execute - Edit Mesh"
                    return
                
                # Import meshes back
                ctx = omni.usd.get_context()
                stage = ctx.get_stage()
                
                # Patch geometry back
                from .mesh_patcher import patch_meshes_to_stage
                patched = patch_meshes_to_stage(stage, self._edit_mesh_prim_paths, edited_path)
                
                self._edit_btn.text = f"Done! Updated {patched} mesh(es)"
            else:
                self._edit_btn.text = "Error: No result from Blender"
            
        except Exception as e:
            carb.log_error(f"[BlenderBridge] Import error: {e}")
            self._edit_btn.text = f"Error: {e}"
        finally:
            await asyncio.sleep(2.0)
            self._edit_btn.text = "Execute - Edit Mesh"
            self._edit_mesh_process = None
            self._edit_mesh_export_path = None
    
    def _count_meshes(self, stage, prim_paths: List[str]) -> int:
        """Count total meshes in the given prim paths"""
        count = 0
        for path in prim_paths:
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            for p in Usd.PrimRange(prim):
                if p.IsA(UsdGeom.Mesh):
                    count += 1
        return count
    
    def _on_visibility_changed(self, visible: bool):
        """Called when window visibility changes"""
        pass
    
    def shutdown(self):
        """Cleanup on extension shutdown"""
        # Kill any running Blender process
        if self._edit_mesh_process and self._edit_mesh_process.poll() is None:
            self._edit_mesh_process.terminate()
            self._edit_mesh_process = None
        
        omni.kit.menu.utils.remove_menu_items(self._menu_items, "vtools")
        self._window = None
