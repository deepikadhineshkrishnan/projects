#!/usr/bin/env python3
"""Turn a CVAT XML export into README-ready preview images and label statistics.

Supports both CVAT export formats:
  * "CVAT for images 1.1"  (boxes, polygons, polylines, points, ellipses, masks,
                            skeletons, cuboids, tags)
  * "CVAT for video 1.1"   (tracks of any of the above)

Usage (run from the repository root):
    python tools/cvat_showcase.py projects/01-object-detection-bboxes
    python tools/cvat_showcase.py --all
    python tools/cvat_showcase.py projects/04-video-object-tracking --samples 4

For each project folder it:
  1. finds export/annotations.xml (unzipping a CVAT .zip in export/ if needed),
  2. draws the annotations onto sample images -> previews/,
  3. makes an animated GIF for video/tracking projects -> previews/tracking.gif,
  4. writes label statistics into the project's README.md between the
     <!-- STATS:START --> / <!-- STATS:END --> and
     <!-- PREVIEWS:START --> / <!-- PREVIEWS:END --> markers, and to STATS.md.
"""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is missing. Run:  python3 -m pip install -r tools/requirements.txt")

REPO = Path(__file__).resolve().parent.parent
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
SHAPES = ("box", "polygon", "polyline", "points", "ellipse", "mask", "skeleton", "cuboid")
TYPE_NAMES = {
    "box": "Box", "polygon": "Polygon", "polyline": "Polyline", "points": "Points",
    "ellipse": "Ellipse", "mask": "Mask", "skeleton": "Skeleton", "cuboid": "Cuboid", "tag": "Tag",
}
MAX_SIDE = 1280   # longest side of preview images
GIF_SIDE = 640    # longest side of GIF frames
GIF_FRAMES = 48


# --------------------------------------------------------------------------- parsing

def make_shape(el, frame, label=None, track=None):
    return {
        "type": el.tag,
        "label": label or el.get("label") or "?",
        "frame": frame,
        "track": track,
        "occluded": el.get("occluded") == "1",
        "outside": el.get("outside") == "1",
        "keyframe": el.get("keyframe") == "1",
        "attrs": {a.get("name"): (a.text or "") for a in el.findall("attribute")},
        "el": el,
    }


def parse_export(xml_path: Path) -> dict:
    root = ET.parse(xml_path).getroot()
    meta = root.find("meta")

    labels, title, size = {}, None, None
    if meta is not None:
        for lab in meta.iter("label"):
            name = lab.findtext("name")
            if name:
                labels[name] = {
                    "color": (lab.findtext("color") or "").strip(),
                    "svg": lab.findtext("svg") or "",
                    "parent": lab.findtext("parent") or "",
                }
        for tag in ("project", "task", "job"):
            node = meta.find(tag)
            if node is not None and node.findtext("name"):
                title = node.findtext("name")
                break
        size_text = meta.findtext(".//size")
        if size_text and size_text.strip().isdigit():
            size = int(size_text)

    frames: dict[int, dict] = {}
    by_frame: dict[int, list] = defaultdict(list)
    tags: list[dict] = []

    for img in root.findall("image"):
        fid = int(img.get("id"))
        frames[fid] = {
            "name": img.get("name", ""),
            "w": int(float(img.get("width", 0))),
            "h": int(float(img.get("height", 0))),
        }
        for el in img:
            if el.tag == "tag":
                tags.append({"label": el.get("label", "?"), "frame": fid})
            elif el.tag in SHAPES:
                by_frame[fid].append(make_shape(el, fid))

    tracks: dict[int, dict] = {}
    for tr in root.findall("track"):
        tid = int(tr.get("id"))
        label = tr.get("label", "?")
        entries = []
        for el in tr:
            if el.tag not in SHAPES:
                continue
            s = make_shape(el, int(el.get("frame")), label=label, track=tid)
            entries.append(s)
            if not s["outside"]:
                by_frame[s["frame"]].append(s)
        tracks[tid] = {"label": label, "shapes": entries}

    if size is None:
        size = len(frames) if frames else (max(by_frame) + 1 if by_frame else 0)

    return {
        "title": title, "labels": labels, "frames": frames, "by_frame": by_frame,
        "tags": tags, "tracks": tracks, "size": size,
    }


# --------------------------------------------------------------------------- images on disk

