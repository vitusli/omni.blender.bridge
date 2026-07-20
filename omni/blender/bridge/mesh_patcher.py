# SPDX-License-Identifier: Apache-2.0
"""
Mesh Patcher for Blender Bridge

Patches edited mesh geometry from Blender back to original USD stage.
Uses face vertex count for matching (since Blender changes mesh paths).
"""

from typing import List

import carb
from pxr import Sdf, Usd, UsdGeom, Vt


def _expected_normal_count(interp: str, points_count: int, face_count: int, face_index_count: int) -> int:
    if interp == UsdGeom.Tokens.vertex:
        return points_count
    if interp == UsdGeom.Tokens.faceVarying:
        return face_index_count
    if interp == UsdGeom.Tokens.uniform:
        return face_count
    if interp == UsdGeom.Tokens.constant:
        return 1
    return 0


def path_segments(path_str: str) -> List[str]:
    """Split path into segments, filtering empty strings."""
    return [s for s in path_str.split('/') if s]


def _normalize_paths(paths: List[str]) -> List[str]:
    unique = []
    for path in sorted(set(paths), key=lambda p: len(p)):
        path_obj = Sdf.Path(path)
        if any(path_obj.HasPrefix(Sdf.Path(parent)) for parent in unique):
            continue
        unique.append(path)
    return unique


def match_score(orig_segments: List[str], edit_segments: List[str], 
                orig_face_count: int = 0, edit_face_count: int = 0) -> int:
    """
    Calculate match score between original and edited mesh paths.
    Higher score = better match.
    
    Primary: path segments
    Secondary: face vertex count as confirmation (but allow changes)
    """
    orig_parents = orig_segments[:-1] if len(orig_segments) > 1 else orig_segments
    edit_parents = edit_segments[:-1] if len(edit_segments) > 1 else edit_segments
    
    score = 0
    
    # Score for matching parent segments
    for seg in orig_parents:
        if seg in edit_parents:
            score += 100
    
    # Score for matching mesh name
    orig_name = orig_segments[-1] if orig_segments else ""
    edit_name = edit_segments[-1] if edit_segments else ""
    
    if orig_name == edit_name:
        score += 500  # Strong match on name
    elif edit_name.startswith(orig_name):
        score += 250
    elif orig_name in edit_name:
        score += 100
    
    return score


def patch_meshes_to_stage(stage: Usd.Stage, prim_paths: List[str], edited_usd_path: str) -> int:
    """
    Patch edited mesh geometry back to the original stage.
    
    Args:
        stage: Target USD stage to patch
        prim_paths: List of prim paths that were exported for editing
        edited_usd_path: Path to the edited mesh USD file
    
    Returns:
        Number of meshes patched
    """
    # Open edited mesh USD
    edit_stage = Usd.Stage.Open(edited_usd_path)
    if not edit_stage:
        carb.log_error(f"[BlenderBridge] Could not open edited result: {edited_usd_path}")
        return 0
    
    prim_paths = _normalize_paths(prim_paths)

    # Collect meshes from edited USD
    edit_meshes = []
    for prim in edit_stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            face_counts = mesh.GetFaceVertexCountsAttr().Get()
            total_face_verts = sum(face_counts) if face_counts else 0
            edit_meshes.append((prim, total_face_verts))
    
    carb.log_info(f"[BlenderBridge] Found {len(edit_meshes)} meshes in edited result")
    
    patched = 0

    # Author into current edit target (typically a session/override layer) when available.
    current_target = stage.GetEditTarget()
    target_layer = current_target.GetLayer() if current_target else None
    if target_layer:
        carb.log_info(f"[BlenderBridge] Patching into edit target layer: {target_layer.identifier}")
    else:
        carb.log_warn("[BlenderBridge] No explicit edit target layer; patching into default stage target")
    
    # Find and patch meshes
    for prim_path in prim_paths:
        root_prim = stage.GetPrimAtPath(prim_path)
        if not root_prim:
            continue
        
        for prim in Usd.PrimRange(root_prim):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            
            orig_path = str(prim.GetPath())
            orig_segs = path_segments(orig_path)
            
            # Get stage mesh face count
            stage_mesh = UsdGeom.Mesh(prim)
            stage_face_counts = stage_mesh.GetFaceVertexCountsAttr().Get()
            stage_face_verts = sum(stage_face_counts) if stage_face_counts else 0
            
            # Find best matching edited mesh by name
            best_match = None
            best_score = 0
            
            for edit_prim, edit_face_verts in edit_meshes:
                edit_path = str(edit_prim.GetPath())
                edit_segs = path_segments(edit_path)
                score = match_score(orig_segs, edit_segs, stage_face_verts, edit_face_verts)
                if score > best_score:
                    best_score = score
                    best_match = edit_prim
            
            if best_match and best_score > 0:
                count = _copy_geometry(prim, best_match, orig_path)
                if count > 0:
                    patched += 1
                    try:
                        edit_meshes = [entry for entry in edit_meshes if entry[0] != best_match]
                    except Exception:
                        pass
    
    return patched


