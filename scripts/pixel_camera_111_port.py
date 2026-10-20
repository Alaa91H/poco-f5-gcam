"""Reviewed 11.1 DEX roles, selected only by the verified native input profile.

Latest upstream resolution remains independent. Unknown native/DEX layouts fail
closed; this module never downloads an older version to make a build pass.
"""
from pathlib import Path
import hashlib
import os
import re
import tempfile
import zipfile

try:
    from scripts import patch_pixel_camera_device_gate as p
    from scripts import patch_pixel_camera_enumeration as enumeration
    from scripts import patch_pixel_camera_manual_controls as manual_controls
    from scripts.patch_pixel_camera_raw_session import patch_raw_session_smali
except ModuleNotFoundError:
    import patch_pixel_camera_device_gate as p
    import patch_pixel_camera_enumeration as enumeration
    import patch_pixel_camera_manual_controls as manual_controls
    from patch_pixel_camera_raw_session import patch_raw_session_smali


def require(condition, message):
    if not condition:
        raise p.PatchError(message)


# Exact 11.1.040 native wrapper reviewed against the original upstream library.
# The extension is optional on marble; when absent, return the OpenCL error that
# selects GCam's existing one-base-frame SpatialMerge fallback.
OPENCL_111_SOURCE_SHA256 = '142f699b735372c2b7dbf6f950bf9163c5cb5d9ccf9903592ca28673c2c6f434'
OPENCL_111_OFFSET = 0x6EABE80
OPENCL_111_ORIGINAL = bytes.fromhex(
    'ff8301d1fe6f01a9fa6702a9f85f03a9f65704a9f44f05a9fb3340f9fa0300aa'
    'e0230091f30307aaf40306aaf503052af60304aaf70303aaf80302aaf90301aa'
    '66452594a8e90290e0031aaae10319aa08b147f9e20318aae30317aae40316aa'
    'e503152ae60314aae70313aafb0300f900013fd6f303002ae02300915f452594'
    'e003132af44f45a9f65744a9f85f43a9fa6742a9fe6f41a9ff830191c0035fd6'
)
OPENCL_111_REPLACEMENT = bytes.fromhex(
    'a8e9029008b147f9680000b540078012c0035fd6ffc301d1fe0700f9e00702a9'
    'e20f03a9e41704a9e61f05a9e93b40f9e90300f9e083019168452594e00742a9'
    'e20f43a9e41744a9e61f45a9a8e9029008b147f900013fd6e06b00b9e0830191'
    '66452594e06b40b9fe0740f9ffc30191c0035fd6'
)


def patch_opencl_merge_fallback(data):
    require(hashlib.sha256(data).hexdigest() == OPENCL_111_SOURCE_SHA256,
            '11.1 OpenCL fallback source library SHA-256 changed')
    end = OPENCL_111_OFFSET + len(OPENCL_111_ORIGINAL)
    require(data[OPENCL_111_OFFSET:end] == OPENCL_111_ORIGINAL,
            '11.1 OpenCL fallback wrapper instructions changed')
    require(0 < len(OPENCL_111_REPLACEMENT) <= len(OPENCL_111_ORIGINAL),
            '11.1 OpenCL fallback replacement exceeds reviewed wrapper')
    # The reviewed replacement is a shorter wrapper. Preserve the original
    # instructions after its return byte-for-byte, as in the validated review.
    patched = (data[:OPENCL_111_OFFSET] + OPENCL_111_REPLACEMENT
               + data[OPENCL_111_OFFSET + len(OPENCL_111_REPLACEMENT):])
    return (patched,
            {'behavior': 'return CL_INVALID_OPERATION when optional OpenCL entry is absent',
             'offset': hex(OPENCL_111_OFFSET), 'original_wrapper_bytes_verified': len(OPENCL_111_ORIGINAL),
             'replacement_bytes': len(OPENCL_111_REPLACEMENT), 'library_size_preserved': True,
             'multi_frame_spatial_merge': False, 'runtime': 'NOT TESTED'})


def normalize_patch(text, aliases, patch):
    for actual, canonical in aliases:
        require(canonical not in text, f'Canonical descriptor collision: {canonical}')
        text = text.replace(actual, canonical)
    text, report = patch(text)
    for actual, canonical in reversed(aliases):
        text = text.replace(canonical, actual)
    return text, report


def patch_optional(text):
    return normalize_patch(text, [('Lopn;', 'Lofe;'), ('Ltpg;', 'Ltdn;')],
                           p.patch_onecamera_optional_key_smali_text)


