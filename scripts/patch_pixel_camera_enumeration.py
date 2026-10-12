"""Experimental POCO F5 enumeration fix: avoid late logical-to-physical ID remaps.

The native metadata adapter already excludes aliases 3/4/5/6. Exclude the same
IDs at Java enumeration so CameraGraph and CameraDevice select the same camera.
Do not apply to other devices or Pixel Camera layouts without runtime evidence.
"""
import re

ORIGINAL = '    invoke-virtual {{{reg}}}, Landroid/hardware/camera2/CameraManager;->getCameraIdList()[Ljava/lang/String;'
REPLACEMENT = '    invoke-static {{{reg}}}, Lpoco/CameraIds;->get(Landroid/hardware/camera2/CameraManager;)[Ljava/lang/String;'


def patch_smali(text: str, descriptor: str, expected_count: int) -> str:
    if not re.search(r'^\.class\s+.*' + re.escape(descriptor) + r'$', text, re.M):
        raise ValueError('Camera enumeration owner changed')
    if 'Lpoco/CameraIds;' in text:
        raise ValueError('Camera enumeration patch already present')
    pattern = re.compile(r'^    invoke-virtual \{([vp]\d+)\}, Landroid/hardware/camera2/CameraManager;->getCameraIdList\(\)\[Ljava/lang/String;$', re.M)
    if len(pattern.findall(text)) != expected_count:
        raise ValueError('Camera enumeration call count/layout changed')
    return pattern.sub(lambda m: REPLACEMENT.format(reg=m.group(1)), text)


HELPER = '''.class public final Lpoco/CameraIds;
.super Ljava/lang/Object;

.method public static get(Landroid/hardware/camera2/CameraManager;)[Ljava/lang/String;
    .registers 1
    invoke-virtual {p0}, Landroid/hardware/camera2/CameraManager;->getCameraIdList()[Ljava/lang/String;
    move-result-object p0
    invoke-static {p0}, Lpoco/CameraIds;->filter([Ljava/lang/String;)[Ljava/lang/String;
    move-result-object p0
    return-object p0
.end method

.method public static filter([Ljava/lang/String;)[Ljava/lang/String;
    .registers 7
    if-nez p0, :start
    return-object p0
    :start
    new-instance v0, Ljava/util/ArrayList;
    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V
    const/4 v1, 0x0
    array-length v2, p0
    :loop
    if-ge v1, v2, :done
    aget-object v3, p0, v1
    const-string v4, "3"
    invoke-virtual {v4, v3}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v5
    if-nez v5, :next
    const-string v4, "4"
    invoke-virtual {v4, v3}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v5
    if-nez v5, :next
    const-string v4, "5"
    invoke-virtual {v4, v3}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v5
    if-nez v5, :next
    const-string v4, "6"
    invoke-virtual {v4, v3}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v5
    if-nez v5, :next
    invoke-virtual {v0, v3}, Ljava/util/ArrayList;->add(Ljava/lang/Object;)Z
    :next
    add-int/lit8 v1, v1, 0x1
    goto :loop
    :done
    const/4 v1, 0x0
    new-array v1, v1, [Ljava/lang/String;
    invoke-virtual {v0, v1}, Ljava/util/ArrayList;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;
    move-result-object v0
    check-cast v0, [Ljava/lang/String;
    const-string v3, "GCamEnumeratedIds"
    invoke-static {v0}, Ljava/util/Arrays;->toString([Ljava/lang/Object;)Ljava/lang/String;
    move-result-object v4
    invoke-static {v3, v4}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    return-object v0
.end method
'''
