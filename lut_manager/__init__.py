"""LUT Manager: browse .cube LUTs and preview them live in the 3D viewport.

The LUT is applied through a compositor node group, so it shows in the viewport when
the viewport compositor is on (Viewport Shading > Compositor) and in final renders.
Works with any render engine whose viewport output Blender can composite
(EEVEE, Cycles, Workbench, Octane...). Supports Blender 4.5 (incl. OctaneBlender) and 5.x.

Blender has no 3D-LUT compositor node, so each .cube table is stored as a 2D strip
image (N*N x N, one N x N slice per blue level). Every pixel samples the 8 surrounding
LUT entries with nearest-pixel Map UV lookups and blends them by the fractional parts:
an exact trilinear lookup that runs on the GPU viewport compositor.
"""

bl_info = {
    "name": "LUT Manager",
    "author": "Matteo Fontana",
    "version": (1, 1, 0),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Sidebar > LUT",
    "description": "Browse and preview .cube LUTs live in the 3D viewport",
    "category": "Compositing",
}

import colorsys
import json
import os

import bpy
import bpy.utils.previews
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

# Blender 5.0 replaced scene.node_tree/Composite node with a compositing node group.
IS_BLENDER_5 = "compositing_node_group" in bpy.types.Scene.bl_rna.properties

NODE_NAME = "LUT Manager"
IN_SPACE_NODE = "LUTM Input Space"
OUT_SPACE_NODE = "LUTM Output Space"
EXTENSIONS = {".cube"}
UNCATEGORIZED = "Uncategorized"
THUMB_SIZE = 128


# ===========================================================================
# .cube files


class CubeLUT:
    """A 3D LUT. `table` has shape (N, N, N, 3) indexed [b, g, r], as stored in .cube files."""

    def __init__(self, table, domain_min, domain_max, title=""):
        self.table = table
        self.size = table.shape[0]
        self.domain_min = np.asarray(domain_min, dtype=np.float32)
        self.domain_max = np.asarray(domain_max, dtype=np.float32)
        self.title = title

    def strip_pixels(self):
        """RGBA float pixels of a (N*N) x N strip image: column = b*N + r, row = g (bottom-up)."""
        n = self.size
        rgb = self.table.transpose(1, 0, 2, 3).reshape(n, n * n, 3)
        rgba = np.ones((n, n * n, 4), dtype=np.float32)
        rgba[..., :3] = rgb
        return rgba.ravel()

    def apply(self, rgb):
        """Apply the LUT to an (..., 3) array with trilinear interpolation."""
        n = self.size
        span = np.where(self.domain_max > self.domain_min, self.domain_max - self.domain_min, 1.0)
        x = np.clip((rgb - self.domain_min) / span, 0.0, 1.0) * (n - 1)
        i0 = np.minimum(np.floor(x).astype(np.int32), n - 2)
        f = x - i0
        i1 = i0 + 1

        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        t = self.table

        c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r1] * fr
        c01 = t[b0, g1, r0] * (1 - fr) + t[b0, g1, r1] * fr
        c10 = t[b1, g0, r0] * (1 - fr) + t[b1, g0, r1] * fr
        c11 = t[b1, g1, r0] * (1 - fr) + t[b1, g1, r1] * fr
        c0 = c00 * (1 - fg) + c01 * fg
        c1 = c10 * (1 - fg) + c11 * fg
        return c0 * (1 - fb) + c1 * fb


