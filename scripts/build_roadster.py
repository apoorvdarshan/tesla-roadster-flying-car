"""
Tesla Roadster 2 "flying car" -- stylized Blender scene.

Reproduces the spirit of the 2026-10-15 SpaceX McGregor demo: a sleek
electric hypercar rides a huge curved suction ramp, goes upside down,
launches on compressed-air thrusters, kick-flips in mid-air and lands.

Stages:
    blender -b -P build_roadster.py -- --stage car      # studio turntable still
    blender -b -P build_roadster.py -- --stage preview  # quick scene check
    blender -b -P build_roadster.py -- --stage hero     # final poster + .blend
    blender -b -P build_roadster.py -- --stage anim     # mp4 animation
    blender -b -P build_roadster.py -- --stage save     # .blend only
"""

import bpy
import bmesh
import math
import sys
import os
from math import cos, sin, copysign, radians, pi, sqrt
from mathutils import Vector

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RENDERS = os.path.join(ROOT, "renders")
os.makedirs(RENDERS, exist_ok=True)

RAMP_R = 9.0                 # ramp arc radius (m)
RAMP_W = 3.6                 # ramp width (m)
RAMP_THK = 0.35              # ramp slab thickness (m)
RAMP_A_END = radians(203.0)  # angle where the car leaves the ramp

FPS = 30
F_DRIVE_END = 96             # 0..96  : on the ramp
F_LAND = 175                 # flight ends (ground contact)
F_TOTAL = 232                # roll-out end
V_LAUNCH = 21.0              # m/s at ramp exit

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STAGE = "all"
for i, a in enumerate(argv):
    if a == "--stage" and i + 1 < len(argv):
        STAGE = argv[i + 1]

CAR_PARTS = []


# ----------------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------------
def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.objects,
                  bpy.data.cameras, bpy.data.lights, bpy.data.worlds,
                  bpy.data.actions, bpy.data.images):
        for b in list(block):
            try:
                block.remove(b)
            except Exception:
                pass


