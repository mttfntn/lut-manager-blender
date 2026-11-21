# AGENTS.md: LUT Manager (Blender add-on)

Compact guide for AI assistants. Human version: HANDOVER.md. User guide: README.md.

## Facts
- Blender 5.3 only (`bl_info["blender"] = (5, 3, 0)`); GPL-3.0-or-later; original work (no upstream).
- Repo: github.com/mttfntn/lut-manager-blender (private), branch `main`, remote `origin`.
- All add-on code: `lut_manager/__init__.py` (single module). Module name `lut_manager`; prefs keyed by `__name__`.
- Optional: OctaneBlender add-on 32.x for Blender 5.3 (`scene.oct_view_cam`, `camera.data.octane`).

## Commands
```bash
# tests (40 checks, exit 1 on failure); isolate the profile
BLENDER_USER_RESOURCES=$(mktemp -d) blender -b --factory-startup --python tests/test_addon.py [-- --gpu --dump out.json --addon-dir DIR]
python tests/compare_dumps.py old.json new.json      # before/after comparison, exit 1 on differences
python tools/build_release.py [--expect-version vX.Y.Z]   # -> dist/lut_manager-X.Y.Z.zip (lut_manager/__init__.py + LICENSE)
bash tools/get_blender.sh 5.3                         # Linux CI: download Blender, prints executable path
gh run list --limit 5; gh run view <id> --log-failed  # check CI
```
Windows Blender: `"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"`.

## Architecture (sections of `lut_manager/__init__.py`)
- `.cube`: `read_cube(path) -> CubeLUT(table[b,g,r,3], domain_min, domain_max, title)`; `_parse_lines`; `CubeLUT.apply` (numpy trilinear); `strip_pixels` (N²×N RGBA).
- Compositor: `update_lut_image` (hidden `.LUTM <scene>` float image, Non-Color), `build_group` (59 nodes: 8 nearest Map UV samples + 7 mixes), `insert_node`/`remove_node` (before Group Output), `sync_values`.
- Octane: `sync_octane` writes `custom_lut`/`lut_strength` on both imagers, enables `use_preview_camera_imager`/`use_render_camera_imager`, node muted; msgbus on `RenderSettings.engine` and `Scene.camera`.
- Library: `scan`/`entries`/`entry_for`/`filtered`/`favorites` (JSON in prefs), thumbnails via `bpy.app.timers` 4 per tick.
- Properties: `scene.lut_manager` (PropertyGroup built in `_make_settings` at register); `lut` enum is a view of `lut_path` via get/set.

## Data flow
pick thumbnail → `_lut_set` → `apply_lut`: `read_cube` → `update_lut_image` → `build_group` → `insert_node` → `sync_values` (→ `sync_octane`). Sliders/menus → `sync_values` only. File load → `_on_load` (re-subscribe msgbus, rebuild images).

## Rules and pitfalls
- No docstrings on bpy classes (Operator/Panel/AddonPreferences/PropertyGroup): Blender shows them as tooltips. Use `#` comments.
- ShaderNodeMix: get sockets by identifier (`_socket(..., "A_Color")`), not name.
- Map UV `Interpolation` must stay "Nearest".
- Enum items: keep strings referenced in module lists; provide an item numbered -1 when nothing is selected (else redraw warnings).
- Generated image not saved → `_on_load` rebuilds. msgbus doesn't fire in `-b`; tests call `_on_engine_or_camera_change()`.
- `read_cube` fast path must stay result-identical to `_parse_lines`-only parsing (verified on 1,134 files). Changes to parsing need an old-vs-new comparison.
- Never test in the user's running Blender or real profile; use `BLENDER_USER_RESOURCES=<scratch>` + `--factory-startup`.
- Installed copy (`%APPDATA%/Blender Foundation/Blender/5.3/scripts/addons/lut_manager/`) may be stale; compare before debugging. Old 1.1 single file `lut_manager.py` must be removed.
- CI has no GPU: tests use the CPU compositor and Cycles CPU. Octane checks use a stand-in unless the real add-on is loaded (needs a display, not `-b`).

## How changes were verified (1.2.0)
- Baseline: the old version on 5.3 and 4.5 passed all checks except the enum warning bug.
- New: 40/40 on 5.3 (CPU+GPU), 40/40 with the real Octane 32.2 add-on in a GUI instance (scratch profile).
- Old vs new dumps on 5.3: 16/16 parse and pixel hashes identical; only difference is the fixed enum check.
- Parser: 0 differences on 1,134 generated files; 33³ parse 14.3 → 8.4 ms.

## Release checklist
1. Bump `bl_info["version"]`; update static badges (release, Blender) in README by hand (repo is private).
2. Push to `main`; Tests green.
3. `git tag -a vX.Y.Z -m "title" -m "notes"`; `git push origin vX.Y.Z`.
4. Release workflow: tests → checks tag == bl_info → builds zip → `gh release create` with tag message as notes. Verify with `gh release view vX.Y.Z`.

## Git setup
- `origin` = https://github.com/mttfntn/lut-manager-blender.git; work on `main`; small commits.
- Ignored: `__pycache__/`, `*.pyc`, `*.zip`, `dist/`, `.DS_Store`.
- Commit trailer used: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