def read_cube(path):
    size_3d = size_1d = None
    domain_min, domain_max = [0.0] * 3, [1.0] * 3
    title = ""
    values = []

    # utf-8-sig strips the byte-order mark some tools (Resolve, Photoshop) write.
    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            s = line.split("#", 1)[0].strip() if not line.lstrip().startswith("TITLE") else line.strip()
            if not s:
                continue
            if s[0].isdigit() or s[0] in "-+.":
                values.append(s)
                continue
            parts = s.split()
            key = parts[0].upper()
            if key == "TITLE":
                title = s[5:].strip().strip('"')
            elif key == "LUT_3D_SIZE":
                size_3d = int(parts[1])
            elif key == "LUT_1D_SIZE":
                size_1d = int(parts[1])
            elif key == "DOMAIN_MIN":
                domain_min = [float(v) for v in parts[1:4]]
            elif key == "DOMAIN_MAX":
                domain_max = [float(v) for v in parts[1:4]]
            elif key in {"LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"}:
                domain_min = [float(parts[1])] * 3
                domain_max = [float(parts[2])] * 3

    data = np.array(" ".join(values).split(), dtype=np.float32)
    data = data[: len(data) - len(data) % 3].reshape(-1, 3)

    if size_3d:
        if size_3d < 2 or len(data) < size_3d ** 3:
            raise ValueError(f"expected {size_3d ** 3} entries, found {len(data)}")
        table = data[: size_3d ** 3].reshape(size_3d, size_3d, size_3d, 3)
    elif size_1d:
        if size_1d < 2 or len(data) < size_1d:
            raise ValueError(f"expected {size_1d} entries, found {len(data)}")
        table = _expand_1d(data[:size_1d])
    else:
        raise ValueError("missing LUT_3D_SIZE / LUT_1D_SIZE")

    return CubeLUT(np.ascontiguousarray(table), domain_min, domain_max, title)


def _expand_1d(curve, n=33):
    """Turn a 1D LUT into an equivalent 3D table so the same pipeline handles both."""
    src = np.linspace(0.0, 1.0, len(curve))
    grid = np.linspace(0.0, 1.0, n)
    table = np.empty((n, n, n, 3), dtype=np.float32)
    table[..., 0] = np.interp(grid, src, curve[:, 0])[None, None, :]
    table[..., 1] = np.interp(grid, src, curve[:, 1])[None, :, None]
    table[..., 2] = np.interp(grid, src, curve[:, 2])[:, None, None]
    return table


# ===========================================================================
# Compositor


def color_spaces():
    prop = bpy.types.CompositorNodeConvertColorSpace.bl_rna.properties["from_color_space"]
    return [item.identifier for item in prop.enum_items]


def scene_linear_space():
    spaces = color_spaces()
    for name in ("scene_linear", "Linear Rec.709"):
        if name in spaces:
            return name
    return spaces[0]


def update_lut_image(scene, lut):
    name = f".LUTM {scene.name}"
    width, height = lut.size * lut.size, lut.size
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) != (width, height):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name, width, height, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    img.pixels.foreach_set(lut.strip_pixels())
    img.update()
    return img


def _socket(sockets, identifier):
    return next(s for s in sockets if s.identifier == identifier)


def _math(nodes, op, a, b=None, c=None, clamp=False, x=0, y=0):
    node = nodes.new("ShaderNodeMath")
    node.operation = op
    node.use_clamp = clamp
    node.location = (x, y)
    for i, value in enumerate((a, b, c)):
        if value is None:
            continue
        if isinstance(value, bpy.types.NodeSocket):
            node.id_data.links.new(value, node.inputs[i])
        else:
            node.inputs[i].default_value = value
    return node.outputs[0]


def _mix(nodes, links, factor, a, b, x=0, y=0):
    """RGBA Mix node; returns its colour output."""
    node = nodes.new("ShaderNodeMix")
    node.data_type = "RGBA"
    node.location = (x, y)
    links.new(factor, _socket(node.inputs, "Factor_Float"))
    links.new(a, _socket(node.inputs, "A_Color"))
    links.new(b, _socket(node.inputs, "B_Color"))
    return _socket(node.outputs, "Result_Color")


def _map_uv_nearest(node):
    if "Interpolation" in node.inputs:  # Blender 5.x
        node.inputs["Interpolation"].default_value = "Nearest"
    else:
        node.filter_type = "NEAREST"


def _ensure_interface(group):
    if group.interface.items_tree:
        return
    iface = group.interface
    iface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    factor = iface.new_socket("Factor", in_out="INPUT", socket_type="NodeSocketFloat")
    factor.default_value, factor.min_value, factor.max_value = 1.0, 0.0, 1.0
    iface.new_socket("Exposure", in_out="INPUT", socket_type="NodeSocketFloat")
    iface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")


