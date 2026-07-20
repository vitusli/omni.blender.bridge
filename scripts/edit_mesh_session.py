#!/usr/bin/env python3
"""Interactive Blender edit session for Omni Blender Bridge.

Usage (from Isaac Sim):
    blender --python edit_mesh_session.py -- --input <path_to_usd>

Behavior:
- Imports the input USD into the current Blender session.
- Keeps Blender open for manual edits.
- Polls for a `.export_signal` file next to the input USD.
- When signaled, exports to the requested USD path and quits Blender.
"""

import argparse
import os
import tempfile
import sys
from typing import Optional

import addon_utils
import bpy


LOG_PREFIX = "[BlenderBridgeScript]"
SOURCE_LAYER_KEY = "bridge_source_layer"
BASELINE_LAYER_KEY = "bridge_baseline_layer"
GEOM_ATTRS = [
    "points",
    "faceVertexCounts",
    "faceVertexIndices",
    "primvars:st",
    "primvars:st:indices",
]


def _safe_relpath(path: str, start: str) -> str:
    try:
        rel = os.path.relpath(path, start)
        return rel.replace("\\", "/")
    except Exception:
        return path.replace("\\", "/")


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}")


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []

    parser = argparse.ArgumentParser(description="Blender Bridge edit session")
    parser.add_argument("--input", required=True, help="Input USD file to edit")
    parser.add_argument("--poll", type=float, default=0.5, help="Signal poll interval in seconds")
    return parser.parse_args(argv)


def ensure_usd_support() -> bool:
    if hasattr(bpy.ops.wm, "usd_import") and hasattr(bpy.ops.wm, "usd_export"):
        return True

    try:
        enabled_default, enabled_state = addon_utils.check("io_scene_usd")
        if not (enabled_default or enabled_state):
            addon_utils.enable("io_scene_usd", default_set=False, persistent=False)
    except Exception as exc:
        log(f"Failed to enable USD addon: {exc}")

    return hasattr(bpy.ops.wm, "usd_import") and hasattr(bpy.ops.wm, "usd_export")


def ensure_pxr_support() -> bool:
    try:
        bpy.utils.expose_bundled_modules()
        import pxr  # noqa: F401
        return True
    except Exception as exc:
        log(f"pxr modules unavailable: {exc}")
        return False


def import_input_usd(input_path: str) -> bool:
    if not os.path.exists(input_path):
        log(f"Input USD not found: {input_path}")
        return False

    if not ensure_usd_support():
        log("USD import/export operators are not available in this Blender build")
        return False

    try:
        result = bpy.ops.wm.usd_import(filepath=input_path)
        obj_count = len(bpy.data.objects)
        log(f"Imported USD: {input_path}")
        log(f"Import result: {result}, objects in scene: {obj_count}")
        bpy.context.scene[SOURCE_LAYER_KEY] = input_path
        return True
    except Exception as exc:
        log(f"USD import failed: {exc}")
        return False


def create_baseline_snapshot() -> Optional[str]:
    """Export a baseline USD right after import for robust delta comparison."""
    try:
        baseline_dir = tempfile.mkdtemp(prefix="blender_bridge_baseline_")
        baseline_path = os.path.join(baseline_dir, "baseline_full.usd")
        bpy.ops.wm.usd_export(filepath=baseline_path)
        bpy.context.scene[BASELINE_LAYER_KEY] = baseline_path
        log(f"Created baseline snapshot: {baseline_path}")
        return baseline_path
    except Exception as exc:
        log(f"Failed to create baseline snapshot: {exc}")
        return None


def _values_different(a, b) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    try:
        return a != b
    except Exception:
        return str(a) != str(b)


def _copy_attr_if_changed(src_prim, dst_prim, base_prim, attr_name: str) -> bool:
    from pxr import Sdf

    src_attr = src_prim.GetAttribute(attr_name)
    if not src_attr:
        return False
    src_val = src_attr.Get()
    if src_val is None:
        return False

    base_attr = base_prim.GetAttribute(attr_name) if base_prim else None
    base_val = base_attr.Get() if base_attr and base_attr.HasValue() else None

    if not _values_different(src_val, base_val):
        return False

    dst_attr = dst_prim.CreateAttribute(attr_name, src_attr.GetTypeName())
    dst_attr.Set(src_val)
    return True


def _copy_xform_if_changed(src_prim, dst_prim, base_prim) -> bool:
    changed = False

    for src_attr in src_prim.GetAttributes():
        name = src_attr.GetName()
        if not (name.startswith("xformOp:") or name == "xformOpOrder"):
            continue

        src_val = src_attr.Get()
        if src_val is None:
            continue

        base_attr = base_prim.GetAttribute(name) if base_prim else None
        base_val = base_attr.Get() if base_attr and base_attr.HasValue() else None

        if _values_different(src_val, base_val):
            dst_attr = dst_prim.CreateAttribute(name, src_attr.GetTypeName())
            dst_attr.Set(src_val)
            changed = True

    return changed


