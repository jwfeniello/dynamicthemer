"""Build a viewable scene from a PS3 dynamic theme.

Input can be:
  - a .zip containing the theme .pkg and its .rap license
  - a .pkg (pass --rap if its theme file is an encrypted .edat)
  - a .p3t

Output goes to themes/<name>/ (extracted files, unpacked tree, scene.glb) and the theme is
added to themes/index.json so the viewer lists it.

Usage:
  python tools/build_theme.py "PS3 Theme.zip" --name my_theme
  python tools/build_theme.py theme.pkg --rap license.rap --name my_theme
  python tools/build_theme.py theme.p3t --name my_theme
"""
import argparse, json, os, shutil, subprocess, sys, zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)

def run(script, *args):
    subprocess.run([sys.executable, "-I", os.path.join(TOOLS, script), *map(str, args)], check=True)

def find(folder, ext):
    for dirpath, _, names in os.walk(folder):
        for n in sorted(names):
            if n.lower().endswith(ext):
                return os.path.join(dirpath, n)
    return None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help=".zip, .pkg or .p3t")
    ap.add_argument("--name", help="output folder name under themes/ (default: input file name)")
    ap.add_argument("--rap", help=".rap license for an encrypted theme")
    ap.add_argument("--title", help="label shown in the viewer")
    a = ap.parse_args()

    name = a.name or os.path.splitext(os.path.basename(a.input))[0].replace(" ", "_")
    out = os.path.join(ROOT, "themes", name)
    os.makedirs(out, exist_ok=True)
    src, rap = os.path.abspath(a.input), a.rap and os.path.abspath(a.rap)

    if src.lower().endswith(".zip"):
        zdir = os.path.join(out, "zip")
        with zipfile.ZipFile(src) as z:
            z.extractall(zdir)
        src = find(zdir, ".pkg") or find(zdir, ".p3t")
        rap = rap or find(zdir, ".rap")
        if not src:
            sys.exit("no .pkg or .p3t found in the zip")

    p3t = os.path.join(out, "theme.p3t")
    if src.lower().endswith(".pkg"):
        pkg_out = os.path.join(out, "pkg_out")
        run("ps3_pkg_extract.py", src, pkg_out)
        edat, plain = find(pkg_out, ".edat"), find(pkg_out, ".p3t")
        if edat:
            if not rap:
                sys.exit("this theme is encrypted (.edat); pass its .rap with --rap")
            run("ps3_edat_decrypt.py", edat, rap, p3t)
        elif plain:
            shutil.copyfile(plain, p3t)
        else:
            sys.exit("no theme file found in the package")
    else:
        shutil.copyfile(src, p3t)

    tree = os.path.join(out, "p3t_out")
    run("p3t_unpack.py", p3t, tree)
    raf = next((os.path.join(tree, d) for d in sorted(os.listdir(tree))
                if d.endswith("_raf") and os.path.isdir(os.path.join(tree, d))), None)
    if not raf:
        print("This theme has no dynamic (3D) background; images are in", tree)
        return
    run("raf_to_gltf.py", raf, os.path.join(out, "scene.glb"))

    index_path = os.path.join(ROOT, "themes", "index.json")
    index = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            index = json.load(f)
    index = [t for t in index if t.get("path") != f"{name}/scene.glb"]
    index.append({"title": a.title or name.replace("_", " "), "path": f"{name}/scene.glb"})
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    print(f"Done: themes/{name}/scene.glb (listed in the viewer)")

if __name__ == "__main__":
    main()