def build_group(group, lut, image):
    """(Re)build the node group for a LUT. Colour spaces are set afterwards by sync_values."""
    _ensure_interface(group)
    nodes, links = group.nodes, group.links
    nodes.clear()

    n = lut.size
    s = n - 1
    nn = n * n

    g_in = nodes.new("NodeGroupInput")
    g_in.location = (-1600, 0)
    g_out = nodes.new("NodeGroupOutput")
    g_out.location = (2000, 0)

    exposure = nodes.new("CompositorNodeExposure")
    exposure.location = (-1400, 0)
    links.new(g_in.outputs["Image"], exposure.inputs["Image"])
    links.new(g_in.outputs["Exposure"], exposure.inputs["Exposure"])

    space_in = nodes.new("CompositorNodeConvertColorSpace")
    space_in.name = IN_SPACE_NODE
    space_in.location = (-1200, 0)
    links.new(exposure.outputs["Image"], space_in.inputs["Image"])

    sep = nodes.new("CompositorNodeSeparateColor")
    sep.location = (-1000, 0)
    links.new(space_in.outputs["Image"], sep.inputs["Image"])

    # Per channel: normalise the domain to 0..1, then find the two neighbouring
    # LUT indices (i0, i0 + 1) and the blend fraction between them.
    idx = []
    for i, name in enumerate(("Red", "Green", "Blue")):
        y = 600 - i * 400
        lo, hi = float(lut.domain_min[i]), float(lut.domain_max[i])
        scale = 1.0 / (hi - lo) if hi > lo else 1.0
        c = _math(nodes, "MULTIPLY_ADD", sep.outputs[name], scale, -lo * scale, clamp=True, x=-800, y=y)
        scaled = _math(nodes, "MULTIPLY", c, float(s), x=-600, y=y)
        i0 = _math(nodes, "MINIMUM", _math(nodes, "FLOOR", scaled, x=-400, y=y), float(s - 1), x=-200, y=y)
        frac = _math(nodes, "SUBTRACT", scaled, i0, x=0, y=y - 150)
        i1 = _math(nodes, "ADD", i0, 1.0, x=0, y=y)
        idx.append(((i0, i1), frac))
    (r_i, fr), (g_i, fg), (b_i, fb) = idx

    # Pixel-centre UVs inside the strip image.
    u_base = [_math(nodes, "MULTIPLY_ADD", b, 1.0 / n, 0.5 / nn, x=200, y=-400 - k * 150)
              for k, b in enumerate(b_i)]
    u = [[_math(nodes, "MULTIPLY_ADD", r, 1.0 / nn, ub, x=400, y=-400 - (2 * kb + kr) * 150)
          for kr, r in enumerate(r_i)] for kb, ub in enumerate(u_base)]
    v = [_math(nodes, "MULTIPLY_ADD", g, 1.0 / n, 0.5 / n, x=400, y=200 - k * 150)
         for k, g in enumerate(g_i)]

    lut_img = nodes.new("CompositorNodeImage")
    lut_img.image = image
    lut_img.location = (400, 600)

    def sample(kb, kg, kr, row):
        uv = nodes.new("ShaderNodeCombineXYZ")
        uv.location = (600, 600 - row * 160)
        links.new(u[kb][kr], uv.inputs["X"])
        links.new(v[kg], uv.inputs["Y"])
        uv.inputs["Z"].default_value = 1.0
        map_uv = nodes.new("CompositorNodeMapUV")
        map_uv.location = (800, 600 - row * 160)
        _map_uv_nearest(map_uv)
        links.new(lut_img.outputs["Image"], map_uv.inputs["Image"])
        links.new(uv.outputs["Vector"], map_uv.inputs["UV"])
        return map_uv.outputs["Image"]

    slices = []
    for kb in range(2):
        rows = []
        for kg in range(2):
            row = kb * 4 + kg * 2
            rows.append(_mix(nodes, links, fr, sample(kb, kg, 0, row), sample(kb, kg, 1, row + 1),
                             x=1000, y=600 - row * 160))
        slices.append(_mix(nodes, links, fg, rows[0], rows[1], x=1150, y=600 - kb * 640))
    graded = _mix(nodes, links, fb, slices[0], slices[1], x=1300, y=200)

    space_out = nodes.new("CompositorNodeConvertColorSpace")
    space_out.name = OUT_SPACE_NODE
    space_out.location = (1450, 200)
    links.new(graded, space_out.inputs["Image"])

    blend = _mix(nodes, links, g_in.outputs["Factor"], exposure.outputs["Image"],
                 space_out.outputs["Image"], x=1600, y=0)

    # Keep the original alpha.
    sep_out = nodes.new("CompositorNodeSeparateColor")
    sep_out.location = (1750, 100)
    links.new(blend, sep_out.inputs["Image"])
    comb = nodes.new("CompositorNodeCombineColor")
    comb.location = (1850, 0)
    for name in ("Red", "Green", "Blue"):
        links.new(sep_out.outputs[name], comb.inputs[name])
    links.new(sep.outputs["Alpha"], comb.inputs["Alpha"])
    links.new(comb.outputs["Image"], g_out.inputs["Image"])


