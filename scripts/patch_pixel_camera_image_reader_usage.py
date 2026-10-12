"""Experimental session-configuration fallback for Pixel Camera 11.0 on POCO F5.

The exact 800x600 PRIVATE/RAW10/YUV graph fails with YUV usage 259 and
configures successfully with YUV usage 3, keeping all other settings unchanged.
The factory retains RAW, PRIVATE, hardware-buffer format and dataspace settings.
This resolves the observed configuration rejection only. It does not establish
working Pixel Camera preview, photo processing or video recording.
"""

import argparse
from pathlib import Path
import re


GUARD = """    # POCO F5: only YUV_420_888 with CPU_READ_OFTEN | GPU_SAMPLED_IMAGE.
    iget v0, p1, Luzq;->c:I

    const/16 v4, 0x23

    if-ne v0, v4, :poco_yuv_usage_done

    const-wide/16 v4, 0x103

    cmp-long v0, v2, v4

    if-nez v0, :poco_yuv_usage_done

    const-wide/16 v2, 0x3

    :poco_yuv_usage_done
"""


def patch_smali(text: str) -> str:
    if not re.search(r'^\.class public final Luvx;$', text, re.MULTILINE):
        raise ValueError('Expected Pixel Camera 11.0 ImageReader factory Luvx;')
    if ':poco_yuv_usage_done' in text:
        raise ValueError('YUV consumer usage fallback already applied')
    method = re.search(r'^\.method public final a\(Luzq;\)Luzt;\n'
                       r'    \.registers 8\n(?P<body>.*?)^\.end method$',
                       text, re.MULTILINE | re.DOTALL)
    if not method:
        raise ValueError('ImageReader method/register layout changed')
    anchor = ('    iget-wide v2, p1, Luzq;->e:J\n\n'
              '    const-wide/16 v4, -0x1\n\n'
              '    cmp-long v0, v2, v4')
    if method.group('body').count(anchor) != 1:
        raise ValueError('ImageReader usage lookup/default comparison changed')
    # v0 and v4/v5 are overwritten by the original comparison immediately
    # afterwards. v1 must keep its -1 sentinel for hardware-buffer format.
    location = text.index(anchor, method.start(), method.end())
    insertion = location + len('    iget-wide v2, p1, Luzq;->e:J\n\n')
    return text[:insertion] + GUARD + text[insertion:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(patch_smali(args.input.read_text(encoding='utf-8')), encoding='utf-8')


if __name__ == '__main__':
    main()
