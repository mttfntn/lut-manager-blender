"""Build the installable add-on zip: dist/lut_manager-<version>.zip

    python tools/build_release.py [--expect-version X.Y.Z]

The zip holds one folder, lut_manager/, with __init__.py and LICENSE, which is what
Blender's "Install from Disk" expects. The version comes from bl_info in
lut_manager/__init__.py; --expect-version (used by the release workflow) fails the
build when it doesn't match the git tag. Prints the zip path.
"""

import argparse
import ast
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "lut_manager")


def addon_version():
    tree = ast.parse(open(os.path.join(PACKAGE, "__init__.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "bl_info" for t in node.targets):
            return ".".join(str(v) for v in ast.literal_eval(node.value)["version"])
    sys.exit("bl_info not found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-version")
    args = parser.parse_args()

    version = addon_version()
    if args.expect_version and args.expect_version.lstrip("v") != version:
        sys.exit(f"tag {args.expect_version} does not match bl_info version {version}")

    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", f"lut_manager-{version}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(PACKAGE, "__init__.py"), "lut_manager/__init__.py")
        z.write(os.path.join(ROOT, "LICENSE"), "lut_manager/LICENSE")
    print(out)


if __name__ == "__main__":
    main()