def ensure_group(settings):
    group = settings.node_group
    if group is None:
        group = bpy.data.node_groups.new(NODE_NAME, "CompositorNodeTree")
        settings.node_group = group
    return group


def _scene_tree(scene, create=True):
    if not IS_BLENDER_5:
        if create and not scene.use_nodes:
            scene.use_nodes = True
        return scene.node_tree
    tree = scene.compositing_node_group
    if tree is None and create:
        tree = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = tree.nodes.new("CompositorNodeRLayers")
        rl.location = (-300, 0)
        out = tree.nodes.new("NodeGroupOutput")
        out.location = (300, 0)
        tree.links.new(rl.outputs["Image"], out.inputs["Image"])
        scene.compositing_node_group = tree
    return tree


def _output_socket(tree):
    """The socket that receives the final image: Composite node (4.x) or group output (5.x)."""
    if IS_BLENDER_5:
        if not any(i.in_out == "OUTPUT" for i in tree.interface.items_tree if i.item_type == "SOCKET"):
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        idname = "NodeGroupOutput"
    else:
        idname = "CompositorNodeComposite"
    outputs = [n for n in tree.nodes if n.bl_idname == idname]
    node = next((n for n in outputs if getattr(n, "is_active_output", False)), None)
    if node is None:
        node = outputs[0] if outputs else tree.nodes.new(idname)
    return node, node.inputs[0]


