"""Instrument Pixel Camera 11.0's ImageReader factory without changing settings.

Luzq.toString() logs width, height, image format, maxImages, usage,
hardware-buffer format and dataspace, in that order. Assemble the resulting
smali tree and replace its owning DEX in a separately signed diagnostic APK.
"""

import argparse
from pathlib import Path
import re


LOG_BLOCK = """    const-string v0, "GCamImageReaderConfig"

    invoke-virtual {p1}, Luzq;->toString()Ljava/lang/String;

    move-result-object v1

    invoke-static {v0, v1}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I

"""


def patch_smali(text: str) -> str:
    if not re.search(r'^\.class public final Luvx;$', text, re.MULTILINE):
        raise ValueError('Expected Pixel Camera 11.0 ImageReader factory Luvx;')
    if 'GCamImageReaderConfig' in text:
        raise ValueError('ImageReader diagnostics are already present')
    anchor = ('.method public final a(Luzq;)Luzt;\n'
              '    .registers 8\n\n'
              '    iget v0, p0, Luvx;->b:I')
    if text.count(anchor) != 1:
        raise ValueError('ImageReader method/register layout changed')
    # v0 and v1 are scratch locals. The original paths overwrite both before
    # reading them; parameter registers p0/p1 and configuration fields stay intact.
    prefix, instruction = anchor.rsplit('    iget', 1)
    return text.replace(anchor, prefix + LOG_BLOCK + '    iget' + instruction, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    patched = patch_smali(args.input.read_text(encoding='utf-8'))
    args.output.write_text(patched, encoding='utf-8')


if __name__ == '__main__':
    main()
