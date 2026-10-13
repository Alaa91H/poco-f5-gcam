"""Scoped Qualcomm RAW session compatibility patch for marble on API37+.

Keeps standard non-RAW sessions, custom/high-speed sessions, and request targets
unchanged. Refuses unknown caller data flow rather than guessing register roles.
"""
import argparse
import re
from pathlib import Path

class PatchError(RuntimeError):
    pass

HELPER = '''
.method private static pocoRawSessionType(ILjava/lang/String;Ljava/util/List;)I
    .registers 9
    if-nez p0, :poco_raw_keep
    sget v0, Landroid/os/Build$VERSION;->SDK_INT:I
    const/16 v1, 0x25
    if-lt v0, v1, :poco_raw_keep
    const-string v0, "marble"
    sget-object v1, Landroid/os/Build;->DEVICE:Ljava/lang/String;
    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :poco_raw_keep
    const-string v0, "0"
    invoke-virtual {v0, p1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :poco_raw_inspect
    const-string v0, "1"
    invoke-virtual {v0, p1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :poco_raw_keep
    :poco_raw_inspect
    invoke-interface {p2}, Ljava/util/List;->iterator()Ljava/util/Iterator;
    move-result-object v0
    :poco_raw_loop
    invoke-interface {v0}, Ljava/util/Iterator;->hasNext()Z
    move-result v1
    if-eqz v1, :poco_raw_keep
    invoke-interface {v0}, Ljava/util/Iterator;->next()Ljava/lang/Object;
    move-result-object v1
    check-cast v1, Landroid/hardware/camera2/params/OutputConfiguration;
    invoke-virtual {v1}, Landroid/hardware/camera2/params/OutputConfiguration;->getConfiguredFormat()I
    move-result v2
    const/16 v3, 0x20
    if-eq v2, v3, :poco_raw_apply
    const/16 v3, 0x25
    if-eq v2, v3, :poco_raw_apply
    const/16 v3, 0x26
    if-ne v2, v3, :poco_raw_loop
    :poco_raw_apply
    const-string v0, "PocoRawSession"
    const-string v1, "Selecting Qualcomm0x9000 only for marble primary camera session containing RAW output"
    invoke-static {v0, v1}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I
    const p0, 0x9000
    :poco_raw_keep
    return p0
.end method
'''

def patch_raw_session_smali(text: str):
    if 'pocoRawSessionType' in text:
        raise PatchError('RAW session compatibility patch already present')
    owners=re.findall(r'^\.class\s+[^\n]*\s+(L[^;\s]+;)$',text,re.M)
    fields=re.findall(r'^\.field\s+[^\n]*\s+(\w+):Ljava/lang/String;$',text,re.M)
    if len(owners)!=1 or len(fields)!=1:
        raise PatchError('Camera device owner or camera-ID field is ambiguous')
    owner=owners[0]; field=fields[0]
    anchor='    new-instance v2, Landroid/hardware/camera2/params/SessionConfiguration;'
    ctor='invoke-direct {v2, v15, v13, v9, v0}, Landroid/hardware/camera2/params/SessionConfiguration;-><init>(ILjava/util/List;Ljava/util/concurrent/Executor;Landroid/hardware/camera2/CameraCaptureSession$StateCallback;)V'
    if text.count(anchor)!=1 or text.count(ctor)!=1:
        raise PatchError('Session constructor register roles changed')
    before,after=text.split(anchor)
    method=before[before.rfind('.method '):]
    # v5 held the callback handler; that constructor consumed it. The list,
    # executor, callback, and original type remain in v13/v9/v0/v15.
    flow=r'move-object v13, v5\s+move-object/from16 v5, v18\s+invoke-direct/range \{v0 \.\. v5\}, L[^;]+;-><init>\(L[^;]+;L[^;]+;L[^;]+;L[^;]+;Landroid/os/Handler;\)V\s*$'
    if not re.search(flow,method) or not re.search(r'iget v15, v6, L[^;]+;->\w+:I',method):
        raise PatchError('Unreviewed session-constructor data flow')
    if not re.match(r'\s*'+re.escape(ctor),after):
        raise PatchError('Unexpected statements between allocation and constructor')
    block=(f'\n    iget-object v5, v1, {owner}->{field}:Ljava/lang/String;\n'
           f'    invoke-static {{v15, v5, v13}}, {owner}->pocoRawSessionType(ILjava/lang/String;Ljava/util/List;)I\n'
           '    move-result v15\n')
    result=before+anchor+block+after+HELPER
    return result, {'owner':owner,'camera_id_field':field,'model':'marble','minimum_api':37,
                    'camera_ids':['0','1'],'original_session_type':0,'raw_session_type':36864,
                    'raw_formats':[32,37,38],'request_targets':'preserved'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    patched,report=patch_raw_session_smali(args.source.read_text(encoding='utf-8'))
    args.output.write_text(patched,encoding='utf-8')
    print(report)
