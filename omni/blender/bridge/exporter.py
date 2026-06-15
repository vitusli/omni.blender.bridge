# SPDX-License-Identifier: Apache-2.0
"""
USD Geometry Exporter for Blender Bridge

Exports selected meshes to a temporary USD file for Blender processing.
Strips physics, joints, and other non-geometry data.
"""

import os
import tempfile
from typing import List, Optional

import carb
from pxr import Usd, UsdGeom, Sdf


async def export_meshes_to_usd(
    stage: Usd.Stage,
    prim_paths: List[str],
    output_path: Optional[str] = None
) -> Optional[str]:
    """
    Export selected meshes to a clean USD file.
    
    Args:
        stage: Source USD stage
        prim_paths: List of prim paths to export
        output_path: Optional output path. If None, uses temp directory.
    
    Returns:
        Path to exported USD, or None on failure
    """
    if not prim_paths:
        carb.log_warn("[BlenderBridge] No prim paths to export")
        return None
    
    # Determine output path
    if output_path is None:
        temp_dir = tempfile.mkdtemp(prefix="blender_bridge_")
        output_path = os.path.join(temp_dir, "geometry_export.usd")
    else:
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        # Create new stage for export
        export_stage = Usd.Stage.CreateNew(output_path)
        export_stage.SetMetadata("upAxis", stage.GetMetadata("upAxis") or "Y")
        
        # Copy metersPerUnit to preserve scale (Isaac Sim uses meters)
        meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        UsdGeom.SetStageMetersPerUnit(export_stage, meters_per_unit)
        carb.log_info(f"[BlenderBridge] Stage metersPerUnit: {meters_per_unit}")
        
        # Track copied prims for material extraction later
        mesh_count = 0
        
        for src_path in prim_paths:
            src_prim = stage.GetPrimAtPath(src_path)
            if not src_prim or not src_prim.IsValid():
                carb.log_warn(f"[BlenderBridge] Invalid prim: {src_path}")
                continue
            
            # Recursively copy geometry
            mesh_count += _copy_geometry_recursive(
                stage, export_stage, src_prim, Sdf.Path.absoluteRootPath
            )
        
        if mesh_count == 0:
            carb.log_warn("[BlenderBridge] No meshes found in selection")
            return None
        
        # Save
        export_stage.GetRootLayer().Save()
        carb.log_info(f"[BlenderBridge] Exported {mesh_count} meshes to {output_path}")
        
        return output_path
        
    except Exception as e:
        carb.log_error(f"[BlenderBridge] Export failed: {e}")
        return None


def _copy_geometry_recursive(
    src_stage: Usd.Stage,
    dst_stage: Usd.Stage, 
    src_prim: Usd.Prim,
    dst_parent_path: Sdf.Path
) -> int:
    """
    Recursively copy geometry prims (Xform, Mesh).
    
    Returns number of meshes copied.
    """
    mesh_count = 0
    
    # Determine destination path
    dst_path = dst_parent_path.AppendChild(src_prim.GetName())
    
    # Check prim type
    if src_prim.IsA(UsdGeom.Mesh):
        # Copy mesh
        _copy_mesh(src_stage, dst_stage, src_prim, dst_path)
        mesh_count += 1
        
    elif src_prim.IsA(UsdGeom.Xform) or src_prim.IsA(UsdGeom.Scope):
        # Create Xform container
        UsdGeom.Xform.Define(dst_stage, dst_path)
        
        # Copy transform if Xformable
        if src_prim.IsA(UsdGeom.Xformable):
            _copy_transform(src_prim, dst_stage.GetPrimAtPath(dst_path))
        
        # Recurse into children
        for child in src_prim.GetChildren():
            # Skip physics, joints, materials
            child_type = child.GetTypeName()
            if child_type in ["PhysicsRigidBodyAPI", "PhysicsJoint", "Material", "Shader"]:
                continue
            if "Physics" in child_type or "Joint" in child_type:
                continue
            
            mesh_count += _copy_geometry_recursive(
                src_stage, dst_stage, child, dst_path
            )
    
    return mesh_count


def _copy_mesh(
    src_stage: Usd.Stage,
    dst_stage: Usd.Stage,
    src_prim: Usd.Prim,
    dst_path: Sdf.Path
):
    """Copy a mesh prim with its geometry attributes"""
    src_mesh = UsdGeom.Mesh(src_prim)
    dst_mesh = UsdGeom.Mesh.Define(dst_stage, dst_path)
    
    # Copy geometry attributes
    attrs_to_copy = [
        "points",
        "faceVertexCounts", 
        "faceVertexIndices",
        "normals",
        "primvars:st",  # UVs
        "primvars:st:indices",
    ]
    
    for attr_name in attrs_to_copy:
        src_attr = src_prim.GetAttribute(attr_name)
        if src_attr and src_attr.HasValue():
            dst_attr = dst_mesh.GetPrim().CreateAttribute(
                attr_name, 
                src_attr.GetTypeName()
            )
            dst_attr.Set(src_attr.Get())
    
    # Copy transform
    _copy_transform(src_prim, dst_mesh.GetPrim())
    
    # Copy GeomSubsets (for material assignment info)
    for child in src_prim.GetChildren():
        if child.IsA(UsdGeom.Subset):
            _copy_geom_subset(child, dst_stage, dst_path)


def _copy_geom_subset(
    src_subset: Usd.Prim,
    dst_stage: Usd.Stage,
    parent_path: Sdf.Path
):
    """Copy a GeomSubset (material face groups)"""
    dst_path = parent_path.AppendChild(src_subset.GetName())
    dst_subset = UsdGeom.Subset.Define(dst_stage, dst_path)
    
    # Copy indices
    src_indices = src_subset.GetAttribute("indices")
    if src_indices and src_indices.HasValue():
        dst_subset.CreateIndicesAttr(src_indices.Get())
    
    # Copy element type
    src_elem = src_subset.GetAttribute("elementType")
    if src_elem and src_elem.HasValue():
        dst_subset.CreateElementTypeAttr(src_elem.Get())
    
    # Copy family name
    src_family = src_subset.GetAttribute("familyName")
    if src_family and src_family.HasValue():
        dst_subset.CreateFamilyNameAttr(src_family.Get())


def _copy_transform(src_prim: Usd.Prim, dst_prim: Usd.Prim):
    """Copy xformOp attributes from source to destination"""
    src_xformable = UsdGeom.Xformable(src_prim)
    dst_xformable = UsdGeom.Xformable(dst_prim)
    
    # Get all xform ops
    for op in src_xformable.GetOrderedXformOps():
        attr = op.GetAttr()
        if attr and attr.HasValue():
            # Create matching attribute on destination
            dst_attr = dst_prim.CreateAttribute(
                attr.GetName(),
                attr.GetTypeName()
            )
            dst_attr.Set(attr.Get())
    
    # Copy xformOpOrder
    src_order = src_prim.GetAttribute("xformOpOrder")
    if src_order and src_order.HasValue():
        dst_order = dst_prim.CreateAttribute("xformOpOrder", src_order.GetTypeName())
        dst_order.Set(src_order.Get())