def locate_images(export_dir: Path):
    files = sorted(
        p for p in export_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in IMG_EXT
    )
    by_name, by_stem = {}, {}
    for p in files:
        rel = p.relative_to(export_dir).as_posix()
        by_name[rel] = p
        if rel.startswith("images/"):
            by_name.setdefault(rel[len("images/"):], p)
        by_name.setdefault(p.name, p)
        by_stem.setdefault(p.stem.lower(), p)

    def resolve(fid: int, frames: dict):
        info = frames.get(fid)
        if info and info["name"]:
            name = info["name"]
            return by_name.get(name) or by_name.get(Path(name).name)
        # Video exports name frames frame_000000.PNG, frame_000001.PNG, ...
        hit = by_stem.get(f"frame_{fid:06d}")
        if hit:
            return hit
        return files[fid] if 0 <= fid < len(files) else None

    return resolve, len(files)


# --------------------------------------------------------------------------- drawing

def color_for(label: str, labels: dict):
    c = labels.get(label, {}).get("color", "")
    if re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    hue = int(hashlib.md5(label.encode()).hexdigest(), 16) % 360 / 360
    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.95)
    return int(r * 255), int(g * 255), int(b * 255)


def pts(text: str):
    out = []
    for pair in (text or "").split(";"):
        if "," in pair:
            x, y = pair.split(",")[:2]
            out.append((float(x), float(y)))
    return out


def get_font(size: int):
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def skeleton_edges(svg: str):
    """Return [(sublabel_a, sublabel_b)] from a CVAT skeleton label's SVG template."""
    names, edges = {}, []
    for tag, body in re.findall(r"<(line|circle)\b([^>]*)>", svg or ""):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', body))
        if tag == "circle" and "data-node-id" in attrs:
            names[attrs["data-node-id"]] = attrs.get("data-label-name", attrs["data-node-id"])
        elif tag == "line" and "data-node-from" in attrs:
            edges.append((attrs["data-node-from"], attrs.get("data-node-to")))
    return [(names.get(a, a), names.get(b, b)) for a, b in edges]


def decode_mask(el):
    rle = [int(v) for v in (el.get("rle") or "").replace(" ", "").split(",") if v]
    left, top, w, h = (int(float(el.get(k, 0))) for k in ("left", "top", "width", "height"))
    if w <= 0 or h <= 0:
        return None, (left, top)
    buf = bytearray(w * h)
    idx, on = 0, False
    for run in rle:
        if on:
            buf[idx:idx + run] = b"\xff" * run
        idx += run
        on = not on
    return Image.frombytes("L", (w, h), bytes(buf[: w * h])), (left, top)


