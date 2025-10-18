#!/usr/bin/env python3
"""
png2svg.py — Batch convert PNGs to SVGs (raster -> vector) using potrace/autotrace.

This debug-friendly version prints diagnostic info, shows tracebacks on error,
and can optionally keep PBM files (--keep-pbm) and run on a single file (--single).

Usage examples:
  # batch convert with debug output
  python png2svg.py -i ./pngs -o ./svgs --debug

  # convert a single file and keep PBM for inspection
  python png2svg.py --single ./pngs/example.png -o ./svgs --keep-pbm --debug

Requirements:
  - Python 3.7+
  - Pillow (pip install pillow)
  - potrace or autotrace on PATH
"""
from __future__ import annotations
import argparse
import os
import sys
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Optional
from PIL import Image, ImageOps

VERSION = "1.3-debug"

def dbg_print(msg: str, debug: bool):
    # Print and flush so CI/remote terminals show in real time
    if debug:
        print(msg, flush=True)

def find_vectorizer(debug: bool = False) -> Optional[str]:
    potrace_path = shutil.which("potrace")
    autotrace_path = shutil.which("autotrace")
    dbg_print(f"potrace path: {potrace_path}", debug)
    dbg_print(f"autotrace path: {autotrace_path}", debug)
    if potrace_path:
        return "potrace"
    if autotrace_path:
        return "autotrace"
    return None

def _write_pbm_ascii(img_mode1: Image.Image, path: str) -> None:
    img = img_mode1.convert("L")
    w, h = img.size
    pixels = list(img.getdata())
    with open(path, "w", encoding="ascii") as f:
        f.write(f"P1\n{w} {h}\n")
        for y in range(h):
            row = pixels[y * w:(y + 1) * w]
            f.write(" ".join("1" if p > 0 else "0" for p in row))
            f.write("\n")

def convert_to_pbm_bitmap(img_path: str, threshold: int = 128, use_alpha: bool = False, invert: bool = False, debug: bool = False) -> str:
    dbg_print(f"Loading image: {img_path}", debug)
    im = Image.open(img_path)
    im = im.convert("RGBA")
    has_alpha = ("A" in im.getbands())
    dbg_print(f"Image size: {im.size}, bands: {im.getbands()}, has_alpha={has_alpha}", debug)

    if use_alpha and has_alpha:
        alpha = im.getchannel("A")
        bw = alpha.point(lambda p: 255 if p > 0 else 0).convert("1")
    else:
        gray = im.convert("L")
        bw = gray.point(lambda p: 255 if p > threshold else 0).convert("1")

    if invert:
        bw = ImageOps.invert(bw.convert("L")).convert("1")

    fd, tmp_path = tempfile.mkstemp(suffix=".pbm")
    os.close(fd)
    dbg_print(f"Writing PBM to: {tmp_path}", debug)

    try:
        # try letting Pillow write (some builds support PBM)
        bw.save(tmp_path)
        dbg_print("Saved PBM via Pillow", debug)
    except (KeyError, OSError) as e:
        dbg_print(f"Pillow PBM save failed ({e}), falling back to ASCII PBM writer", debug)
        try:
            _write_pbm_ascii(bw, tmp_path)
            dbg_print("Saved PBM via ASCII fallback", debug)
        except Exception as ee:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise

    return tmp_path

def run_potrace(pbm_path: str, out_svg_path: str, extra_opts: Optional[str], debug: bool):
    cmd = ["potrace", "-s", "-o", out_svg_path, pbm_path]
    if extra_opts:
        cmd += extra_opts.split()
    dbg_print(f"Running potrace: {' '.join(cmd)}", debug)
    subprocess.run(cmd, check=True)

def run_autotrace(raster_path: str, out_svg_path: str, extra_opts: Optional[str], debug: bool):
    cmd = ["autotrace", raster_path, "-output-file", out_svg_path, "-output-format", "svg"]
    if extra_opts:
        cmd += extra_opts.split()
    dbg_print(f"Running autotrace: {' '.join(cmd)}", debug)
    subprocess.run(cmd, check=True)

