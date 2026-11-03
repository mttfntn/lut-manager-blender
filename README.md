# LUT Manager

A Blender add-on to browse your `.cube` LUTs and preview them live in the 3D viewport.

- Works in **Blender 4.5 and 5.x**, including **OctaneBlender** (built on Blender 4.5).
- Works on **Windows and macOS**.
- Everything is in one file: `lut_manager.py`.

---

## Installation

1. In Blender, open **Edit → Preferences → Add-ons**.
2. Click the **⌄** menu (top right) → **Install from Disk…** and pick `lut_manager.py`.
3. Tick **LUT Manager** in the list to enable it.
4. In the 3D viewport, press **N** and open the **LUT** tab.
5. Choose the folder that contains your `.cube` files.

> When you update `lut_manager.py`, install it again, then restart Blender.
> Blender runs its installed copy, not the file on your Desktop.

---

## The panel

| Control | What it does |
|---|---|
| **Checkbox in the header** | Turns the LUT on/off without losing your settings. |
| **Compositor** | Viewport compositor mode: *Disabled*, *Camera* or *Always*. Set it to **Always** to see the LUT in the viewport. Hidden when the render engine is Octane. |
| **Thumbnail** | Click it to browse all LUTs. Each thumbnail is a colour test chart with that LUT applied. |
| **★ / ▲ ▼ / ⟳** | Add the LUT to favorites, go to the previous/next LUT, rescan the folder. |
| **Search** | Filters by name or category and shows how many LUTs match. |
| **★ filter / Category / Sort** | Show only favorites, filter by subfolder, sort by name, category or newest. |
| **✕** next to the LUT name | Removes the LUT node from the compositor and reconnects your original nodes. |
| **LUT Factor** | How strongly the LUT is applied (0 = off, 1 = full). |
| **Exposure** | Brightness before the LUT, **in stops**. **0 is neutral**; +1 doubles the brightness. |
| **Input / Output** | Colour spaces the LUT expects and produces. See [Choosing Input and Output](#choosing-input-and-output). |
| **Folder field** | Your LUT folder. Each subfolder becomes a category. |

Favorites are saved in your Blender preferences, so they are the same in every `.blend` file.

---

## How it works

The add-on uses one of two methods, depending on the render engine.

### EEVEE, Cycles and Workbench: the compositor

Blender has no built-in node for 3D LUTs, so the add-on builds one.

1. **It reads the `.cube` file.** Both 3D and 1D LUTs are supported, as well as `DOMAIN_MIN/MAX`, `LUT_3D_INPUT_RANGE`, comments and files exported from Resolve or Photoshop.
2. **It turns the LUT into a small image.** The image is a strip of colour slices, one slice for each blue level.
3. **It adds a node group called "LUT Manager" to your compositor,** placed just before the output. Your existing nodes are kept. For every pixel, the group:
   - applies Exposure,
   - converts the image to the **Input** colour space,
   - looks up the 8 nearest LUT colours and blends them (trilinear interpolation, the standard method),
   - converts the result from the **Output** colour space back to Blender's working colour space,
   - mixes the result with the original by **LUT Factor**.
4. **Blender's viewport compositor runs the group on the GPU,** so you see the LUT live in the viewport. It is also applied to final renders (F12).

In tests, the output matched a reference LUT implementation exactly.

### Octane: Octane's own imager

Octane draws its viewport itself, so Blender's compositor never touches it. When the render engine is **Octane**, the add-on uses Octane's own LUT slot instead:

- The chosen `.cube` file goes into **Custom LUT** of the Octane camera imager. This is set for both the free viewport and the active camera.
- **LUT Factor** controls Octane's **LUT Strength**.
- If Octane's preview or render camera imager is off, the add-on switches it on.
- The compositor node is muted while Octane is the engine, so renders don't get the LUT twice.
- When you switch the render engine or the active camera, everything updates automatically.
- **Exposure**, **Input** and **Output** are greyed out, because Octane's LUT slot only takes the file and its strength.

When you turn the LUT off, the add-on only clears a Custom LUT that came from your LUT folder. A LUT you set by hand in Octane is kept.

---

## Octane settings

> ### ⚠️ Turn off ACES tone mapping
> In the Octane imager, open the **Tone Mapping** section and **untick "ACES tone mapping"**.
>
> With ACES on, Octane tone-maps the image with ACES and the LUT is applied to that result. Most LUTs are not designed for that, so the colours and contrast look wrong.
>
> Untick it in **both places**:
> - the **viewport** imager: *3D Viewport → N sidebar → **Octane** tab → **Octane Imager (Preview Mode)** → **Tone Mapping***
> - the **camera** imager: select the camera → *Properties → **Camera (Object Data)** tab → **Octane Imager (Render Mode)** → **Tone Mapping***
>
> Octane uses the viewport imager in free view and the camera imager when you look through the camera and for final renders. Turning ACES off in both keeps them consistent.

Other Octane tips:

- **Leave the View Transform at Raw.** Octane asks for it, and it has no effect on Octane's own LUT.
- **Don't touch Input and Output.** With Octane as the render engine they are ignored.
- **Start a render to see the LUT.** It appears in Octane's **Rendered** viewport once the Octane render server is running.

---

## Choosing Input and Output

These settings only apply to **EEVEE, Cycles and Workbench**. A LUT is just a table, "colour in → colour out", and it only looks right when the colours it receives are in the format it was designed for.

### Input: what kind of image the LUT was designed for

| Your LUT | Input |
|---|---|
| Creative / "look" LUTs for normal video (most LUT packs) | **sRGB** |
| LUTs for log footage ("LogC", "S-Log3", "V-Log", "Log to Rec709"…) | The matching **log** space in the list |
| You want Blender's AgX/Filmic tone mapping first, then the LUT on top (softer highlights) | **AgX Base sRGB** or **Filmic sRGB** |

### Output: what the LUT produces (must match your View Transform)

Nearly every LUT outputs normal sRGB video. The right setting depends on **Render Properties → Color Management → View**:

| View Transform | Output |
|---|---|
| **Standard** | **sRGB** |
| **Raw** | **Non-Color** |
| AgX / Filmic / others | Avoid them: Blender would tone-map the image a second time after the LUT. Switch the View to **Standard**. |

**The most common mistake** is View = *Raw* with Output = *sRGB*. The image gets converted one time too many and looks too dark and contrasty.

**Recommended starting point:**
- View **Standard**: Input **sRGB**, Output **sRGB**, Exposure **0**.
- View **Raw**: Input **sRGB**, Output **Non-Color**, Exposure **0**.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No **LUT** tab in the N panel | Make sure the add-on is ticked in Preferences → Add-ons, then restart Blender. |
| The LUT doesn't show in the viewport (EEVEE/Cycles) | Set **Compositor** to **Always**, and use **Material Preview** or **Rendered** shading. |
| The LUT doesn't show in Octane | Use Octane **Rendered** mode with the Octane server running, and make sure the header checkbox is on. |
| Colours look wrong in Octane | Untick **ACES tone mapping** in the imager's Tone Mapping section (see [Octane settings](#octane-settings)). |
| Image too dark, contrasty or washed out (EEVEE/Cycles) | Check **Input/Output** against the tables above, and set **Exposure** back to **0**. |
| "Could not read LUT" | The `.cube` file is incomplete or damaged; its thumbnail is red. Re-export it. |
| New LUTs don't appear | Click **⟳** to rescan the folder. |

---

## Limitations

- **Only `.cube` files** are supported (3D and 1D). `.3dl`, `.csp` and other formats are not.
- **One LUT per scene.** Different scenes can each have their own LUT.
- **Blender 4.5 or newer** is required. Older versions use different compositor nodes.
- **Each computer needs the LUT folder set once,** because the path is different on each one.
