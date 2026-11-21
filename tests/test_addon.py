"""Headless test suite for LUT Manager. Runs inside Blender:

    blender -b --factory-startup --python tests/test_addon.py -- [options]

Options (after "--"):
    --addon-dir DIR   folder containing the lut_manager package (default: the repo root)
    --gpu             also check the GPU compositor (needs a GPU; the viewport uses it)
    --dump FILE       write every result (pixel hashes, parsed LUTs) to a JSON file, so
                      two versions can be compared with tests/compare_dumps.py

Exits with status 1 if any check fails. It writes only to a temporary folder; run it
with BLENDER_USER_RESOURCES pointing at a scratch folder to keep your real profile
untouched (toggling a favorite may save preferences).
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import traceback

import addon_utils
import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--addon-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
parser.add_argument("--gpu", action="store_true")
parser.add_argument("--dump")
args = parser.parse_args(argv)

sys.path.insert(0, os.path.abspath(args.addon_dir))
addon_utils.enable("lut_manager", default_set=True)
import lut_manager as L  # noqa: E402

TMP = tempfile.mkdtemp(prefix="lutm_test_")
LUT_DIR = os.path.join(TMP, "luts")
RES = 64
results = {"blender": bpy.app.version_string, "checks": [], "dump": {}}
failures = []


def check(name, ok, detail=""):
    results["checks"].append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures.append(name)


def digest(arr):
    return hashlib.sha256(np.ascontiguousarray(arr, dtype=np.float32).tobytes()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Test LUT folder


def write_3d(path, n, fn, header="", nl="\n", bom=False, comment=False):
    g = np.linspace(0, 1, n)
    lines = [l for l in header.split("\n") if l] + [f"LUT_3D_SIZE {n}"]
    for b in g:
        for gg in g:
            for r in g:
                lines.append("%.6f %.6f %.6f" % fn(r, gg, b) + ("  # c" if comment and r == 0 else ""))
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        f.write(nl.join(lines) + nl)


os.makedirs(os.path.join(LUT_DIR, "Film"))
write_3d(os.path.join(LUT_DIR, "Film", "Weird_Swap.cube"), 17, lambda r, g, b: (g * g, 1 - b, 0.5 * r + 0.5 * b ** 3))
write_3d(os.path.join(LUT_DIR, "Warm.cube"), 33, lambda r, g, b: (min(1, r * 1.1), g, b * 0.85))
write_3d(os.path.join(LUT_DIR, "Messy Resolve.cube"), 9, lambda r, g, b: (b, r, g),
         header='# Created by test\nTITLE "Grade #3"\n\nLUT_IN_VIDEO_RANGE\nDOMAIN_MIN 0.1 0.0 0.0\nDOMAIN_MAX 0.9 1.0 0.8',
         nl="\r\n", bom=True, comment=True)
write_3d(os.path.join(LUT_DIR, "InputRange.cube"), 5, lambda r, g, b: (r * r, g, b), header="LUT_3D_INPUT_RANGE 0.0 0.5")
with open(os.path.join(LUT_DIR, "Curve1D.cube"), "w") as f:
    f.write("LUT_1D_SIZE 5\n")
    for v in np.linspace(0, 1, 5):
        f.write(f"{v ** 2} {v} {np.sqrt(v)}\n")
with open(os.path.join(LUT_DIR, "._Warm.cube"), "wb") as f:  # macOS resource fork: must be skipped
    f.write(b"\x00\x05junk")
with open(os.path.join(LUT_DIR, "Broken.cube"), "w") as f:
    f.write("LUT_3D_SIZE 33\n0 0 0\n")
VALID = ["Curve1D", "InputRange", "Messy Resolve", "Warm", "Weird Swap"]


def run_checks():
    ctx = bpy.context
    sc = ctx.scene
    st = sc.lut_manager

    # -- library -----------------------------------------------------------
    L.prefs().lut_dir = LUT_DIR
    ents = L.entries()
    names = [(e.name, e.category) for e in ents]
    check("scan finds .cube files, skips ._ files, uses subfolder as category",
          names == [("Broken", "Uncategorized"), ("Curve1D", "Uncategorized"), ("InputRange", "Uncategorized"),
                    ("Messy Resolve", "Uncategorized"), ("Warm", "Uncategorized"), ("Weird Swap", "Film")], names)
    check("categories", L.categories() == ["Film", "Uncategorized"], L.categories())
    for e in ents:
        L._make_thumbnail(e)
    check("thumbnails made for every file (broken one too)", all(e.path in L._previews for e in ents))

    # -- parser --------------------------------------------------------------
    m = L.read_cube(os.path.join(LUT_DIR, "Messy Resolve.cube"))
    check("messy file: BOM, CRLF, comments, title with #, domain",
          m.size == 9 and m.title == "Grade #3" and m.domain_min.tolist() == [np.float32(0.1), 0, 0]
          and m.domain_max.tolist() == [np.float32(0.9), 1, np.float32(0.8)], (m.size, m.title))
    r = L.read_cube(os.path.join(LUT_DIR, "InputRange.cube"))
    check("LUT_3D_INPUT_RANGE sets the domain", r.domain_max.tolist() == [0.5] * 3)
    c = L.read_cube(os.path.join(LUT_DIR, "Curve1D.cube"))
    check("1D LUT expands to 33^3", c.table.shape == (33, 33, 33, 3))
    try:
        L.read_cube(os.path.join(LUT_DIR, "Broken.cube"))
        check("truncated file raises ValueError", False)
    except ValueError as exc:
        check("truncated file raises ValueError", "expected 35937 entries, found 1" in str(exc), exc)
    for e in ents:
        try:
            lut = L.read_cube(e.path)
            results["dump"][f"parse:{e.name}"] = [digest(lut.table), lut.domain_min.tolist(), lut.domain_max.tolist(), lut.title]
        except ValueError as exc:
            results["dump"][f"parse:{e.name}"] = ["error", str(exc)]

    # -- compositor ----------------------------------------------------------
    sc.render.resolution_x = sc.render.resolution_y = RES
    sc.render.resolution_percentage = 100
    # Cycles on the CPU renders without a GPU (CI machines have none); the compositor
    # replaces the render with the test image anyway.
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 1
    rng = np.random.default_rng(1)
    src = rng.random((RES, RES, 3)).astype(np.float32)
    src[0, :8] = [[0, 0, 0], [1, 1, 1], [1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, .5], [1, 1, 0], [.999, .001, .5]]
    im = bpy.data.images.new("src", RES, RES, float_buffer=True)
    im.colorspace_settings.name = "Non-Color"
    px = np.ones((RES, RES, 4), np.float32)
    px[..., :3] = src
    im.pixels.foreach_set(px.ravel())

    tree = L._scene_tree(sc)
    tree.nodes.clear()
    inode = tree.nodes.new("CompositorNodeImage")
    inode.image = im
    out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(inode.outputs[0], out.inputs[0])
    st.input_space = "Non-Color"
    st.output_space = "Non-Color"

    def render(device):
        sc.render.compositor_device = device
        sc.render.image_settings.file_format = "OPEN_EXR"
        sc.render.image_settings.color_depth = "32"
        sc.render.filepath = os.path.join(TMP, f"out_{device}.exr")
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(sc.render.filepath, check_existing=False)
        a = np.array(img.pixels[:], np.float32).reshape(RES, RES, 4)[..., :3]
        bpy.data.images.remove(img)
        return a

    devices = ["CPU"] + (["GPU"] if args.gpu else [])
    for e in ents:
        st.lut = e.path
        if e.name == "Broken":
            check("broken LUT shows an error in the panel", st.error.startswith("Could not read LUT"), st.error)
            continue
        check(f"{e.name}: node inserted before the output",
              out.inputs[0].links[0].from_node.name == L.NODE_NAME and not st.error, st.error)
        ref = L.read_cube(e.path).apply(src)
        for dev in devices:
            a = render(dev)
            err = float(np.abs(a - ref).max())
            check(f"{e.name}: {dev} compositor matches reference trilinear lookup", err < 1e-4, f"max err {err:.2e}")
            results["dump"][f"render:{e.name}:{dev}"] = digest(np.round(a, 5))

    st.factor = 0.0
    a = render("CPU")
    check("factor 0 returns the input", float(np.abs(a - src).max()) < 1e-6)
    st.factor = 1.0
    st.enabled = False
    a = render("CPU")
    check("disabled returns the input", float(np.abs(a - src).max()) < 1e-6)
    st.enabled = True
    L.remove_node(sc)
    check("remove reconnects the original nodes",
          L.find_node(sc) is None and out.inputs[0].links[0].from_node == inode)

    # -- enum with nothing selected (used to warn on every redraw) ------------
    st.lut_path = ""
    items = L._lut_enum_items(st, ctx)
    check("no selection: browser has a placeholder item, value resolves to it",
          items[0][0] == "NONE" and st.lut == "NONE", (items[0][:2], st.lut))

    # -- operators -----------------------------------------------------------
    st.lut = ents[1].path
    bpy.ops.lut_manager.step(direction=1)
    check("next LUT", L.norm(st.lut_path) == ents[2].path)
    bpy.ops.lut_manager.step(direction=-1)
    bpy.ops.lut_manager.step(direction=-1)
    check("previous LUT", L.norm(st.lut_path) == ents[0].path)
    bpy.ops.lut_manager.toggle_favorite()
    st.favorites_only = True
    check("favorite filter", [e.name for e in L.filtered(st)] == [ents[0].name])
    bpy.ops.lut_manager.toggle_favorite()
    st.favorites_only = False
    st.search = "warm"
    check("search filter", [e.name for e in L.filtered(st)] == ["Warm"])
    st.search = ""
    st.category = "Film"
    check("category filter", [e.name for e in L.filtered(st)] == ["Weird Swap"])
    st.category = "ALL"

    # -- a scene without a compositor ------------------------------------------
    sc2 = bpy.data.scenes.new("Second")
    sc2.lut_manager.lut = ents[-1].path
    t2 = L._scene_tree(sc2, create=False)
    kinds = sorted(n.bl_idname for n in t2.nodes) if t2 else []
    check("new scene: compositor created with Render Layers -> LUT -> output",
          "CompositorNodeGroup" in kinds and "CompositorNodeRLayers" in kinds, kinds)

    check_octane(sc, ents)


# ---------------------------------------------------------------------------
# Octane: use the real add-on when it is loaded (the OctaneBlender add-on, which needs
# a display), otherwise register a stand-in with the same property names so the sync
# logic is still exercised (CI, plain Blender, background mode).


def _fake_octane():
    class FakeImager(bpy.types.PropertyGroup):
        custom_lut: bpy.props.StringProperty()
        lut_strength: bpy.props.FloatProperty(default=1.0)

    class FakeViewCam(bpy.types.PropertyGroup):
        imager: bpy.props.PointerProperty(type=FakeImager)

    class FakeSceneOctane(bpy.types.PropertyGroup):
        use_preview_camera_imager: bpy.props.BoolProperty()
        use_render_camera_imager: bpy.props.BoolProperty()

    class FakeCameraOctane(bpy.types.PropertyGroup):
        imager: bpy.props.PointerProperty(type=FakeImager)

    class FakeOctaneEngine(bpy.types.RenderEngine):
        bl_idname = "octane"
        bl_label = "Octane (test stand-in)"

    for cls in (FakeImager, FakeViewCam, FakeSceneOctane, FakeCameraOctane, FakeOctaneEngine):
        bpy.utils.register_class(cls)
    bpy.types.Scene.oct_view_cam = bpy.props.PointerProperty(type=FakeViewCam)
    bpy.types.Scene.octane = bpy.props.PointerProperty(type=FakeSceneOctane)
    bpy.types.Camera.octane = bpy.props.PointerProperty(type=FakeCameraOctane)


def check_octane(sc, ents):
    real = hasattr(bpy.types.Scene, "oct_view_cam")
    if not real:
        _fake_octane()
    label = "real Octane" if real else "Octane stand-in"
    st = sc.lut_manager
    if sc.camera is None:
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        sc.collection.objects.link(cam)
        sc.camera = cam
    if not L.find_node(sc):
        st.lut = ents[1].path
    sc.render.engine = "octane"
    L._on_engine_or_camera_change()  # msgbus doesn't fire in background mode
    st.lut = ents[4].path
    st.factor = 0.6
    view, cam_img = sc.oct_view_cam.imager, sc.camera.data.octane.imager
    check(f"{label}: LUT and strength copied to viewport and camera imagers",
          view.custom_lut == st.lut_path == cam_img.custom_lut and abs(view.lut_strength - 0.6) < 1e-6,
          (os.path.basename(view.custom_lut), view.lut_strength))
    check(f"{label}: imagers switched on",
          sc.octane.use_preview_camera_imager and sc.octane.use_render_camera_imager)
    check(f"{label}: compositor node muted (no double grading)", L.find_node(sc).mute)
    st.enabled = False
    check(f"{label}: disabling clears our LUT", view.custom_lut == "" and cam_img.custom_lut == "")
    view.custom_lut = "C:/manual/Own.cube"
    L.sync_values(sc, st)
    check(f"{label}: a LUT set by hand in Octane is kept", view.custom_lut == "C:/manual/Own.cube")
    st.enabled = True
    sc.render.engine = "CYCLES"
    L._on_engine_or_camera_change()
    check(f"{label}: switching back to Blender engines unmutes the node", not L.find_node(sc).mute)


try:
    run_checks()
    addon_utils.disable("lut_manager")
    check("unregister", not hasattr(bpy.types.Scene, "lut_manager"))
except Exception:  # noqa: BLE001 - report and fail instead of hanging Blender
    traceback.print_exc()
    failures.append("exception")

if args.dump:
    with open(args.dump, "w") as f:
        json.dump(results, f, indent=1)
passed = sum(1 for c in results["checks"] if c[1])
print(f"\nRESULT {passed}/{len(results['checks'])} checks passed on Blender {bpy.app.version_string}"
      + (f", FAILED: {failures}" if failures else ""))
sys.stdout.flush()
os._exit(1 if failures else 0)