def _path_segments(path) -> list[str]:
    return [seg for seg in str(path).split("/") if seg]


def _normalize_name(name: str) -> str:
    # Blender duplicate suffixes: Cube.001 or Cube_001
    if "." in name:
        head, tail = name.rsplit(".", 1)
        if tail.isdigit():
            return head
    if "_" in name:
        head, tail = name.rsplit("_", 1)
        if tail.isdigit():
            return head
    return name


def _mesh_match_score(src_path, edit_path, src_mesh, edit_mesh) -> int:
    src_segs = _path_segments(src_path)
    edit_segs = _path_segments(edit_path)
    if not src_segs or not edit_segs:
        return -1

    score = 0
    src_parent = src_segs[:-1]
    edit_parent = edit_segs[:-1]

    # Strong parent-chain signal for duplicate leaf names.
    for seg in src_parent:
        if seg in edit_parent:
            score += 100

    src_leaf = src_segs[-1]
    edit_leaf = edit_segs[-1]
    if src_leaf == edit_leaf:
        score += 500
    elif _normalize_name(src_leaf) == _normalize_name(edit_leaf):
        score += 350
    elif _normalize_name(src_leaf) in _normalize_name(edit_leaf):
        score += 150

    src_counts = src_mesh.GetFaceVertexCountsAttr().Get()
    edit_counts = edit_mesh.GetFaceVertexCountsAttr().Get()
    if src_counts and edit_counts and len(src_counts) == len(edit_counts):
        score += 30

    return score


def _find_object_for_prim_path(prim_path: str):
    # First try explicit metadata if available.
    for obj in bpy.data.objects:
        if obj.get("prim_path") == prim_path:
            return obj

    # Fallback: resolve by parent chain names from prim path.
    segs = [seg for seg in prim_path.split("/") if seg]
    if not segs:
        return None

    leaf = segs[-1]
    candidates = [obj for obj in bpy.data.objects if obj.name == leaf]
    for obj in candidates:
        chain = []
        cur = obj
        while cur:
            chain.append(cur.name)
            cur = cur.parent
        chain.reverse()
        if chain[-len(segs) :] == segs:
            return obj

    return None