def _image_source(tree):
    rl = next((n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"), None)
    if rl is None:
        rl = tree.nodes.new("CompositorNodeRLayers")
        rl.location = (-400, 0)
    return rl.outputs["Image"]


def find_node(scene):
    tree = _scene_tree(scene, create=False)
    if tree is None:
        return None
    node = tree.nodes.get(NODE_NAME)
    return node if node and node.bl_idname == "CompositorNodeGroup" else None


def insert_node(scene, group):
    """Place the LUT group right before the compositor's output, keeping the user's nodes."""
    node = find_node(scene)
    if node is not None:
        node.node_tree = group
        return node

    tree = _scene_tree(scene)
    out, target = _output_socket(tree)
    source = target.links[0].from_socket if target.is_linked else _image_source(tree)

    node = tree.nodes.new("CompositorNodeGroup")
    node.name = node.label = NODE_NAME
    node.node_tree = group
    node.location = (out.location.x, out.location.y)
    out.location.x += 250

    tree.links.new(source, node.inputs["Image"])
    tree.links.new(node.outputs["Image"], target)
    return node


def remove_node(scene):
    node = find_node(scene)
    if node is None:
        return
    tree = node.id_data
    inp = node.inputs["Image"]
    source = inp.links[0].from_socket if inp.is_linked else None
    targets = [link.to_socket for link in node.outputs["Image"].links]
    tree.nodes.remove(node)
    if source is not None:
        for t in targets:
            tree.links.new(source, t)


def sync_values(scene, settings):
    sync_octane(scene, settings)
    node = find_node(scene)
    if node is not None:
        # Octane applies the LUT in its own imager; muting avoids grading renders twice.
        node.mute = not settings.enabled or is_octane(scene)
        node.inputs["Factor"].default_value = settings.factor
        node.inputs["Exposure"].default_value = settings.exposure
    group = settings.node_group
    if group is None:
        return
    linear = scene_linear_space()
    space_in = group.nodes.get(IN_SPACE_NODE)
    space_out = group.nodes.get(OUT_SPACE_NODE)
    if space_in:
        space_in.from_color_space = linear
        space_in.to_color_space = settings.input_space
    if space_out:
        space_out.from_color_space = settings.output_space
        space_out.to_color_space = linear


# ===========================================================================
# Octane
#
# Octane draws its viewport itself, bypassing Blender's compositor, so with Octane
# the LUT goes into the Custom LUT slot of Octane's camera imager instead.


def is_octane(scene):
    return scene.render.engine == "octane" and hasattr(scene, "oct_view_cam")


def _octane_imagers(scene):
    """Viewport imager (free view) and the active camera's imager (camera view, renders)."""
    imagers = [scene.oct_view_cam.imager]
    cam = scene.camera
    if cam is not None and cam.type == "CAMERA" and hasattr(cam.data, "octane"):
        imagers.append(cam.data.octane.imager)
    return imagers


def sync_octane(scene, settings):
    if not is_octane(scene):
        return
    active = settings.enabled and bool(settings.lut_path)
    for imager in _octane_imagers(scene):
        if active:
            if imager.custom_lut != settings.lut_path:
                imager.custom_lut = settings.lut_path
            if abs(imager.lut_strength - settings.factor) > 1e-6:
                imager.lut_strength = settings.factor
        elif imager.custom_lut and entry_for(imager.custom_lut) is not None:
            # Only clear LUTs that came from this add-on's folder.
            imager.custom_lut = ""
    if active:
        scene.octane.use_preview_camera_imager = True
        scene.octane.use_render_camera_imager = True


_msgbus_owner = object()


def _on_engine_or_camera_change():
    for scene in bpy.data.scenes:
        if scene.lut_manager.lut_path:
            sync_values(scene, scene.lut_manager)


def _subscribe_msgbus():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    for key in ((bpy.types.RenderSettings, "engine"), (bpy.types.Scene, "camera")):
        bpy.msgbus.subscribe_rna(key=key, owner=_msgbus_owner, args=(), notify=_on_engine_or_camera_change)


# ===========================================================================
# Library: folder scanning, thumbnails, favorites


class Entry:
    __slots__ = ("index", "path", "name", "category", "mtime")

    def __init__(self, index, path, name, category, mtime):
        self.index = index
        self.path = path
        self.name = name
        self.category = category
        self.mtime = mtime


_entries = []
_by_path = {}
_scanned_dir = None
_previews = None
_pending = []
_sample = None


def prefs():
    return bpy.context.preferences.addons[__name__].preferences


def norm(path):
    # normcase lower-cases on Windows only, so paths compare correctly on both platforms.
    return os.path.normcase(os.path.abspath(bpy.path.abspath(path))) if path else ""


def entries():
    folder = prefs().lut_dir
    if norm(folder) != _scanned_dir:
        scan(folder)
    return _entries


def scan(folder):
    global _scanned_dir
    _entries.clear()
    _by_path.clear()
    clear_previews()
    _scanned_dir = norm(folder)
    if not _scanned_dir or not os.path.isdir(_scanned_dir):
        return

    found = []
    for root, _dirs, files in os.walk(_scanned_dir):
        for file in files:
            if file.startswith(".") or os.path.splitext(file)[1].lower() not in EXTENSIONS:
                continue  # also skips macOS "._" resource-fork files
            path = norm(os.path.join(root, file))
            rel = os.path.relpath(root, _scanned_dir)
            category = UNCATEGORIZED if rel == "." else rel.replace("\\", "/").split("/")[0]
            name = os.path.splitext(file)[0].replace("_", " ")
            found.append((path, name, category, os.path.getmtime(path)))

    for i, (path, name, category, mtime) in enumerate(sorted(found, key=lambda e: e[1].lower())):
        entry = Entry(i, path, name, category, mtime)
        _entries.append(entry)
        _by_path[path] = entry
    _pending.extend(_entries)
    if not bpy.app.timers.is_registered(_thumbnail_tick):
        bpy.app.timers.register(_thumbnail_tick, first_interval=0.05)


def entry_for(path):
    entries()
    return _by_path.get(norm(path))


def categories():
    return sorted({e.category for e in entries()}, key=str.lower)


def filtered(settings):
    favs = favorites()
    search = settings.search.strip().lower()
    result = []
    for e in entries():
        if settings.favorites_only and e.path not in favs:
            continue
        if settings.category != "ALL" and e.category != settings.category:
            continue
        if search and search not in e.name.lower() and search not in e.category.lower():
            continue
        result.append(e)

    if settings.sort == "CATEGORY":
        result.sort(key=lambda e: (e.category.lower(), e.name.lower()))
    elif settings.sort == "DATE":
        result.sort(key=lambda e: -e.mtime)
    return result


def favorites():
    try:
        return set(json.loads(prefs().favorites or "[]"))
    except ValueError:
        return set()


def toggle_favorite(path):
    favs = favorites()
    favs.symmetric_difference_update({norm(path)})
    prefs().favorites = json.dumps(sorted(favs))
    if bpy.context.preferences.use_preferences_save:
        bpy.ops.wm.save_userpref()


def _sample_image():
    """Procedural test chart: hue sweep with brightness ramp, plus a grey ramp at the bottom."""
    global _sample
    if _sample is not None:
        return _sample
    n = THUMB_SIZE
    img = np.zeros((n, n, 3), dtype=np.float32)
    grey_rows = n // 5
    for y in range(n):
        for x in range(n):
            if y < grey_rows:
                img[y, x] = x / (n - 1)
            else:
                t = (y - grey_rows) / (n - grey_rows - 1)
                img[y, x] = colorsys.hsv_to_rgb(x / n, 0.25 + 0.6 * (1 - t), 0.15 + 0.85 * t)
    _sample = img
    return img


def icon_id(entry):
    if _previews is not None and entry.path in _previews:
        return _previews[entry.path].icon_id
    return 0


def _make_thumbnail(entry):
    if _previews is None or entry.path in _previews:
        return
    preview = _previews.new(entry.path)
    try:
        rgb = np.clip(read_cube(entry.path).apply(_sample_image()), 0.0, 1.0)
    except Exception as exc:  # noqa: BLE001 - a broken file should just get a red thumbnail
        print(f"LUT Manager: could not read {entry.path}: {exc}")
        rgb = np.zeros((THUMB_SIZE, THUMB_SIZE, 3), dtype=np.float32)
        rgb[..., 0] = 0.6
    rgba = np.ones((THUMB_SIZE, THUMB_SIZE, 4), dtype=np.float32)
    rgba[..., :3] = rgb
    preview.image_size = (THUMB_SIZE, THUMB_SIZE)
    preview.image_pixels_float.foreach_set(rgba.ravel())


def _thumbnail_tick():
    for _ in range(4):
        if not _pending:
            break
        _make_thumbnail(_pending.pop(0))
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return 0.01 if _pending else None


def clear_previews():
    _pending.clear()
    if _previews is not None:
        _previews.clear()


# ===========================================================================
# Properties


def apply_lut(scene):
    settings = scene.lut_manager
    settings.error = ""
    if not settings.lut_path:
        sync_values(scene, settings)
        return
    try:
        lut = read_cube(settings.lut_path)
    except Exception as exc:  # noqa: BLE001 - shown in the panel
        settings.error = f"Could not read LUT: {exc}"
        return
    image = update_lut_image(scene, lut)
    group = ensure_group(settings)
    build_group(group, lut, image)
    insert_node(scene, group)
    sync_values(scene, settings)


def _on_values(self, context):
    sync_values(self.id_data, self)


def _on_enabled(self, context):
    if self.enabled and self.lut_path and find_node(self.id_data) is None:
        apply_lut(self.id_data)
    else:
        sync_values(self.id_data, self)


# Enum item strings must stay referenced while Blender uses them.
_lut_items = []
_category_items = []
_space_items = []


def _lut_enum_items(self, context):
    items = filtered(self)
    current = entry_for(self.lut_path)
    if current is not None and current not in items:
        items.insert(0, current)
    _lut_items.clear()
    for e in items:
        _lut_items.append((e.path, e.name, f"{e.category}\n{e.path}", icon_id(e) or "IMAGE_DATA", e.index))
    if not _lut_items:
        _lut_items.append(("NONE", "No LUTs", "Set a LUT folder in the panel", "ERROR", -1))
    return _lut_items


def _lut_get(self):
    entry = entry_for(self.lut_path)
    return entry.index if entry else -1


def _lut_set(self, value):
    entry = next((e for e in entries() if e.index == value), None)
    if entry is None:
        return
    self.lut_path = entry.path
    apply_lut(self.id_data)


def _category_enum_items(self, context):
    _category_items.clear()
    _category_items.append(("ALL", "All Categories", ""))
    for cat in categories():
        _category_items.append((cat, cat, ""))
    return _category_items


def _space_default(*preferred):
    ids = [s[0] for s in _space_items]
    return next((name for name in preferred if name in ids), ids[0])


class LUTM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    lut_dir: StringProperty(name="LUT Folder", subtype="DIR_PATH",
                            description="Folder containing .cube files; subfolders become categories")
    favorites: StringProperty(default="[]", options={"HIDDEN"})

    def draw(self, context):
        self.layout.prop(self, "lut_dir")


def _make_settings():
    """Built at register time because the colour-space list comes from the OCIO config."""
    if not _space_items:
        _space_items.extend((s, s, "") for s in color_spaces())

    class LUTM_Settings(bpy.types.PropertyGroup):
        enabled: BoolProperty(name="Enable LUT", default=True, update=_on_enabled)
        lut_path: StringProperty(name="LUT File", subtype="FILE_PATH")
        lut: EnumProperty(name="LUT", items=_lut_enum_items, get=_lut_get, set=_lut_set)
        factor: FloatProperty(name="LUT Factor", default=1.0, min=0.0, max=1.0,
                              subtype="FACTOR", update=_on_values)
        exposure: FloatProperty(name="Exposure", default=0.0, soft_min=-5.0, soft_max=5.0,
                                step=10, update=_on_values)
        input_space: EnumProperty(
            name="Input Space", items=_space_items, update=_on_values,
            default=_space_default("AgX Base sRGB", "Filmic sRGB", "sRGB"),
            description="Colour space the LUT expects as input (the scene is converted to it first)")
        output_space: EnumProperty(
            name="Output Space", items=_space_items, update=_on_values,
            default=_space_default("sRGB"),
            description="Colour space the LUT produces")
        search: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"})
        favorites_only: BoolProperty(name="Favorites", description="Show only favorite LUTs")
        category: EnumProperty(name="Category", items=_category_enum_items)
        sort: EnumProperty(name="Sort", items=[
            ("NAME", "Name", ""), ("CATEGORY", "Category", ""), ("DATE", "Newest", "")])
        node_group: PointerProperty(type=bpy.types.NodeTree, options={"HIDDEN"})
        error: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    return LUTM_Settings


# ===========================================================================
# Operators


class LUTM_OT_step(bpy.types.Operator):
    bl_idname = "lut_manager.step"
    bl_label = "Next / Previous LUT"
    bl_options = {"INTERNAL", "UNDO"}

    direction: IntProperty(default=1)

    def execute(self, context):
        settings = context.scene.lut_manager
        items = filtered(settings)
        if not items:
            return {"CANCELLED"}
        current = entry_for(settings.lut_path)
        i = items.index(current) if current in items else -self.direction
        settings.lut = items[(i + self.direction) % len(items)].path
        return {"FINISHED"}


class LUTM_OT_toggle_favorite(bpy.types.Operator):
    bl_idname = "lut_manager.toggle_favorite"
    bl_label = "Toggle Favorite"
    bl_description = "Add or remove the current LUT from your favorites"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        path = context.scene.lut_manager.lut_path
        if not path:
            return {"CANCELLED"}
        toggle_favorite(path)
        return {"FINISHED"}


class LUTM_OT_refresh(bpy.types.Operator):
    bl_idname = "lut_manager.refresh"
    bl_label = "Refresh LUT Folder"
    bl_description = "Rescan the LUT folder and rebuild thumbnails"

    def execute(self, context):
        scan(prefs().lut_dir)
        return {"FINISHED"}


class LUTM_OT_remove(bpy.types.Operator):
    bl_idname = "lut_manager.remove"
    bl_label = "Remove From Compositor"
    bl_description = "Remove the LUT node from the compositor and reconnect the original nodes"
    bl_options = {"UNDO"}

    def execute(self, context):
        remove_node(context.scene)
        context.scene.lut_manager.lut_path = ""
        return {"FINISHED"}


# ===========================================================================
# Panel


class LUTM_PT_panel(bpy.types.Panel):
    bl_label = "LUT Manager"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LUT"

    def draw_header(self, context):
        self.layout.prop(context.scene.lut_manager, "enabled", text="")

    def draw(self, context):
        layout = self.layout
        settings = context.scene.lut_manager
        addon_prefs = prefs()

        octane = is_octane(context.scene)
        if octane:
            layout.label(text="Octane: applied through the Octane imager", icon="INFO")
        else:
            layout.prop(context.space_data.shading, "use_compositor", text="Compositor")

        if not addon_prefs.lut_dir:
            box = layout.box()
            box.label(text="Choose the folder with your .cube files:", icon="INFO")
            box.prop(addon_prefs, "lut_dir", text="")
            return

        # Browser
        box = layout.box()
        row = box.row()
        row.template_icon_view(settings, "lut", show_labels=True, scale=7.0, scale_popup=5.0)
        col = row.column(align=True)
        is_fav = norm(settings.lut_path) in favorites()
        col.operator("lut_manager.toggle_favorite", text="", icon="SOLO_ON" if is_fav else "SOLO_OFF")
        col.separator()
        col.operator("lut_manager.step", text="", icon="TRIA_UP").direction = -1
        col.operator("lut_manager.step", text="", icon="TRIA_DOWN").direction = 1
        col.separator()
        col.operator("lut_manager.refresh", text="", icon="FILE_REFRESH")

        # Filters
        box = layout.box()
        row = box.row(align=True)
        row.prop(settings, "search", text="", icon="VIEWZOOM")
        row.label(text=f"{len(filtered(settings))} results")
        row = box.row(align=True)
        row.prop(settings, "favorites_only", text="", icon="SOLO_ON")
        row.prop(settings, "category", text="")
        row.prop(settings, "sort", text="")

        # Grade
        box = layout.box()
        entry = entry_for(settings.lut_path)
        row = box.row(align=True)
        row.label(text=entry.name if entry else "No LUT selected", icon="IMAGE_RGB")
        if settings.lut_path:
            row.operator("lut_manager.remove", text="", icon="X")
        if settings.error:
            box.label(text=settings.error, icon="ERROR")
        col = box.column()
        col.active = settings.enabled
        col.prop(settings, "factor", slider=True)
        # Octane's imager only takes the LUT file and its strength.
        sub = col.column()
        sub.active = not octane
        sub.prop(settings, "exposure")
        sub.prop(settings, "input_space", text="Input")
        sub.prop(settings, "output_space", text="Output")

        layout.prop(addon_prefs, "lut_dir", text="")


# ===========================================================================
# Registration


@bpy.app.handlers.persistent
def _on_load(_dummy):
    # Loading a file drops msgbus subscriptions.
    _subscribe_msgbus()
    # The LUT strip is a generated image, so its pixels are not saved with the .blend.
    for scene in bpy.data.scenes:
        if scene.lut_manager.lut_path:
            apply_lut(scene)


_classes = [LUTM_Preferences, LUTM_OT_step, LUTM_OT_toggle_favorite, LUTM_OT_refresh,
            LUTM_OT_remove, LUTM_PT_panel]
_settings_cls = None


def register():
    global _previews, _settings_cls
    _previews = bpy.utils.previews.new()
    _settings_cls = _make_settings()
    bpy.utils.register_class(_settings_cls)
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lut_manager = PointerProperty(type=_settings_cls)
    bpy.app.handlers.load_post.append(_on_load)
    _subscribe_msgbus()


def unregister():
    global _previews, _scanned_dir
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    del bpy.types.Scene.lut_manager
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(_settings_cls)

    if bpy.app.timers.is_registered(_thumbnail_tick):
        bpy.app.timers.unregister(_thumbnail_tick)
    _pending.clear()
    _entries.clear()
    _by_path.clear()
    _scanned_dir = None
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