def _copy_geometry(stage_prim: Usd.Prim, edit_prim: Usd.Prim, orig_path: str) -> int:
    """
    Copy geometry from edited mesh to stage mesh.
    
    Copies: points, face vertex counts, face vertex indices, normals
    
    Returns 1 on success, 0 on failure.
    """
    edit_mesh = UsdGeom.Mesh(edit_prim)
    stage_mesh = UsdGeom.Mesh(stage_prim)
    
    try:
        changed = False

        # Ensure destination prim spec exists on current edit target as an over.
        try:
            stage = stage_prim.GetStage()
            target = stage.GetEditTarget()
            target_layer = target.GetLayer() if target else None
            if target_layer:
                stage_path = str(stage_prim.GetPath())
                mapped_path = target.MapToSpecPath(stage_prim.GetPath())
                spec_path = str(mapped_path) if mapped_path and mapped_path != Sdf.Path.emptyPath else stage_path
                if not target_layer.GetPrimAtPath(spec_path):
                    prim_spec = Sdf.CreatePrimInLayer(target_layer, spec_path)
                    if prim_spec:
                        prim_spec.specifier = Sdf.SpecifierOver
        except Exception as e:
            carb.log_warn(f"[BlenderBridge] Could not precreate over for {orig_path}: {e}")

        # Copy points (vertices)
        points = edit_mesh.GetPointsAttr().Get()
        if points:
            stage_points = stage_mesh.GetPointsAttr().Get()
            if stage_points != points:
                stage_mesh.GetPointsAttr().Set(points)
                changed = True
        
        # Copy face vertex counts
        face_counts = edit_mesh.GetFaceVertexCountsAttr().Get()
        if face_counts:
            stage_face_counts = stage_mesh.GetFaceVertexCountsAttr().Get()
            if stage_face_counts != face_counts:
                stage_mesh.GetFaceVertexCountsAttr().Set(face_counts)
                changed = True
        
        # Copy face vertex indices
        face_indices = edit_mesh.GetFaceVertexIndicesAttr().Get()
        if face_indices:
            stage_face_indices = stage_mesh.GetFaceVertexIndicesAttr().Get()
            if stage_face_indices != face_indices:
                stage_mesh.GetFaceVertexIndicesAttr().Set(face_indices)
                changed = True
        
        # Copy normals if present
        normals = edit_mesh.GetNormalsAttr().Get()
        if normals:
            edit_interp = edit_mesh.GetNormalsInterpolation()
            face_counts = face_counts if face_counts else stage_mesh.GetFaceVertexCountsAttr().Get()
            face_indices = face_indices if face_indices else stage_mesh.GetFaceVertexIndicesAttr().Get()
            points = points if points else stage_mesh.GetPointsAttr().Get()
            expected_count = _expected_normal_count(
                edit_interp,
                len(points) if points else 0,
                len(face_counts) if face_counts else 0,
                len(face_indices) if face_indices else 0,
            )

            # Skip invalid normals that would corrupt Hydra/OV mesh import.
            if expected_count > 0 and len(normals) != expected_count:
                carb.log_warn(
                    f"[BlenderBridge] Skipping normals for {orig_path}: "
                    f"got {len(normals)}, expected {expected_count} for {edit_interp} interpolation"
                )
            else:
                stage_normals = stage_mesh.GetNormalsAttr().Get()
                if stage_normals != normals:
                    stage_mesh.GetNormalsAttr().Set(normals)
                    changed = True

                stage_interp = stage_mesh.GetNormalsInterpolation()
                if edit_interp != stage_interp:
                    stage_mesh.SetNormalsInterpolation(edit_interp)
                    changed = True

        if changed:
            carb.log_info(f"[BlenderBridge] Patched geometry: {orig_path}")
            return 1

        carb.log_info(f"[BlenderBridge] No geometry changes: {orig_path}")
        return 0
        
    except Exception as e:
        carb.log_warn(f"[BlenderBridge] Failed to copy geometry for {orig_path}: {e}")
        return 0