def _is_close(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


def apply_auto_edit_commands(source_path: str) -> None:
    """Apply optional scripted edits from `.auto_edit_commands` in session folder.

    Command syntax:
      flatten_cube <prim_path> <delta_z_cm>
    """
    cmd_file = os.path.join(os.path.dirname(source_path), ".auto_edit_commands")
    if not os.path.exists(cmd_file):
        return

    stage_meters_per_unit = 1.0
    try:
        if ensure_pxr_support():
            from pxr import Usd, UsdGeom

            source_stage = Usd.Stage.Open(source_path)
            if source_stage:
                stage_meters_per_unit = UsdGeom.GetStageMetersPerUnit(source_stage) or 1.0
    except Exception as exc:
        log(f"Could not read stage metersPerUnit: {exc}")

    try:
        with open(cmd_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
    except Exception as exc:
        log(f"Failed to read auto edit commands: {exc}")
        return

    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            log(f"Invalid auto edit command: {line}")
            continue

        cmd, prim_path, value = parts[0], parts[1], parts[2]
        if cmd != "flatten_cube":
            log(f"Unknown auto edit command: {cmd}")
            continue

        try:
            delta_cm = float(value)
        except ValueError:
            log(f"Invalid flatten value in command: {line}")
            continue

        obj = _find_object_for_prim_path(prim_path)
        if not obj or obj.type != "MESH" or not obj.data:
            log(f"Could not resolve mesh for {prim_path}")
            continue

        mesh = obj.data
        if not mesh.vertices:
            log(f"Mesh has no vertices: {prim_path}")
            continue

        z_values = [v.co.z for v in mesh.vertices]
        top_z = max(z_values)

        # cm -> meters -> stage units.
        delta_stage_units = (delta_cm / 100.0) / stage_meters_per_unit
        target_z = top_z - delta_stage_units

        changed = 0
        for v in mesh.vertices:
            if _is_close(v.co.z, top_z, eps=1e-5):
                v.co.z = target_z
                changed += 1

        mesh.update()
        log(
            f"Applied flatten_cube on {prim_path}: top_z {top_z:.6f} -> {target_z:.6f}, "
            f"moved {changed} vertices"
        )

    try:
        os.remove(cmd_file)
    except Exception:
        pass


def export_delta_layer(source_path: str, target_path: str) -> bool:
    if not ensure_pxr_support():
        return False

    from pxr import Sdf, Usd, UsdGeom

    tmp_dir = tempfile.mkdtemp(prefix="blender_bridge_export_")
    tmp_export = os.path.join(tmp_dir, "edited_full.usd")

    try:
        bpy.ops.wm.usd_export(filepath=tmp_export)
    except Exception as exc:
        log(f"Temporary USD export failed: {exc}")
        return False

    baseline_path = bpy.context.scene.get(BASELINE_LAYER_KEY, "")
    if not baseline_path or not os.path.exists(baseline_path):
        log("Baseline snapshot missing, cannot compute reliable delta")
        return False

    src_stage = Usd.Stage.Open(source_path)
    baseline_stage = Usd.Stage.Open(baseline_path)
    edit_stage = Usd.Stage.Open(tmp_export)
    if not src_stage or not baseline_stage or not edit_stage:
        log("Failed to open source/baseline/edited USD stage for delta export")
        return False

    out_dir = os.path.dirname(target_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    out_stage = Usd.Stage.CreateNew(target_path)
    if not out_stage:
        log(f"Failed to create output USD layer: {target_path}")
        return False

    out_layer = out_stage.GetRootLayer()
    out_layer.subLayerPaths = [_safe_relpath(source_path, out_dir or os.getcwd())]

    src_meshes = [p for p in baseline_stage.Traverse() if p.GetTypeName() == "Mesh"]
    edit_meshes = [p for p in edit_stage.Traverse() if p.GetTypeName() == "Mesh"]
    unmatched_edit = set(range(len(edit_meshes)))

    changed_meshes = 0
    for src_prim in src_meshes:
        src_path = src_prim.GetPath()
        src_mesh = UsdGeom.Mesh(src_prim)

        best_idx = None
        best_score = -1
        for idx in unmatched_edit:
            edit_prim = edit_meshes[idx]
            edit_mesh = UsdGeom.Mesh(edit_prim)
            score = _mesh_match_score(src_path, edit_prim.GetPath(), src_mesh, edit_mesh)
            if score > best_score:
                best_idx = idx
                best_score = score

        if best_idx is None:
            continue

        unmatched_edit.remove(best_idx)
        edit_prim = edit_meshes[best_idx]

        out_prim = out_stage.OverridePrim(src_path)
        changed = False
        for attr_name in GEOM_ATTRS:
            if _copy_attr_if_changed(edit_prim, out_prim, src_prim, attr_name):
                changed = True

        if _copy_xform_if_changed(edit_prim, out_prim, src_prim):
            changed = True

        if changed:
            changed_meshes += 1

    out_layer.Save()
    log(f"Exported override USD: {target_path} (changed meshes: {changed_meshes})")
    return True


def read_signal_path(signal_file: str) -> Optional[str]:
    try:
        with open(signal_file, "r", encoding="utf-8") as f:
            target = f.read().strip()
        return target or None
    except Exception as exc:
        log(f"Failed to read signal file: {exc}")
        return None


def export_and_quit(target_path: str) -> None:
    try:
        out_dir = os.path.dirname(target_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        source_path = bpy.context.scene.get(SOURCE_LAYER_KEY, "")

        if source_path and os.path.exists(source_path):
            if not export_delta_layer(source_path, target_path):
                bpy.ops.wm.usd_export(filepath=target_path)
                log(f"Fallback full USD export: {target_path}")
        else:
            bpy.ops.wm.usd_export(filepath=target_path)
            log(f"Fallback full USD export (no source snapshot): {target_path}")
    except Exception as exc:
        log(f"USD export failed: {exc}")
        return

    try:
        bpy.ops.wm.quit_blender()
    except Exception as exc:
        log(f"Failed to quit Blender cleanly: {exc}")


def main() -> None:
    args = parse_args()
    input_path = os.path.abspath(args.input)
    session_dir = os.path.dirname(input_path)
    signal_file = os.path.join(session_dir, ".export_signal")

    log(f"Input: {input_path}")
    log(f"Signal file: {signal_file}")

    # Headless mode support for automated tests.
    if bpy.app.background:
        if not import_input_usd(input_path):
            return
        create_baseline_snapshot()
        apply_auto_edit_commands(input_path)
        target_path = read_signal_path(signal_file)
        if target_path:
            try:
                os.remove(signal_file)
            except Exception:
                pass
            export_and_quit(os.path.abspath(target_path))
        else:
            log("Background mode: no export signal file found, exiting")
        return

    def _poll_signal() -> Optional[float]:
        if not os.path.exists(signal_file):
            return max(0.1, float(args.poll))

        target_path = read_signal_path(signal_file)
        try:
            os.remove(signal_file)
        except Exception:
            pass

        if not target_path:
            log("Signal received without output path; waiting for next signal")
            return max(0.1, float(args.poll))

        export_and_quit(os.path.abspath(target_path))
        return None

    def _init_session() -> Optional[float]:
        if not import_input_usd(input_path):
            return None

        create_baseline_snapshot()

        apply_auto_edit_commands(input_path)

        bpy.app.timers.register(_poll_signal, first_interval=max(0.1, float(args.poll)))
        log("Ready for editing. Waiting for export signal...")
        return None

    # Run import slightly after startup so Blender UI/operators are fully ready.
    bpy.app.timers.register(_init_session, first_interval=0.2)


if __name__ == "__main__":
    main()
