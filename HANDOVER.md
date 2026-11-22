# Handover: LUT Manager

For whoever maintains this add-on next. User documentation is in [README.md](README.md);
a compact version of this file for AI assistants is in [AGENTS.md](AGENTS.md).

## What the project is

A Blender add-on that lets you browse a folder of `.cube` colour LUTs (with thumbnails,
search, favorites and categories) and preview the chosen LUT live in the 3D viewport.

- Author: Matteo Fontana. Original work, not a fork; there is no upstream to merge from.
- License: GPL-3.0-or-later ([LICENSE](LICENSE)).
- Repository: <https://github.com/mttfntn/lut-manager-blender> (public), branch `main`.

## What you need

| Need | Version / note |
|---|---|
| Blender | **5.3** (the only supported version; set in `bl_info["blender"]` and in the CI matrix) |
| OctaneBlender add-on | Optional. Tested with 32.2 for Blender 5.3. Needs the Octane render server to show an image. |
| Python | Blender's bundled Python and numpy. Plain Python 3 is enough for `tools/build_release.py` and `tests/compare_dumps.py`. |
| git, GitHub CLI (`gh`) | For releases and CI checks. |

## How the code is organised

| Path | What it is |
|---|---|
| `lut_manager/__init__.py` | The whole add-on (one module, about 1000 lines, in sections; see below). |
| `tests/test_addon.py` | Headless test suite, run inside Blender. 40 checks. Exits 1 on failure. |
| `tests/compare_dumps.py` | Compares two `--dump` result files (before/after a change). |
| `tools/build_release.py` | Builds `dist/lut_manager-<version>.zip` (package folder + LICENSE). |
| `tools/get_blender.sh` | Downloads the newest Linux build of a Blender series, with retries and mirrors (used by CI). |
| `.github/workflows/tests.yml` | CI: runs the test suite on every push to `main` and on pull requests. |
| `.github/workflows/release.yml` | On a `vX.Y.Z` tag: tests, builds, publishes a GitHub release with the zip. |
| `README.md` | User guide. `HANDOVER.md` and `AGENTS.md` are not shipped in the zip. |

Sections of `lut_manager/__init__.py`, in order:

| Section | Main functions | Job |
|---|---|---|
| `.cube` files | `read_cube`, `_parse_lines`, `CubeLUT.apply`, `CubeLUT.strip_pixels` | Parse files into a numpy table; apply a LUT in numpy (for thumbnails and as the test reference). |
| Compositor | `build_group`, `insert_node`, `remove_node`, `sync_values`, `update_lut_image` | Build the LUT node group and splice it in front of the compositor output. |
| Octane | `sync_octane`, `is_octane`, `_subscribe_msgbus` | Copy the LUT into Octane's camera imagers; re-sync when the engine or camera changes. |
| Library | `scan`, `entries`, `filtered`, `favorites`, `_thumbnail_tick` | Folder scan, filters, favorites (JSON in the add-on preferences), thumbnails made 4 per timer tick. |
| Properties | `apply_lut`, `_lut_enum_items`, `_lut_get`/`_lut_set`, `_make_settings` | Per-scene settings (`scene.lut_manager`) and their update callbacks. |
| Operators, Panel, Registration | `LUTM_OT_*`, `LUTM_PT_panel`, `register`, `_on_load` | UI in 3D Viewport > N sidebar > LUT. |

## How the main feature works

Blender has no compositor node for 3D LUTs, so the add-on builds one:

1. `read_cube` parses the file into a table `(N, N, N, 3)` indexed `[blue, green, red]`.
   1D LUTs are expanded to a 33³ table.
2. `update_lut_image` stores the table as a hidden float image `.LUTM <scene>`, N² × N
   pixels: one N × N slice per blue level, side by side.
3. `build_group` creates the node group "LUT Manager" (59 nodes): Exposure, convert to the
   Input colour space, normalise each channel to the LUT domain, compute the two
   neighbouring indices and fractions per channel, sample the 8 surrounding entries with
   **nearest-pixel** Map UV nodes, mix them (trilinear interpolation), convert from the
   Output colour space back to `scene_linear`, mix with the original by Factor, keep alpha.
4. `insert_node` puts the group just before the compositor's Group Output and keeps the
   user's nodes. The viewport compositor (Viewport Shading > Compositor) runs it on the GPU.

With **Octane** as the render engine, the compositor doesn't affect the viewport, so
`sync_octane` writes the file path into `custom_lut` and Factor into `lut_strength` on both
`scene.oct_view_cam.imager` (free view) and the active camera's `octane.imager`, switches
both imagers on, and mutes the compositor node so final renders aren't graded twice.

Data flow: picking a LUT → `_lut_set` → `apply_lut` (read, image, group, insert, sync).
Sliders and menus only call `sync_values`, which never rebuilds the group.

## How to make a change

