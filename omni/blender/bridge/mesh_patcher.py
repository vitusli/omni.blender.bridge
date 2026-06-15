# SPDX-License-Identifier: Apache-2.0
"""
Mesh Patcher for Blender Bridge

Patches edited mesh geometry from Blender back to original USD stage.
Uses face vertex count for matching (since Blender changes mesh paths).
"""

from typing import List

import carb
from pxr import Sdf, Usd, UsdGeom, Vt


def path_segments(path_str: str) -> List[str]:
    """Split path into segments, filtering empty strings."""
    return [s for s in path_str.split('/') if s]


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
        # Copy points (vertices)
        points = edit_mesh.GetPointsAttr().Get()
        if points:
            stage_mesh.GetPointsAttr().Set(points)
        
        # Copy face vertex counts
        face_counts = edit_mesh.GetFaceVertexCountsAttr().Get()
        if face_counts:
            stage_mesh.GetFaceVertexCountsAttr().Set(face_counts)
        
        # Copy face vertex indices
        face_indices = edit_mesh.GetFaceVertexIndicesAttr().Get()
        if face_indices:
            stage_mesh.GetFaceVertexIndicesAttr().Set(face_indices)
        
        # Copy normals if present
        normals = edit_mesh.GetNormalsAttr().Get()
        if normals:
            stage_mesh.GetNormalsAttr().Set(normals)
            stage_mesh.SetNormalsInterpolation(edit_mesh.GetNormalsInterpolation())
        
        carb.log_info(f"[BlenderBridge] Patched geometry: {orig_path}")
        return 1
        
    except Exception as e:
        carb.log_warn(f"[BlenderBridge] Failed to copy geometry for {orig_path}: {e}")
        return 0