def patch_video_mode_fallback(text):
    """Avoid crashing when a selected high-speed mode has no compatible size."""
    require(re.search(r'^\.class\s+[^\n]*\s+Ljbw;$', text, re.M) is not None,
            '11.1 video-mode configuration owner changed')
    require('.method public final a()Ljfz;' in text,
            '11.1 video-mode configuration method changed')
    marker = 'poco_safe_fps_fallback_'
    require(marker not in text, 'video-mode fallback already present')
    gap = r'\n(?:[ \t]*\n)*'
    pattern = re.compile(
        r'    invoke-interface \{v4\}, Lj\$/util/stream/Stream;->findFirst\(\)Lj\$/util/Optional;'
        + gap + r'    move-result-object v4'
        + gap + r'    invoke-virtual \{v4\}, Lj\$/util/Optional;->get\(\)Ljava/lang/Object;'
        + gap + r'    move-result-object v4'
        + gap + r'    check-cast v4, Lusx;'
        + gap + r'    goto :goto_24b')
    matches = list(pattern.finditer(text))
    require(len(matches) == 2,
            f'expected two reviewed video-resolution selectors, found {len(matches)}')
    for index, match in reversed(list(enumerate(matches, start=1))):
        replacement = (
            '    invoke-interface {v4}, Lj$/util/stream/Stream;->findFirst()Lj$/util/Optional;\n'
            '    move-result-object v4\n'
            '    invoke-virtual {v4}, Lj$/util/Optional;->isPresent()Z\n'
            '    move-result v5\n'
            f'    if-eqz v5, :poco_safe_fps_fallback_{index}\n'
            '    invoke-virtual {v4}, Lj$/util/Optional;->get()Ljava/lang/Object;\n'
            '    move-result-object v4\n'
            '    check-cast v4, Lusx;\n'
            '    goto :goto_24b\n'
            f'    :poco_safe_fps_fallback_{index}\n'
            '    sget-object v3, Lusv;->c:Lusv;\n'
            '    sget-object v4, Lusx;->i:Lusx;\n'
            '    goto :goto_24b')
        text = text[:match.start()] + replacement + text[match.end():]
    return text, {'empty_filter_fallback': '30fps_1080p',
                  'selectors_guarded': len(matches),
                  'scope': 'only empty filtered video-resolution options'}


def patch_missing_key(text):
    key = '    sget-object v0, Ltpb;->a:Landroid/hardware/camera2/CaptureRequest$Key;'
    require(text.count(key) == 1, '11.1 optional request key moved')
    start = text.index(key)
    end = text.index('    return-object v0', start)
    value = 'invoke-static {v5}, Ljava/lang/Integer;->valueOf(I)Ljava/lang/Integer;'
    block = text[start:end]
    require(block.count(value) == 1, '11.1 optional request value register changed')
    # Normalize the shape check only; restore the actual v5 data flow afterward.
    block = block.replace(value, value.replace('{v5}', '{v4}'))
    normalized = text[:start] + block + text[end:]
    aliases = [('Lnbw;', 'Lmta;'), ('Ltpb;', 'Ltdi;'), ('Lvaz;', 'Lupd;'),
               ('Lyvd;', 'Lyjh;'), ('Lyuq;', 'Lyiu;')]
    patched, report = normalize_patch(normalized, aliases, p.patch_onecamera_missing_request_key_smali_text)
    start = patched.index(key)
    end = patched.index('    return-object v0', start)
    block = patched[start:end].replace(value.replace('{v5}', '{v4}'), value)
    return patched[:start] + block + patched[end:], report


def patch_odr(text):
    return normalize_patch(text, [('Lptg;', 'Lodr;'), ('Ltpg;', 'Ltdn;'),
                                 ('Lvaz;', 'Lupd;'), ('Lyrh;', 'Lyfm;'), ('Lvae;', 'Luoi;')],
                           p.patch_onecamera_odr_missing_request_key_smali_text)


def patch_qll(text):
    key = '    sget-object v1, Ltpg;->a:Landroid/hardware/camera2/CaptureRequest$Key;'
    require(text.count(key) == 1, '11.1 qll AWB case moved')
    start = text.rfind('    :pswitch_', 0, text.index(key))
    end = text.index('    return-void', text.index(key)) + len('    return-void')
    require(start >= 0, '11.1 qll AWB case has no switch label')
    block = text[start:end]
    wrapper = '.class public final Lptg;\n.super Ljava/lang/Object;\n.method public final a(Ljava/lang/Object;)V\n    .registers 4\n'
    patched, report = patch_odr(wrapper + block + '\n.end method\n')
    patched = patched[patched.index('    :pswitch_'):patched.rfind('\n.end method')]
    return text[:start] + patched + text[end:], report