1. Edit `lut_manager/__init__.py`.
2. Run the tests (next section), ideally also with `--gpu`.
3. For anything that could change results, compare before and after:
   ```bash
   git show HEAD:lut_manager/__init__.py > /tmp/old/lut_manager/__init__.py   # make /tmp/old/lut_manager first
   blender -b --factory-startup --python tests/test_addon.py -- --addon-dir /tmp/old --dump old.json
   blender -b --factory-startup --python tests/test_addon.py -- --dump new.json
   python tests/compare_dumps.py old.json new.json
   ```
4. Try it in Blender: install the zip from `tools/build_release.py` in a scratch profile
   (see "Things to know"), and look at the panel and viewport.
5. Commit in small steps and push to `main`; check the Tests run on GitHub.

## How to run the tests

```bash
blender -b --factory-startup --python tests/test_addon.py
blender -b --factory-startup --python tests/test_addon.py -- --gpu      # also the GPU compositor
```

On Windows use the full path, e.g. `"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"`.
Set `BLENDER_USER_RESOURCES` to an empty scratch folder so your own Blender profile is
never touched (the favorites test may save preferences).

- Without the Octane add-on (and always in `-b` background mode, where Octane can't start),
  the Octane checks use a stand-in with the same property names. To test the real Octane
  add-on, run without `-b` with Octane enabled, e.g. in a scratch profile that contains a
  copy of the `octane` add-on:
  `blender --factory-startup --python-expr "import addon_utils; addon_utils.enable('octane', default_set=True)" --python tests/test_addon.py -- --gpu`
- Expected result: `RESULT 40/40 checks passed`.

## How to make a release

1. Bump `bl_info["version"]` in `lut_manager/__init__.py`.
2. The release, license and language badges in `README.md` update themselves. Update the Blender badge only if the supported Blender version changed.
3. Commit and push to `main`; wait for Tests to pass.
4. Create an **annotated** tag whose message is the release notes, and push it:
   ```bash
   git tag -a v1.3.0 -m "LUT Manager 1.3.0" -m "- What changed"
   git push origin v1.3.0
   ```
5. The Release workflow tests again, checks that the tag matches `bl_info`, builds the zip
   and publishes the GitHub release with the tag message as notes.

To support another Blender version: change `bl_info["blender"]`, the `matrix.blender` list in
`tests.yml`, the Blender badge and the README, then run the tests on that version.

## Bringing in updates from the original project

Not applicable: this is an original project, not a fork.

## Things to know

- **Test in isolation.** Never test in your everyday Blender. Use a scratch profile:
  `BLENDER_USER_RESOURCES=<empty folder>` plus `--factory-startup`.
- **Installed copy vs repo.** Blender runs the installed copy (`%APPDATA%\Blender Foundation\Blender\5.3\scripts\addons\lut_manager\`),
  not the repo. An outdated install is the first suspect when something "doesn't change".
  Version 1.1 was a single file `lut_manager.py`; it must be deleted before installing 1.2+.
- **Blender docstrings become tooltips.** Operator, panel, preferences and property-group
  classes use `#` comments, not docstrings.
- **Mix node sockets:** look them up by identifier (`_socket(node.inputs, "A_Color")`);
  `inputs["A"]` is ambiguous on `ShaderNodeMix`.
- **Map UV must be "Nearest".** Bilinear or anisotropic filtering blurs across LUT cells.
- **Enum callbacks:** the item strings must stay referenced (`_lut_items` etc.), and
  `_lut_get` returns -1 when nothing is selected, so the item list needs an item numbered -1
  (otherwise Blender warns on every redraw; fixed in 1.2).
- **Generated images aren't saved.** `_on_load` rebuilds the LUT image after a file opens.
- **msgbus** subscriptions are dropped when a file loads; `_on_load` re-subscribes. msgbus
  doesn't fire in background mode, so tests call `_on_engine_or_camera_change()` directly.
- **Parser fast path.** `read_cube` sends the data block to numpy in one call and falls back to
  line-by-line rules when the block has comments, keywords or `inf`/`nan`. It was checked to
  give identical results to the old parser on 1,134 varied files; keep that equivalence
  if you change it.
- **Octane panel names quoted in README** ("Octane Imager(Preview Mode)", "Tone Mapping",
  "ACES tone mapping") come from the Octane add-on's code (`uis/camera.py`); recheck them
  after Octane updates.
- **CI has no GPU:** it tests the CPU compositor and renders with Cycles on the CPU.

## Known limits

- Only `.cube` (3D and 1D). One LUT per scene.
- Only Blender 5.3 is supported and tested; macOS hasn't been tested (no platform-specific code).
- Selecting a LUT rebuilds the whole node group (about 60 nodes); this could be skipped when
  only the table changes. Thumbnails take about 13 ms each for a 33-point LUT.
- GPU compositor results differ from the numpy reference by about 1e-7 (float rounding); the tests allow 1e-4.
- The real Octane path can only be tested with a display and the Octane add-on installed.
