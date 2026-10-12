"""Experimental, exact-version OpenCL null guard for the tested POCO APK.

The absent clGetImageRequirementsInfoEXT returns CL_INVALID_OPERATION (-59).
GCam then uses its existing base-frame fallback. This does not repair RAW
preview, implement the extension, or provide multi-frame SpatialMerge.
The rewritten wrapper retains nine arguments and leak-scope enter/leave calls.
Original unwind metadata is not regenerated: native stack unwinding through
this experimental wrapper is not validated. Keep this out of release builds.
"""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

try:
    from .patch_pixel_camera_device_gate import replace_zip_members
except ImportError:
    from patch_pixel_camera_device_gate import replace_zip_members


MEMBER = 'lib/arm64-v8a/libgcastartup.so'
SOURCE_SHA256 = '6a33754d1bb754cd8e256fa278777022d75e13ee521d2e1dbe14557a9101736f'
OFFSET = 0x6E08E44
ORIGINAL = bytes.fromhex(
    'ff8301d1fe6f01a9fa6702a9f85f03a9f65704a9f44f05a9fb3340f9fa0300aa'
    'e0230091f30307aaf40306aaf503052af60304aaf70303aaf80302aaf90301aa'
    'b8d62394e88c01f0e0031aaae10319aa08c544f9e20318aae30317aae40316aa'
    'e503152ae60314aae70313aafb0300f900013fd6'
)
REPLACEMENT = bytes.fromhex(
    'e88c01f008c544f9680000b540078012c0035fd6ffc301d1fe0700f9e00702a9'
    'e20f03a9e41704a9e61f05a9e93b40f9e90300f9e0830191bad62394e00742a9'
    'e20f43a9e41744a9e61f45a9e88c01f008c544f900013fd6e06b00b9e0830191'
    'b8d62394e06b40b9fe0740f9ffc30191c0035fd6'
)


def patch_library(data: bytes) -> bytes:
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError('Source library SHA-256 differs from the tested version')
    if data[OFFSET:OFFSET + len(ORIGINAL)] != ORIGINAL:
        raise ValueError('OpenCL wrapper instructions differ from the tested version')
    if len(ORIGINAL) != len(REPLACEMENT):
        raise ValueError('Replacement must preserve the library layout')
    return data[:OFFSET] + REPLACEMENT + data[OFFSET + len(ORIGINAL):]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, help='New unsigned APK; align and sign separately')
    args = parser.parse_args()
    if args.output.exists() or args.source.resolve() == args.output.resolve():
        parser.error('Output must be a new file distinct from the source')
    with zipfile.ZipFile(args.source) as archive:
        if any('poco_fault_trace' in name for name in archive.namelist()):
            parser.error('Use a clean source APK without the fault recorder')
        data = patch_library(archive.read(MEMBER))
    with tempfile.TemporaryDirectory(dir=args.output.resolve().parent) as directory:
        library = Path(directory) / 'libgcastartup.so'
        library.write_bytes(data)
        replace_zip_members(args.source, args.output, {MEMBER: library})
    print(json.dumps({'output': str(args.output), 'signed': False,
                      'library_sha256': hashlib.sha256(data).hexdigest(),
                      'runtime': 'NOT TESTED for this output'}))


if __name__ == '__main__':
    main()