def mat(name, base=(0.8, 0.8, 0.8, 1), metallic=0.0, rough=0.4,
        emission=None, emit_strength=0.0, coat=0.0, transmission=0.0,
        ior=1.45, alpha=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    ins = bsdf.inputs

    def setv(key, val):
        if key in ins:
            ins[key].default_value = val

    setv("Base Color", base)
    setv("Metallic", metallic)
    setv("Roughness", rough)
    setv("IOR", ior)
    setv("Alpha", alpha)
    setv("Coat Weight", coat)
    setv("Transmission Weight", transmission)
    if emission is not None:
        setv("Emission Color", emission)
        setv("Emission Strength", emit_strength)
    return m


def flame_material(name, color=(0.45, 0.78, 1.0, 1), strength=9.0):
    """Emissive cone that fades out along its length (alpha gradient)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    ins = bsdf.inputs
    ins["Base Color"].default_value = (0.01, 0.02, 0.05, 1)
    ins["Roughness"].default_value = 0.35
    ins["Emission Color"].default_value = color
    ins["Emission Strength"].default_value = strength
    tex = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(tex.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (1, 1, 1, 1)
    ramp.color_ramp.elements[1].position = 0.32
    ramp.color_ramp.elements[1].color = (0, 0, 0, 1)
    nt.links.new(ramp.outputs["Color"], ins["Alpha"])
    return m


def new_obj(name, mesh, coll=None):
    obj = bpy.data.objects.new(name, mesh)
    (coll or bpy.context.scene.collection).objects.link(obj)
    return obj


def loft_mesh(name, stations, profile):
    """stations: list of (x, halfwidth, halfheight, zc). profile: list (u,v)."""
    verts, faces = [], []
    n = len(profile)
    m = len(stations)
    for (x, w, h, zc) in stations:
        for (u, v) in profile:
            verts.append((x, u * w, zc + v * h))
    for i in range(m - 1):
        for j in range(n):
            a = i * n + j
            b = i * n + (j + 1) % n
            c = (i + 1) * n + (j + 1) % n
            d = (i + 1) * n + j
            faces.append((a, b, c, d))
    faces.append(tuple(range(n - 1, -1, -1)))
    faces.append(tuple(range((m - 1) * n, m * n)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    return mesh


def clean_shade(obj, smooth=True):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth


def oval_profile(n=28, p=2.35, flat_bottom=0.72):
    pts = []
    for i in range(n):
        t = 2 * pi * i / n
        c, s = cos(t), sin(t)
        u = copysign(abs(c) ** (2.0 / p), c)
        v = copysign(abs(s) ** (2.0 / p), s)
        if v < 0:
            v *= flat_bottom
        pts.append((u, v))
    return pts


def add_cylinder(name, r, depth, loc, rot=(0, 0, 0), verts=32, material=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth,
                                        location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    if material:
        o.data.materials.append(material)
    return o


def add_cone(name, r1, r2, depth, loc, rot=(0, 0, 0), verts=24, material=None):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2,
                                    depth=depth, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    if material:
        o.data.materials.append(material)
    return o


def add_cube(name, size, loc, rot=(0, 0, 0), material=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    if material:
        o.data.materials.append(material)
    return o


# ----------------------------------------------------------------------------
# Materials
# ----------------------------------------------------------------------------
def build_materials():
    M = {}
    M["paint"] = mat("Paint_Red", base=(0.52, 0.02, 0.03, 1), metallic=0.6,
                     rough=0.14, coat=1.0)
    M["paint_dark"] = mat("Paint_Carbon", base=(0.022, 0.023, 0.028, 1),
                          metallic=0.4, rough=0.3, coat=0.7)
    M["glass"] = mat("Glass", base=(0.01, 0.015, 0.03, 1), metallic=0.2,
                     rough=0.04, transmission=0.25, ior=1.5, coat=1.0)
    M["tire"] = mat("Tire", base=(0.015, 0.015, 0.017, 1), metallic=0.0, rough=0.72)
    M["rim"] = mat("Rim", base=(0.68, 0.69, 0.72, 1), metallic=1.0, rough=0.16)
    M["disc"] = mat("Brake", base=(0.22, 0.22, 0.24, 1), metallic=0.9, rough=0.32)
    M["head"] = mat("Headlight", base=(0.85, 0.92, 1.0, 1), emission=(0.75, 0.9, 1.0, 1),
                    emit_strength=18.0, rough=0.1)
    M["tail"] = mat("Taillight", base=(0.9, 0.05, 0.05, 1), emission=(1.0, 0.06, 0.05, 1),
                    emit_strength=14.0, rough=0.2)
    M["flame"] = flame_material("ThrusterFlame", strength=0.0)
    M["ramp"] = mat("RampMetal", base=(0.255, 0.265, 0.285, 1),
                    metallic=0.45, rough=0.62)
    M["nozzle"] = mat("Nozzle", base=(0.11, 0.115, 0.13, 1), metallic=1.0, rough=0.3)
    M["concrete"] = mat("Concrete", base=(0.27, 0.27, 0.29, 1), rough=0.9)
    M["steel"] = mat("Steel", base=(0.30, 0.31, 0.34, 1), metallic=1.0, rough=0.5)
    M["steel_dark"] = mat("SteelDark", base=(0.055, 0.06, 0.07, 1), metallic=0.85, rough=0.55)
    M["dirt"] = mat("Dirt", base=(0.30, 0.255, 0.20, 1), rough=0.97)
    M["accent"] = mat("Accent", base=(0.75, 0.76, 0.8, 1), metallic=0.9, rough=0.22)
    M["lamp"] = mat("LampGlow", base=(1, 0.95, 0.85, 1),
                    emission=(1.0, 0.93, 0.78, 1), emit_strength=55.0)
    return M


# ----------------------------------------------------------------------------
# Car
# ----------------------------------------------------------------------------
def build_wheel(name, r, width, x, y, materials):
    ang = radians(90)
    tire = add_cylinder(name + "_tire", r, width, (x, y, r), rot=(ang, 0, 0),
                        verts=40, material=materials["tire"])
    rim = add_cylinder(name + "_rim", r * 0.62, width * 1.04, (x, y, r),
                       rot=(ang, 0, 0), verts=28, material=materials["rim"])
    hub = add_cylinder(name + "_hub", r * 0.20, width * 1.10, (x, y, r),
                       rot=(ang, 0, 0), verts=16, material=materials["disc"])
    for i in range(5):
        a = radians(90 + i * 72)
        spoke = add_cube(name + "_sp_%d" % i, (r * 1.05, 0.035, 0.045),
                         (x + 0, y, r), rot=(0, 0, 0),
                         material=materials["rim"])
        spoke.rotation_euler = (0, a, 0) if abs(y) > 0 else (0, 0, 0)
        spoke.location = (x, y, r)
        clean_shade(spoke, smooth=False)
        CAR_PARTS.append(spoke)
    for o in (tire, rim, hub):
        clean_shade(o)
        CAR_PARTS.append(o)


def _bool_cut(target, cutter):
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    m = target.modifiers.new("cut", "BOOLEAN")
    m.operation = "DIFFERENCE"
    m.solver = "EXACT"
    m.object = cutter
    bpy.ops.object.modifier_apply(modifier="cut")
    bpy.data.objects.remove(cutter, do_unlink=True)


def build_car(materials):
    """Local frame: +X forward, +Z up, z=0 is the ground-contact plane."""
    # ---- body: stations (x, half-width, z_bottom, z_top) ------------------
    st = [
        ( 2.36, 0.30, 0.255, 0.430),
        ( 2.24, 0.48, 0.215, 0.465),
        ( 2.03, 0.67, 0.180, 0.520),
        ( 1.80, 0.795, 0.160, 0.585),
        ( 1.58, 0.900, 0.150, 0.665),
        ( 1.42, 0.955, 0.150, 0.700),      # front fender
        ( 1.26, 0.920, 0.150, 0.640),
        ( 1.02, 0.880, 0.145, 0.605),
        ( 0.64, 0.870, 0.140, 0.610),
        ( 0.20, 0.865, 0.140, 0.625),
        (-0.28, 0.865, 0.140, 0.625),
        (-0.78, 0.875, 0.140, 0.618),
        (-1.12, 0.900, 0.145, 0.640),
        (-1.42, 0.980, 0.150, 0.730),      # rear fender
        (-1.60, 0.930, 0.150, 0.655),
        (-1.82, 0.870, 0.155, 0.610),
        (-2.12, 0.775, 0.165, 0.575),
        (-2.36, 0.630, 0.180, 0.545),
        (-2.52, 0.470, 0.205, 0.510),
    ]
    stations = [(x, w, (zt - zb) * 0.5, (zt + zb) * 0.5) for (x, w, zb, zt) in st]
    body_profile = oval_profile(32, p=2.5, flat_bottom=0.62)
    body = new_obj("Body", loft_mesh("Body", stations, body_profile))
    body.data.materials.append(materials["paint"])
    clean_shade(body)

    # real wheel arches via boolean
    for (wx, wr, cr) in ((1.42, 0.30, 0.330), (-1.42, 0.325, 0.345)):
        cut = add_cylinder("cut_%.2f" % wx, cr, 3.2, (wx, 0, wr),
                           rot=(radians(90), 0, 0), verts=40)
        _bool_cut(body, cut)
    CAR_PARTS.append(body)

    # ---- canopy ----------------------------------------------------------
    canopy_profile = oval_profile(28, p=2.2, flat_bottom=0.95)
    cst = [
        ( 0.86, 0.06, 0.560, 0.600),
        ( 0.62, 0.34, 0.520, 0.680),
        ( 0.26, 0.470, 0.490, 0.760),
        (-0.14, 0.515, 0.480, 0.795),
        (-0.54, 0.505, 0.480, 0.780),
        (-0.90, 0.435, 0.490, 0.740),
        (-1.15, 0.300, 0.500, 0.690),
        (-1.30, 0.100, 0.520, 0.640),
    ]
    cstations = [(x, w, (zt - zb) * 0.5, (zt + zb) * 0.5) for (x, w, zb, zt) in cst]
    canopy = new_obj("Canopy", loft_mesh("Canopy", cstations, canopy_profile))
    canopy.data.materials.append(materials["glass"])
    clean_shade(canopy)
    CAR_PARTS.append(canopy)

    # ---- aero ------------------------------------------------------------
    CAR_PARTS.append(add_cube("Floor", (3.0, 1.50, 0.09), (-0.1, 0, 0.115),
                              material=materials["paint_dark"]))
    CAR_PARTS.append(add_cube("Splitter", (0.24, 1.34, 0.03), (2.20, 0, 0.16),
                              material=materials["paint_dark"]))
    CAR_PARTS.append(add_cube("Diffuser", (0.42, 1.44, 0.13), (-2.36, 0, 0.185),
                              material=materials["paint_dark"]))
    for i in range(5):
        CAR_PARTS.append(add_cube("Fin_%d" % i, (0.38, 0.020, 0.13),
                                  (-2.46, (i - 2) * 0.26, 0.195),
                                  material=materials["paint_dark"]))
    for s in (-1, 1):
        CAR_PARTS.append(add_cube("Skirt_%d" % s, (2.6, 0.05, 0.07),
                                  (-0.05, s * 0.80, 0.125),
                                  material=materials["paint_dark"]))
    CAR_PARTS.append(add_cube("Wing", (0.30, 1.05, 0.028), (-2.15, 0, 0.795),
                              material=materials["paint_dark"]))
    for s in (-1, 1):
        CAR_PARTS.append(add_cube("WingSup_%d" % s, (0.04, 0.04, 0.16),
                                  (-2.13, s * 0.44, 0.720),
                                  material=materials["paint_dark"]))
        CAR_PARTS.append(add_cube("Mirror_%d" % s, (0.10, 0.12, 0.05),
                                  (0.50, s * 0.80, 0.600),
                                  material=materials["paint_dark"]))

    # ---- lights ----------------------------------------------------------
    for s in (-1, 1):
        CAR_PARTS.append(add_cube("Head_%d" % s, (0.10, 0.30, 0.028),
                                  (2.22, s * 0.28, 0.455),
                                  material=materials["head"]))
    CAR_PARTS.append(add_cube("TailLight", (0.045, 1.10, 0.045), (-2.53, 0, 0.560),
                              material=materials["tail"]))

    # ---- wheels ----------------------------------------------------------
    build_wheel("W_FL", 0.30, 0.24, 1.42, 0.86, materials)
    build_wheel("W_FR", 0.30, 0.24, 1.42, -0.86, materials)
    build_wheel("W_RL", 0.325, 0.30, -1.42, 0.88, materials)
    build_wheel("W_RR", 0.325, 0.30, -1.42, -0.88, materials)

    # ---- thrusters -------------------------------------------------------
    flames = []
    for s in (-1, 1):
        nz = add_cone("ThrustMain_%d" % s, 0.115, 0.195, 0.34,
                      (-2.64, s * 0.36, 0.485), rot=(0, radians(-90), 0),
                      material=materials["nozzle"])
        clean_shade(nz)
        CAR_PARTS.append(nz)
        fl = add_cone("FlameMain_%d" % s, 0.140, 0.0, 1.05,
                      (-3.35, s * 0.36, 0.485), rot=(0, radians(-90), 0),
                      material=materials["flame"])
        clean_shade(fl)
        flames.append(fl)
        CAR_PARTS.append(fl)

    att = [("F", 1.86, 0.0, 0.13), ("RL", -2.12, 0.60, 0.14),
           ("RR", -2.12, -0.60, 0.14), ("ML", 0.30, 0.74, 0.15),
           ("MR", 0.30, -0.74, 0.15)]
    for nm, x, y, z in att:
        nz = add_cone("AC_" + nm, 0.07, 0.10, 0.13, (x, y, z),
                      rot=(radians(180), 0, 0), material=materials["nozzle"])
        clean_shade(nz)
        CAR_PARTS.append(nz)
        fl = add_cone("ACF_" + nm, 0.060, 0.0, 0.30, (x, y, z - 0.22),
                      rot=(radians(180), 0, 0), material=materials["flame"])
        clean_shade(fl)
        flames.append(fl)
        CAR_PARTS.append(fl)

    for i, fx in enumerate((1.15, -0.35)):
        fan = add_cylinder("SuctionFan_%d" % i, 0.40, 0.08, (fx, 0, 0.075),
                           verts=32, material=materials["nozzle"])
        clean_shade(fan)
        CAR_PARTS.append(fan)
        ring = add_cylinder("SuctionRing_%d" % i, 0.50, 0.05, (fx, 0, 0.09),
                            verts=32, material=materials["paint_dark"])
        clean_shade(ring)
        CAR_PARTS.append(ring)

    root = bpy.data.objects.new("CarRoot", None)
    bpy.context.scene.collection.objects.link(root)
    root.empty_display_size = 1.5
    for o in CAR_PARTS:
        o.parent = root
    return root, flames


# ----------------------------------------------------------------------------
# Ramp
# ----------------------------------------------------------------------------
def build_ramp(materials):
    cx, cz = 0.0, RAMP_R
    W = RAMP_W * 0.5
    segs = 110
    aa = RAMP_A_END
    rows = []
    for i in range(segs + 1):
        a = aa * i / segs
        sx, sz = cx + RAMP_R * sin(a), cz - RAMP_R * cos(a)
        nx, nz = -sin(a), cos(a)
        ox, oz = sx - nx * RAMP_THK, sz - nz * RAMP_THK
        rows.append([(sx, -W, sz), (sx, W, sz), (ox, W, oz), (ox, -W, oz)])
    verts, faces = [], []
    for row in rows:
        verts.extend(row)
    for i in range(segs):
        for k in range(4):
            a0 = i * 4 + k
            b0 = i * 4 + (k + 1) % 4
            a1 = (i + 1) * 4 + k
            b1 = (i + 1) * 4 + (k + 1) % 4
            faces.append((a0, b0, b1, a1))
    for r in (rows[0], rows[-1]):
        base = verts.index(r[0])
        faces.append((base, base + 1, base + 2, base + 3))
    mesh = bpy.data.meshes.new("Ramp")
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    ramp = new_obj("Ramp", mesh)
    ramp.data.materials.append(materials["ramp"])
    clean_shade(ramp, smooth=False)

    # lead-in
    add_cube("RampLead", (4.0, RAMP_W, 0.34), (-3.7, 0, 0.17),
             material=materials["ramp"])
    # support beams
    for t in (0.16, 0.34, 0.52, 0.70, 0.86):
        a = aa * t
        sx = cx + RAMP_R * sin(a)
        sz = cz - RAMP_R * cos(a)
        hh = max(sz - 0.3, 0.4)
        add_cube("RampBeam_%.2f" % t, (0.20, 0.20, hh), (sx, 0, hh / 2),
                 material=materials["ramp"])
    return ramp


# ----------------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------------
def build_environment(materials):
    bpy.ops.mesh.primitive_plane_add(size=800, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    ground.data.materials.append(materials["dirt"])

    # Raptor-style test stand, well behind the action
    add_cube("TestStand", (10, 12, 15), (-52, -4, 7.5), material=materials["concrete"])
    for i in range(4):
        add_cube("StandLeg_%d" % i, (0.7, 0.7, 15),
                 (-52 + ((i % 2) * 8 - 4), -4 + ((i // 2) * 10 - 5), 7.5),
                 material=materials["steel"])
    add_cube("StandTower", (3, 3, 28), (-52, -4, 14), material=materials["steel_dark"])

    for i, (x, y) in enumerate([(70, 44), (92, -22), (-84, 40)]):
        add_cylinder("WTcol_%d" % i, 0.5, 13, (x, y, 6.5), verts=16,
                     material=materials["steel"])
        add_cylinder("WTtank_%d" % i, 2.3, 3.8, (x, y, 14.5), verts=24,
                     material=materials["steel_dark"])

    # floodlight towers around the site
    flood_pos = [(26, 22), (-30, 24), (24, -24), (-34, -22), (44, 6), (-46, -4)]
    for i, (x, y) in enumerate(flood_pos):
        add_cylinder("Pole_%d" % i, 0.24, 18, (x, y, 9), verts=12,
                     material=materials["steel"])
        add_cube("PoleHead_%d" % i, (2.0, 0.55, 1.0), (x, y, 18.3),
                 material=materials["steel_dark"])
        add_cube("Lamp_%d" % i, (1.8, 0.14, 0.8), (x, y, 18.3),
                 material=materials["lamp"])

    # vehicles to fly over: a line of cars + a semi, at x ~ -19
    for i, y in enumerate((-6.2, -2.1, 2.1, 6.2)):
        add_cube("ParkBody_%d" % i, (4.4, 1.9, 0.95), (-19, y, 0.68),
                 material=materials["accent"])
        add_cube("ParkCab_%d" % i, (2.4, 1.7, 0.72), (-19.3, y, 1.42),
                 material=materials["steel_dark"])
    add_cube("Semi", (11.0, 2.6, 3.6), (-19, 0, 2.0), material=materials["accent"])
    add_cube("SemiCab", (2.6, 2.5, 2.7), (-13.5, 0, 1.55), material=materials["steel_dark"])


# ----------------------------------------------------------------------------
# World + lights
# ----------------------------------------------------------------------------
def build_world_and_lights(dusk=True):
    scene = bpy.context.scene
    world = bpy.data.worlds.new("Sky")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = radians(-1.5 if dusk else 12.0)
    sky.sun_rotation = radians(120.0)
    sky.altitude = 240.0
    for k, v in (("sun_intensity", 0.22 if dusk else 0.3), ("sun_disc", True)):
        if hasattr(sky, k):
            setattr(sky, k, v)
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.55 if dusk else 0.8
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # warm low sun
    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 1.7 if dusk else 3.0
    sun_data.angle = radians(2.0)
    sun_data.color = (1.0, 0.62, 0.38)
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (radians(84), 0, radians(-118))

    # cool sky fill
    fill_data = bpy.data.lights.new("Fill", type="AREA")
    fill_data.energy = 950.0
    fill_data.size = 30.0
    fill_data.color = (0.45, 0.62, 1.0)
    fill = bpy.data.objects.new("Fill", fill_data)
    scene.collection.objects.link(fill)
    fill.location = (26, -26, 14)
    fill.rotation_euler = (radians(58), 0, radians(50))

    # warm rim from behind
    rim_data = bpy.data.lights.new("Rim", type="AREA")
    rim_data.energy = 1300.0
    rim_data.size = 12.0
    rim_data.color = (1.0, 0.72, 0.5)
    riml = bpy.data.objects.new("Rim", rim_data)
    scene.collection.objects.link(riml)
    riml.location = (-34, 6, 14)
    riml.rotation_euler = (radians(62), 0, radians(-128))


# ----------------------------------------------------------------------------
# Trajectory
# ----------------------------------------------------------------------------
def rpos(a):
    return Vector((RAMP_R * sin(a), 0.0, RAMP_R - RAMP_R * cos(a)))


def launch_ballistics():
    """Return (p0, v0, t_ground) for the flight phase."""
    a = RAMP_A_END
    p0 = rpos(a)
    v0 = Vector((cos(a), 0.0, sin(a))) * V_LAUNCH
    a = 0.5 * (-9.81)
    b, c = v0.z, p0.z
    disc = b * b - 4 * a * c
    t_ground = (-b - sqrt(disc)) / (2 * a)     # positive root
    return p0, v0, t_ground


def make_trajectory():
    keys = []
    for f in range(0, F_DRIVE_END + 1):
        t = f / F_DRIVE_END
        a = RAMP_A_END * (t ** 1.18)
        keys.append((f, rpos(a), -a))

    p0, v0, t_ground = launch_ballistics()
    g = -9.81
    n_fly = F_LAND - F_DRIVE_END
    rot_start = -RAMP_A_END
    rot_end = -4 * pi            # ends upright after the kick-flip
    for i in range(1, n_fly + 1):
        f = F_DRIVE_END + i
        s = i / n_fly
        t = t_ground * s
        loc = Vector((p0.x + v0.x * t, 0.0, max(p0.z + v0.z * t + 0.5 * g * t * t, 0.0)))
        roty = rot_start + (rot_end - rot_start) * s
        keys.append((f, loc, roty))

    land_x = keys[-1][1].x
    for i in range(1, F_TOTAL - F_LAND + 1):
        f = F_LAND + i
        keys.append((f, Vector((land_x - 0.42 * i, 0.0, 0.0)), rot_end))
    return keys, F_DRIVE_END


# ----------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------
def build_all():
    clear_scene()
    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.frame_start = 0
    scene.frame_end = F_TOTAL
    materials = build_materials()
    root, flames = build_car(materials)
    build_ramp(materials)
    build_environment(materials)
    build_world_and_lights(dusk=True)

    keys, f_launch = make_trajectory()

    root.animation_data_create()
    for (f, loc, roty) in keys:
        root.location = loc
        root.rotation_euler = (0.0, roty, 0.0)
        root.keyframe_insert("location", frame=f)
        root.keyframe_insert("rotation_euler", frame=f)
    for fc in all_fcurves(root.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"

    # thruster VFX: flames hidden until launch, fade out after landing
    flame_mat = materials["flame"]
    emis_input = flame_mat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"]

    def strength(f):
        if f < f_launch:
            return 0.0
        if f <= F_LAND:
            return 9.0 + 2.0 * sin(f * 1.9)
        if f <= F_LAND + 22:
            return max(0.0, 9.0 * (1 - (f - F_LAND) / 22.0))
        return 0.0

    for f in range(0, F_TOTAL + 1, 2):
        emis_input.default_value = strength(f)
        emis_input.keyframe_insert("default_value", frame=f)
    for fc in all_fcurves(flame_mat.node_tree.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"

    scale_keys = [(0, 0.001), (f_launch - 14, 0.001), (f_launch - 6, 1.0),
                  (F_LAND + 12, 1.0), (F_LAND + 22, 0.001), (F_TOTAL, 0.001)]
    for fl in flames:
        for (f, sc) in scale_keys:
            fl.scale = (sc, sc, sc)
            fl.keyframe_insert("scale", frame=f)
        for fc in all_fcurves(fl.animation_data.action):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"

    hero_cam = build_hero_camera(keys)
    anim_cam = build_anim_camera(root)
    scene.camera = hero_cam
    return root, hero_cam, anim_cam, keys, f_launch


def all_fcurves(action):
    if action is None:
        return []
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    fcs = []
    for layer in action.layers:
        for strip in layer.strips:
            for cb in getattr(strip, "channelbags", []):
                fcs.extend(cb.fcurves)
    return fcs


def hero_frame():
    return int(F_DRIVE_END + (F_LAND - F_DRIVE_END) * 0.33)


def build_hero_camera(keys):
    cam_data = bpy.data.cameras.new("HeroCam")
    cam_data.lens = 40
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = 9.0
    cam = bpy.data.objects.new("HeroCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)

    hf = hero_frame()
    target = next((loc for (f, loc, r) in keys if f == hf), Vector((0, 0, 12)))
    focus = bpy.data.objects.new("HeroFocus", None)
    bpy.context.scene.collection.objects.link(focus)
    # wide side-on shot: loop ramp on the right, airborne car on the left
    focus.location = Vector((target.x + 6.0, 0.0, 12.0))
    cam.location = (-19.0, -25.0, 15.0)
    con = cam.constraints.new("TRACK_TO")
    con.target = focus
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    return cam


def build_anim_camera(target):
    cam_data = bpy.data.cameras.new("AnimCam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("AnimCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = (-6.0, -44.0, 15.0)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    return cam


# ----------------------------------------------------------------------------
# Render
# ----------------------------------------------------------------------------
def setup_render(res=(1280, 720), samples=48, exposure=0.0):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    scene.view_settings.exposure = exposure
    scene.render.image_settings.file_format = "PNG"


def studio_car():
    """Hide the world, isolate the car, studio lighting."""
    scene = bpy.context.scene
    keep = set(CAR_PARTS) | {bpy.data.objects["CarRoot"]}
    for o in list(scene.collection.all_objects):
        if o not in keep:
            o.hide_render = True
            o.hide_viewport = True
    # backdrop
    bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "StudioFloor"
    floor.data.materials.append(mat("StudioFloorM", base=(0.05, 0.05, 0.06, 1), rough=0.4))
    bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 14, 12),
                                     rotation=(radians(90), 0, 0))
    back = bpy.context.active_object
    back.name = "StudioBack"
    back.data.materials.append(mat("StudioBackM", base=(0.03, 0.035, 0.05, 1), rough=0.6))

    # lights
    def area(name, energy, size, loc, rot, color):
        d = bpy.data.lights.new(name, type="AREA")
        d.energy, d.size, d.color = energy, size, color
        o = bpy.data.objects.new(name, d)
        scene.collection.objects.link(o)
        o.location, o.rotation_euler = loc, rot
        return o
    area("Key", 3000, 10, (6, -8, 8), (radians(52), 0, radians(38)), (1.0, 0.96, 0.9))
    area("RimL", 2200, 7, (-9, 3, 6), (radians(70), 0, radians(-118)), (0.4, 0.6, 1.0))
    area("RimR", 1800, 7, (2, 10, 6), (radians(70), 0, radians(160)), (1.0, 0.4, 0.35))
    area("Top", 1600, 12, (0, -1, 14), (0, 0, 0), (0.85, 0.9, 1.0))

    cam_data = bpy.data.cameras.new("StudioCam")
    cam_data.lens = 58
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = 8.0
    cam = bpy.data.objects.new("StudioCam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (7.2, -6.6, 2.4)
    focus = bpy.data.objects.new("StudioFocus", None)
    scene.collection.objects.link(focus)
    focus.location = (0, 0, 0.6)
    con = cam.constraints.new("TRACK_TO")
    con.target = focus
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    scene.camera = cam

    # world: neutral grey
    w = bpy.data.worlds.new("StudioW")
    scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.03, 0.035, 0.05, 1)
    bg.inputs["Strength"].default_value = 1.0


def do_car():
    setup_render(res=(1400, 880), samples=96, exposure=0.0)
    studio_car()
    bpy.context.scene.frame_set(0)
    bpy.context.scene.render.filepath = os.path.join(RENDERS, "car_studio.png")
    bpy.ops.render.render(write_still=True)


def do_preview():
    setup_render(res=(1100, 620), samples=28, exposure=0.0)
    bpy.context.scene.camera = bpy.data.objects.get("HeroCam")
    bpy.context.scene.frame_set(hero_frame())
    bpy.context.scene.render.filepath = os.path.join(RENDERS, "preview.png")
    bpy.ops.render.render(write_still=True)


def do_hero():
    setup_render(res=(2560, 1440), samples=220, exposure=0.0)
    scene = bpy.context.scene
    hc = bpy.data.objects.get("HeroCam")
    scene.camera = hc
    hc.data.dof.focus_distance = (Vector(hc.location) - hc.constraints[0].target.location).length
    scene.frame_set(hero_frame())
    scene.render.filepath = os.path.join(RENDERS, "roadster_hero.png")
    bpy.ops.render.render(write_still=True)
    save_blend()


def do_anim():
    scene = bpy.context.scene
    cyc = scene.cycles
    setup_render(res=(960, 540), samples=18, exposure=0.05)
    # animation is long: trim the ray budget hard, use adaptive sampling
    cyc.use_adaptive_sampling = True
    cyc.adaptive_threshold = 0.05
    cyc.adaptive_min_samples = 6
    cyc.max_bounces = 3
    cyc.diffuse_bounces = 2
    cyc.glossy_bounces = 2
    cyc.transmission_bounces = 2
    cyc.transparent_max_bounces = 6
    scene.camera = bpy.data.objects.get("AnimCam")
    img = scene.render.image_settings
    if hasattr(img, "media_type"):
        try:
            img.media_type = "VIDEO"
        except Exception:
            pass
    img.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.frame_start = 0
    scene.frame_end = F_TOTAL
    scene.render.filepath = os.path.join(RENDERS, "roadster_anim.mp4")
    bpy.ops.render.render(animation=True)


def save_blend():
    scene = bpy.context.scene
    if "HeroCam" in bpy.data.objects:
        scene.camera = bpy.data.objects["HeroCam"]
    path = os.path.join(ROOT, "roadster_flying_car.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    print("saved", path)


# ----------------------------------------------------------------------------
def main():
    build_all()
    if STAGE == "car":
        do_car()
    elif STAGE == "preview":
        do_preview()
    elif STAGE == "hero":
        do_hero()
    elif STAGE == "anim":
        do_anim()
    elif STAGE == "save":
        save_blend()
    else:
        do_preview()
        save_blend()
    print("DONE stage=", STAGE)


if __name__ == "__main__":
    main()