def convert_file(src_path: str, dst_path: str, vectorizer: str, threshold: int, use_alpha: bool, invert: bool, autotrace_opts: Optional[str], keep_pbm: bool, debug: bool):
    dst_dir = os.path.dirname(dst_path)
    os.makedirs(dst_dir, exist_ok=True)
    dbg_print(f"Converting {src_path} -> {dst_path} using {vectorizer}", debug)

    if vectorizer == "potrace":
        pbm = None
        try:
            pbm = convert_to_pbm_bitmap(src_path, threshold=threshold, use_alpha=use_alpha, invert=invert, debug=debug)
            run_potrace(pbm, dst_path, extra_opts=None, debug=debug)
        finally:
            if pbm and os.path.exists(pbm):
                if keep_pbm:
                    dbg_print(f"Keeping PBM: {pbm}", debug)
                else:
                    try:
                        os.remove(pbm)
                        dbg_print(f"Removed PBM: {pbm}", debug)
                    except Exception:
                        dbg_print(f"Failed to remove PBM: {pbm}", debug)
    elif vectorizer == "autotrace":
        run_autotrace(src_path, dst_path, extra_opts=autotrace_opts, debug=debug)
    else:
        raise RuntimeError("No supported vectorizer available.")

def batch_convert(input_dir: str, output_dir: str, threshold: int, use_alpha: bool, invert: bool, autotrace_opts: Optional[str], keep_pbm: bool, single: Optional[str], debug: bool) -> int:
    vectorizer = find_vectorizer(debug=debug)
    if not vectorizer:
        print("ERROR: No vectorizer found. Install 'potrace' or 'autotrace' and ensure it's on PATH.", file=sys.stderr)
        return 0

    if single:
        files = [Path(single)]
    else:
        patterns = ["*.png", "*.PNG"]
        files = []
        for patt in patterns:
            files.extend(sorted(Path(input_dir).glob(patt)))

    dbg_print(f"Found {len(files)} PNG file(s) in '{input_dir}' (single={single is not None})", debug)
    if len(files) == 0:
        print("No PNG files found to convert.", flush=True)
        return 0

    converted = 0
    for idx, fp in enumerate(files, start=1):
        src = str(fp)
        base = fp.stem
        dst = os.path.join(output_dir, f"{base}.svg")
        print(f"[{idx}/{len(files)}] {fp.name} -> {os.path.relpath(dst)} using {vectorizer}", flush=True)
        try:
            convert_file(src, dst, vectorizer, threshold, use_alpha, invert, autotrace_opts, keep_pbm, debug)
            converted += 1
        except subprocess.CalledProcessError as e:
            print(f"ERROR: vectorizer failed for {fp.name}: {e}", file=sys.stderr)
            if debug:
                traceback.print_exc()
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            if debug:
                traceback.print_exc()

    return converted

def parse_args():
    p = argparse.ArgumentParser(description="Batch convert PNGs in a folder to SVGs (vectorize).")
    p.add_argument("-i", "--input-dir", default=".", help="Folder containing PNG files (default: current dir).")
    p.add_argument("-o", "--output-dir", default="./svgs", help="Folder to write SVG files (default: ./svgs).")
    p.add_argument("--threshold", type=int, default=128, help="Luminance threshold for binarization (0-255). Default: 128.")
    p.add_argument("--use-alpha", action="store_true", help="Use PNG alpha channel as mask (foreground where alpha>0).")
    p.add_argument("--invert", action="store_true", help="Invert bitmap before vectorizing (swap foreground/background).")
    p.add_argument("--autotrace-opts", default="", help="Extra options to pass to autotrace (only used if autotrace is selected).")
    p.add_argument("--keep-pbm", action="store_true", help="Do not delete temporary PBM files (helpful for debugging).")
    p.add_argument("--single", help="Convert a single PNG file (path).")
    p.add_argument("--debug", action="store_true", help="Enable debug output.")
    p.add_argument("--version", action="version", version=VERSION)
    return p.parse_args()

def main():
    args = parse_args()
    try:
        count = batch_convert(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            threshold=args.threshold,
            use_alpha=args.use_alpha,
            invert=args.invert,
            autotrace_opts=args.autotrace_opts,
            keep_pbm=args.keep_pbm,
            single=args.single,
            debug=args.debug,
        )
    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        sys.exit(1)

    if count == 0:
        print("Finished: 0 files converted.")
    else:
        print(f"Done — converted {count} file(s). SVGs written to: {args.output_dir}")

if __name__ == "__main__":
    main()