def draw_shapes(img_path: Path, shapes: list, labels: dict, max_side: int):
    base = Image.open(img_path).convert("RGBA")
    W, H = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    lw = max(2, round(max(W, H) / 450))
    font = get_font(max(12, round(max(W, H) / 70)))
    captions = []

    for s in shapes:
        el, label = s["el"], s["label"]
        col = color_for(label, labels)
        solid, fill = col + (255,), col + (70,)
        dash = s["occluded"]
        anchor = None

        if s["type"] == "box":
            box = [float(el.get(k)) for k in ("xtl", "ytl", "xbr", "ybr")]
            d.rectangle(box, outline=solid, width=lw if not dash else max(1, lw // 2))
            anchor = (box[0], box[1])
        elif s["type"] == "polygon":
            p = pts(el.get("points"))
            if len(p) >= 3:
                d.polygon(p, fill=fill)
                d.line(p + [p[0]], fill=solid, width=lw, joint="curve")
                anchor = min(p, key=lambda q: (q[1], q[0]))
        elif s["type"] == "polyline":
            p = pts(el.get("points"))
            if len(p) >= 2:
                d.line(p, fill=solid, width=lw, joint="curve")
                anchor = p[0]
        elif s["type"] == "points":
            r = lw * 2
            for x, y in pts(el.get("points")):
                d.ellipse([x - r, y - r, x + r, y + r], fill=solid, outline=(255, 255, 255, 255))
                anchor = anchor or (x, y)
        elif s["type"] == "ellipse":
            cx, cy, rx, ry = (float(el.get(k)) for k in ("cx", "cy", "rx", "ry"))
            d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=solid, fill=fill, width=lw)
            anchor = (cx - rx, cy - ry)
        elif s["type"] == "mask":
            m, (left, top) = decode_mask(el)
            if m is not None:
                overlay.paste(Image.new("RGBA", m.size, col + (110,)), (left, top), m)
                anchor = (left, top)
        elif s["type"] == "cuboid":
            k = lambda n: (float(el.get(f"x{n}")), float(el.get(f"y{n}")))
            for face in ("1", "2"):
                tl, tr, br, bl = k(f"tl{face}"), k(f"tr{face}"), k(f"br{face}"), k(f"bl{face}")
                d.line([tl, tr, br, bl, tl], fill=solid, width=lw)
            for c in ("tl", "tr", "br", "bl"):
                d.line([k(f"{c}1"), k(f"{c}2")], fill=solid, width=max(1, lw // 2))
            anchor = k("tl1")
        elif s["type"] == "skeleton":
            kp = {}
            for child in el.findall("points"):
                if child.get("outside") == "1":
                    continue
                p = pts(child.get("points"))
                if p:
                    kp[child.get("label")] = (p[0], child.get("occluded") == "1")
            for a, b in skeleton_edges(labels.get(label, {}).get("svg", "")):
                if a in kp and b in kp:
                    d.line([kp[a][0], kp[b][0]], fill=solid, width=lw)
            r = lw * 2
            for (x, y), occ in kp.values():
                d.ellipse([x - r, y - r, x + r, y + r],
                          fill=(255, 255, 255, 255) if occ else solid,
                          outline=solid if occ else (255, 255, 255, 255), width=max(1, lw // 2))
            if kp:
                anchor = min((v[0] for v in kp.values()), key=lambda q: q[1])

        if anchor is not None:
            text = label + (f" #{s['track']}" if s["track"] is not None else "")
            captions.append((anchor, text, solid))

    for (x, y), text, solid in captions:
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
        y0 = max(0, y - th - 6)
        d.rectangle([x, y0, x + tw + 8, y0 + th + 6], fill=solid)
        d.text((x + 4 - l, y0 + 3 - t), text, fill=(255, 255, 255, 255), font=font)

    out = Image.alpha_composite(base, overlay).convert("RGB")
    out.thumbnail((max_side, max_side))
    return out


# --------------------------------------------------------------------------- statistics

def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"


def build_stats(data: dict) -> str:
    by_frame, tracks, tags = data["by_frame"], data["tracks"], data["tags"]
    all_shapes = [s for lst in by_frame.values() for s in lst]
    annotated = {f for f, lst in by_frame.items() if lst} | {t["frame"] for t in tags}

    lines = ["| Metric | Value |", "|---|---|"]
    if tracks:
        lines.append(f"| Frames in video | {fmt(data['size'])} |")
    lines.append(f"| Annotated {'frames' if tracks else 'images'} | {fmt(len(annotated))} |")
    if tracks:
        lines.append(f"| Tracks (unique objects) | {fmt(len(tracks))} |")
        visible = [s for t in tracks.values() for s in t["shapes"] if not s["outside"]]
        keys = [s for s in visible if s["keyframe"]]
        lines.append(f"| Visible shapes across all frames | {fmt(len(visible))} |")
        lines.append(f"| Manually placed keyframes | {fmt(len(keys))} "
                     f"({pct(len(keys), len(visible))} of shapes; the rest interpolated and reviewed) |")
    image_shapes = [s for s in all_shapes if s["track"] is None]
    if image_shapes:
        lines.append(f"| Objects | {fmt(len(image_shapes))} |")
        per = Counter(s["frame"] for s in image_shapes)
        lines.append(f"| Avg objects per annotated image | {fmt(len(image_shapes) / max(1, len(per)))} |")
        lines.append(f"| Max objects in one image | {fmt(max(per.values()))} |")
    if all_shapes:
        occ = sum(s["occluded"] for s in all_shapes)
        lines.append(f"| Marked occluded | {pct(occ, len(all_shapes))} |")
    if tags:
        lines.append(f"| Image-level tags | {fmt(len(tags))} |")

    # Skeleton keypoints
    kp = Counter()
    for s in all_shapes:
        if s["type"] == "skeleton":
            for child in s["el"].findall("points"):
                if child.get("outside") == "1":
                    kp["not visible / outside"] += 1
                elif child.get("occluded") == "1":
                    kp["occluded"] += 1
                else:
                    kp["visible"] += 1
    if kp:
        total = sum(kp.values())
        lines.append(f"| Keypoints labelled | {fmt(total)} (visible {pct(kp['visible'], total)}, "
                     f"occluded {pct(kp['occluded'], total)}, outside {pct(kp['not visible / outside'], total)}) |")

    # Per-label table
    lines += ["", "**Annotations by label**", ""]
    if tracks:
        per_label = defaultdict(lambda: {"tracks": 0, "len": 0, "keys": 0, "type": Counter()})
        for t in tracks.values():
            row = per_label[t["label"]]
            row["tracks"] += 1
            vis = [s for s in t["shapes"] if not s["outside"]]
            row["len"] += len(vis)
            row["keys"] += sum(s["keyframe"] for s in vis)
            row["type"].update(s["type"] for s in vis)
        lines += ["| Label | Shape | Tracks | Avg track length (frames) | Keyframes |", "|---|---|--:|--:|--:|"]
        for label, r in sorted(per_label.items(), key=lambda kv: -kv[1]["tracks"]):
            kind = ", ".join(TYPE_NAMES.get(k, k) for k in r["type"])
            lines.append(f"| {label} | {kind} | {fmt(r['tracks'])} | "
                         f"{fmt(r['len'] / max(1, r['tracks']))} | {fmt(r['keys'])} |")
    if image_shapes or tags:
        counts = defaultdict(Counter)
        for s in image_shapes:
            counts[s["label"]][s["type"]] += 1
        for t in tags:
            counts[t["label"]]["tag"] += 1
        types = [t for t in (*SHAPES, "tag") if any(c[t] for c in counts.values())]
        if tracks:
            lines += ["", "**Per-image annotations**", ""]
        lines.append("| Label | " + " | ".join(TYPE_NAMES[t] for t in types) + " | Total |")
        lines.append("|---|" + "--:|" * (len(types) + 1))
        for label, c in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
            lines.append(f"| {label} | " + " | ".join(fmt(c[t]) if c[t] else "" for t in types)
                         + f" | {fmt(sum(c.values()))} |")

    # Attributes
    attrs = Counter()
    for s in all_shapes:
        for k, v in s["attrs"].items():
            attrs[f"{k} = {v}"] += 1
    if attrs:
        lines += ["", "**Most used attributes**", "", "| Attribute | Count |", "|---|--:|"]
        for kv, n in attrs.most_common(12):
            lines.append(f"| {kv} | {fmt(n)} |")

    return "\n".join(lines)


# --------------------------------------------------------------------------- README update

def replace_block(text: str, key: str, body: str) -> str:
    start, end = f"<!-- {key}:START -->", f"<!-- {key}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    block = f"{start}\n{body}\n{end}"
    if pattern.search(text):
        return pattern.sub(lambda _: block, text)
    return text.rstrip() + "\n\n" + block + "\n"


def previews_markdown(gif: Path | None, samples: list[Path]) -> str:
    out = []
    if gif:
        out.append(f'<img src="previews/{gif.name}" width="100%" alt="Tracking preview">\n')
    for i in range(0, len(samples), 2):
        pair = samples[i:i + 2]
        out.append("<p>" + "".join(
            f'<img src="previews/{p.name}" width="49%" alt="Annotated sample"> ' for p in pair
        ).rstrip() + "</p>")
    return "\n".join(out) if out else "_No preview images could be generated. Export with 'Save images' ticked; see docs/ADDING_A_PROJECT.md._"


# --------------------------------------------------------------------------- main

def merge_exports(parts: list) -> dict:
    """Combine several parsed exports into one, renumbering frames and tracks."""
    if len(parts) == 1:
        return parts[0]
    out = {"title": parts[0]["title"], "labels": {}, "frames": {}, "by_frame": defaultdict(list),
           "tags": [], "tracks": {}, "size": 0}
    f_off = t_off = 0
    for d in parts:
        out["labels"].update({k: v for k, v in d["labels"].items() if k not in out["labels"]})
        for f, info in d["frames"].items():
            out["frames"][f + f_off] = info
        for f, lst in d["by_frame"].items():
            for s in lst:
                s["frame"] += f_off
                if s["track"] is not None:
                    s["track"] += t_off
            out["by_frame"][f + f_off].extend(lst)
        for t in d["tags"]:
            out["tags"].append({**t, "frame": t["frame"] + f_off})
        for tid, tr in d["tracks"].items():
            out["tracks"][tid + t_off] = tr
        out["size"] += d["size"]
        f_off += max([d["size"], *(f + 1 for f in d["by_frame"]), *(f + 1 for f in d["frames"])])
        t_off += max(d["tracks"], default=-1) + 1
    return out


def find_xmls(export_dir: Path) -> list:
    xmls = sorted(export_dir.rglob("*.xml"))
    if not xmls:
        for z in sorted(export_dir.glob("*.zip")):
            print(f"  unzipping {z.name}")
            with zipfile.ZipFile(z) as zf:
                for member in zf.namelist():
                    target = (export_dir / member).resolve()
                    if export_dir.resolve() not in target.parents and target != export_dir.resolve():
                        sys.exit(f"Refusing to extract unsafe path {member!r} from {z.name}")
                zf.extractall(export_dir)
        xmls = sorted(export_dir.rglob("*.xml"))
    return xmls


def scrub_personal_info(xml_path: Path):
    """Remove account details (owner/assignee usernames and emails, API URLs) before publishing."""
    text = xml_path.read_text(encoding="utf-8")
    clean = re.sub(r"<(owner|assignee)>.*?</\1>", r"<\1></\1>", text, flags=re.S)
    clean = re.sub(r"<url>.*?</url>", "<url></url>", clean, flags=re.S)
    clean = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[removed]", clean)
    if clean != text:
        xml_path.write_text(clean, encoding="utf-8")
        print("  removed account details (usernames/emails/URLs) from the export")


def process(project: Path, n_samples: int, make_gif: bool) -> bool:
    export_dir, prev_dir = project / "export", project / "previews"
    print(f"\n== {project.relative_to(REPO) if project.is_relative_to(REPO) else project}")
    xml_paths = find_xmls(export_dir) if export_dir.exists() else []
    if not xml_paths:
        print("  no CVAT export found in export/ (expected annotations.xml or a CVAT .zip) – skipped")
        return False

    for x in xml_paths:
        scrub_personal_info(x)
    data = merge_exports([parse_export(x) for x in xml_paths])
    resolve, n_files = locate_images(export_dir)
    names = ", ".join(x.relative_to(project).as_posix() for x in xml_paths)
    print(f"  {names}: {data['size']} frames/images, "
          f"{sum(len(v) for v in data['by_frame'].values())} shapes, {len(data['tracks'])} tracks, "
          f"{n_files} image files found")

    prev_dir.mkdir(exist_ok=True)
    for old in prev_dir.glob("sample_*.jpg"):
        old.unlink()

    candidates = [(f, lst) for f, lst in data["by_frame"].items() if lst and resolve(f, data["frames"])]
    if data["tracks"]:
        candidates.sort(key=lambda fl: fl[0])          # spread evenly through the video
        step = max(1, len(candidates) // max(1, n_samples))
        chosen = candidates[::step][:n_samples]
    else:
        candidates.sort(key=lambda fl: -len(fl[1]))    # busiest images show the most work
        chosen = candidates[:n_samples]

    samples = []
    for i, (fid, shapes) in enumerate(chosen, 1):
        out = prev_dir / f"sample_{i:02d}.jpg"
        draw_shapes(resolve(fid, data["frames"]), shapes, data["labels"], MAX_SIDE).save(out, quality=88)
        samples.append(out)
    print(f"  wrote {len(samples)} preview image(s)")
    if not samples:  # no source images: fall back to screenshots placed in previews/ by hand
        samples = sorted(p for p in prev_dir.iterdir()
                         if p.suffix.lower() in IMG_EXT and p.name != "tracking.gif")
        if samples:
            print(f"  using {len(samples)} existing image(s) in previews/")

    gif = None
    if make_gif and data["tracks"] and candidates:
        step = max(1, len(candidates) // GIF_FRAMES)
        frames = [draw_shapes(resolve(f, data["frames"]), lst, data["labels"], GIF_SIDE)
                  for f, lst in candidates[::step][:GIF_FRAMES]]
        gif = prev_dir / "tracking.gif"
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=150, loop=0, optimize=True)
        print(f"  wrote {gif.name} ({len(frames)} frames)")

    stats = build_stats(data)
    (project / "STATS.md").write_text(
        f"# Annotation statistics\n\nGenerated from {', '.join(f'`{x.relative_to(project).as_posix()}`' for x in xml_paths)} "
        f"by `tools/cvat_showcase.py`.\n\n{stats}\n", encoding="utf-8")

    readme = project / "README.md"
    text = readme.read_text(encoding="utf-8") if readme.exists() else f"# {project.name}\n"
    text = replace_block(text, "STATS", stats)
    text = replace_block(text, "PREVIEWS", previews_markdown(gif, samples))
    readme.write_text(text, encoding="utf-8")
    print("  updated README.md and STATS.md")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*", type=Path, help="project folder(s) containing export/")
    ap.add_argument("--all", action="store_true", help="process every folder in projects/")
    ap.add_argument("--samples", type=int, default=6, help="number of preview images (default 6)")
    ap.add_argument("--no-gif", action="store_true", help="skip the tracking GIF")
    args = ap.parse_args()

    targets = sorted(p for p in (REPO / "projects").iterdir() if p.is_dir()) if args.all else args.projects
    if not targets:
        ap.error("give a project folder or use --all")
    done = sum(process(p.resolve(), args.samples, not args.no_gif) for p in targets)
    print(f"\nProcessed {done} of {len(targets)} project(s).")


if __name__ == "__main__":
    main()