def patch_idy(text):
    first = '    sget-object v1, Ltpg;->a:Landroid/hardware/camera2/CaptureRequest$Key;'
    second = '    sget-object v0, Ltpg;->b:Landroid/hardware/camera2/CaptureRequest$Key;'
    require(text.count(first) == text.count(second) == 1, '11.1 idy AWB keys moved')
    start = text.index(first)
    end = text.index('    return-void', text.index(second))
    block = text[start:end]
    call = '    invoke-static {v0, p1, p0}, Ljhq;->e(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;Lvae;)V'
    require(block.count(call) == 1, '11.1 idy AWB apply call changed')
    block = block.replace(first, first + '\n    if-eqz v1, :poco_manual_awb_done')
    block = block.replace(second, second + '\n    if-eqz v0, :poco_manual_awb_done')
    block = block.replace(call, call + '\n    :poco_manual_awb_done')
    return text[:start] + block + text[end:], {'behavior': 'skip absent AWB keys locally, preserving first applied value'}


def patch_lookahead(text):
    key = '    sget-object p0, Ltpg;->g:Landroid/hardware/camera2/CaptureRequest$Key;'
    require(text.count(key) == 1 and 'invoke-static {}, Lrdb;->n()Lpfy;' in text,
            '11.1 lookahead empty-set factory changed')
    require('Lvaz;-><init>(Landroid/hardware/camera2/CaptureRequest$Key;Ljava/lang/Object;)V' in text,
            '11.1 lookahead request-pair constructor changed')
    block = '\n    if-nez p0, :poco_lookahead_present\n    invoke-static {}, Lrdb;->n()Lpfy;\n    move-result-object p0\n    return-object p0\n    :poco_lookahead_present\n'
    return text.replace(key, key + block), {'behavior': 'existing empty request set for absent Ltpg.g'}


def patch_yuv_usage(text):
    anchor = '    invoke-virtual {v1, v2, v3}, Landroid/media/ImageReader$Builder;->setUsage(J)Landroid/media/ImageReader$Builder;'
    require(text.count(anchor) == 1 and '.method public final a(Lvlo;)Lvlr;' in text
            and 'iget-wide v2, p1, Lvlo;->e:J' in text, '11.1 ImageReader factory changed')
    block = '    iget p0, p1, Lvlo;->c:I\n    const/16 v4, 0x23\n    if-ne p0, v4, :poco_usage_original\n    const-wide/16 v4, 0x103\n    cmp-long p0, v2, v4\n    if-nez p0, :poco_usage_original\n    const-wide/16 v2, 0x3\n    :poco_usage_original\n'
    return text.replace(anchor, block + anchor), {'behavior': 'YUV35 usage259 only to3'}


def patch_apk(source, output, *, baksmali, smali, java='java', feature_profile=None):
    if feature_profile not in (None, manual_controls.FEATURE_PROFILE):
        raise p.PatchError(f'Unsupported 11.1 feature profile: {feature_profile}')
    with zipfile.ZipFile(source) as archive:
        native = archive.read(p.GCASTARTUP_LIBRARY)
    profile = p.native_patch_profile(native)
    require(profile['name'].startswith('11.1'), 'Wrong input profile for 11.1 DEX roles')
    groups = {
        'classes.dex': ['msr', 'lrm', 'vgr', 'vhw', 'tk', 'ldg', 'ekw', 'opn', 'nbw', 'ptg', 'sb'],
        'classes2.dex': ['ksf', 'hgn', 'rhe', 'vha', 'ojf', 'qll', 'idy', 'jbw'],
    }
    receiver = p.KEEPALIVE_RECEIVER_DESCRIPTOR
    receiver_dex = p.find_keepalive_receiver_dex(source)
    groups.setdefault(receiver_dex, []).append(receiver[1:-1])
    report = {
        'profile': profile['name'],
        'feature_profile': feature_profile,
        'dex_roles': {},
        'native': {},
    }
    manual_meta = None
    with tempfile.TemporaryDirectory(prefix='pixel-camera-111-') as temp:
        root = Path(temp)
        replacements = {}
        merger_source = Path(__file__).resolve().parents[1] / 'tools/MergeCompatibilityDex.java'
        p.run(['javac', '-cp', os.fspath(smali), '-d', os.fspath(root), os.fspath(merger_source)])
        for dex_name, names in groups.items():
            original = root / dex_name
            with zipfile.ZipFile(source) as archive:
                original.write_bytes(archive.read(dex_name))
            scope = root / (dex_name + '-scope')
            scope.mkdir()
            descriptors = ','.join('L' + name + ';' for name in names)
            p.run([java, '-jar', os.fspath(baksmali), 'd', os.fspath(original), '--classes', descriptors, '-o', os.fspath(scope)])
            changed = []
            def save(name, patched, metadata):
                path = scope / (name + '.smali')
                path.write_text(patched, encoding='utf-8')
                changed.append('L' + name + ';')
                report['dex_roles'][name] = metadata
            def read(name):
                return (scope / (name + '.smali')).read_text(encoding='utf-8')
            for name in names:
                if name == receiver[1:-1]:
                    patched, meta = p.patch_keepalive_receiver_smali_text(read(name))
                elif name == 'msr':
                    patched, meta = p.patch_gcam_init_smali_text(read(name), metadata_interface_text=read('vgr'))
                elif name == 'vgr':
                    # Interface needed for shape validation, not a patch target.
                    (scope / 'vgr.smali').unlink()
                    continue
                elif name == 'lrm': patched, meta = p.patch_motion_stabilizer_startup_smali_text(read(name))
                elif name == 'opn': patched, meta = patch_optional(read(name))
                elif name == 'nbw': patched, meta = patch_missing_key(read(name))
                elif name == 'ptg': patched, meta = patch_odr(read(name))
                elif name == 'qll': patched, meta = patch_qll(read(name))
                elif name == 'idy': patched, meta = patch_idy(read(name))
                elif name == 'ojf': patched, meta = patch_lookahead(read(name))
                elif name == 'vhw': patched, meta = patch_yuv_usage(read(name))
                elif name == 'sb': patched, meta = patch_raw_session_smali(read(name))
                elif name == 'jbw': patched, meta = patch_video_mode_fallback(read(name))
                elif name == 'ksf':
                    patched, gate = p.patch_smali_text(read(name))
                    patched, flags = p.patch_nontensor_flag_queries(patched)
                    meta = {'device_gate': gate, 'flags': flags}
                    if feature_profile == manual_controls.FEATURE_PROFILE:
                        require(
                            flags.get('key_type') == 'Lkps;',
                            'manual-controls feature key type changed',
                        )
                        require(
                            flags.get('name_field')
                            == 'Lkpq;->a:Ljava/lang/String;',
                            'manual-controls feature name field changed',
                        )
                        patched, manual_meta = manual_controls.patch_smali(patched)
                        meta['manual_controls'] = manual_meta
                else:
                    text = read(name)
                    count = len(re.findall(r'invoke-virtual \{[vp]\d+\}, Landroid/hardware/camera2/CameraManager;->getCameraIdList', text))
                    require(count > 0, f'Camera enumeration owner changed: {name}')
                    patched = enumeration.patch_smali(text, 'L' + name + ';', count)
                    meta = {'enumeration_calls': count}
                save(name, patched, meta)
            if dex_name == 'classes.dex':
                (scope / 'CameraIds.smali').write_text(enumeration.HELPER, encoding='utf-8')
                changed.append('Lpoco/CameraIds;')
            if (
                feature_profile == manual_controls.FEATURE_PROFILE
                and dex_name == 'classes2.dex'
            ):
                require(
                    manual_meta is not None,
                    'manual-controls feature owner is not in classes2.dex',
                )
                (scope / 'ManualFeatures.smali').write_text(
                    manual_controls.HELPER, encoding='utf-8'
                )
                changed.append('Lpoco/ManualFeatures;')
            patch_dex = root / (dex_name + '-patch.dex')
            final = root / (dex_name + '-final.dex')
            p.run([java, '-jar', os.fspath(smali), 'a', os.fspath(scope), '-o', os.fspath(patch_dex)])
            p.run([java, '-cp', os.fspath(root) + os.pathsep + os.fspath(smali),
                   'MergeCompatibilityDex', os.fspath(original), os.fspath(patch_dex), os.fspath(final), ','.join(changed)])
            replacements[dex_name] = final
        report['native']['tuning'] = p._verify_uncalibrated_tuning_branch(native)
        report['native']['aion'] = p._verify_aion_nonfatal_branch(native)
        native, report['native']['opencl_merge_fallback'] = patch_opencl_merge_fallback(native)
        native, report['native']['patches'] = p.patch_native_bytes(native, patches=profile['patches'])
        lib = root / 'libgcastartup.so'
        lib.write_bytes(native)
        replacements[p.GCASTARTUP_LIBRARY] = lib
        p.replace_zip_members(source, output, replacements)
    if feature_profile == manual_controls.FEATURE_PROFILE:
        require(manual_meta is not None, 'manual-controls feature patch did not run')
        report['manual_controls'] = manual_meta
    return report
