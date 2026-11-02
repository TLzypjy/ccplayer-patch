#!/usr/bin/env python3
"""自动补丁流水线：对 cc播放器（CCMusicModule）原版 deb 做"去抢焦点"二进制补丁并重打包。

改动内容（双架构各 1 处，已字节级验证）：
  - arm64  切片：__text 内 va 0x52704  -> 文件偏移 0x5A704 ，期望指令 BL -> ccm_launchMusicAppForIdlePlayRequest 桩(0x612e0)，替换为 NOP
  - arm64e 切片：__text 内 va 0x53784  -> 文件偏移 0x10B784，期望指令 BL -> ccm_launchMusicAppForIdlePlayRequest 桩(0x62a80)，替换为 NOP
效果：点播放键且当前无播放时，模块不再自动启动/恢复 QQ音乐/网易云/汽水/酷我等音乐 App，
从代码层面消除"抢占系统音频焦点导致他应用自动暂停"的路径。背景视频/图片原本即静音，不动。

安全策略：任何一处的原指令与期望字节不符（说明 deb 版本不同/布局变化）=> 直接失败退出，绝不盲改。
仅使用 Python 标准库（tarfile/lzma/struct），Windows/macOS/Linux/GitHub Actions 均可运行。
"""
import io
import lzma
import re
import struct
import sys
import tarfile
from pathlib import Path

PAYLOAD_REL = 'var/jb/Library/ControlCenter/Bundles/CCMusicModule.bundle/CCMusicModule'

# (文件偏移, 期望指令, 说明)
PATCHES = [
    (0x5A704, 0x94003AF7, 'arm64   BL -> ccm_launchMusicAppForIdlePlayRequest stub 0x612e0'),
    (0x10B784, 0x94003CBF, 'arm64e  BL -> ccm_launchMusicAppForIdlePlayRequest stub 0x62a80'),
]
NOP = 0xD503201F


def parse_ar(data: bytes):
    pos = 8
    mems = {}
    while pos < len(data):
        h = data[pos:pos + 60]
        name = h[0:16].decode('ascii', 'replace').rstrip(' /')
        size = int(h[48:58].decode().strip() or 0)
        pos += 60
        payload = data[pos:pos + size]
        pos += size
        if size % 2:
            pos += 1
        mems[name] = payload
    return mems


def read_tar_members(payload: bytes):
    raw = io.BytesIO(payload)
    tf = None
    for mode in ('r:gz', 'r:xz', 'r:'):
        try:
            raw.seek(0)
            tf = tarfile.open(fileobj=raw, mode=mode)
            break
        except Exception:
            continue
    if tf is None:
        raise ValueError('cannot open tar payload')
    out = []
    for m in tf.getmembers():
        if m.isfile():
            f = tf.extractfile(m)
            out.append((m, f.read() if f else b''))
        else:
            out.append((m, None))
    return out


def write_tar(members, fmt):
    buf = io.BytesIO()
    tf = tarfile.open(fileobj=buf, mode='w', format=fmt)
    for m, content in members:
        if m.isfile() and content is not None:
            tf.addfile(m, io.BytesIO(content))
        else:
            tf.addfile(m)
    tf.close()
    return buf.getvalue()


def ar_member(name: str, payload: bytes):
    name_field = (name + '/').encode()
    name_field = name_field + b' ' * (16 - len(name_field))
    hdr = (name_field + b'0'.ljust(12, b' ') + b'0'.ljust(6, b' ') + b'0'.ljust(6, b' ')
           + b'100644'.ljust(8, b' ') + str(len(payload)).encode().rjust(10, b' ') + b'`\n')
    body = payload
    if len(payload) % 2:
        body += b'\n'
    return hdr + body


def bump_version(v: str) -> str:
    m = re.fullmatch(r'(\d+(?:[.:][0-9A-Za-z.+-]*)?)-(\d+)', v.strip())
    if m:
        return '%s-%d' % (m.group(1), int(m.group(2)) + 1)
    return '%s-1' % v.strip()


def main():
    if len(sys.argv) < 2:
        print('usage: python patch_deb.py <input.deb> [output_dir]')
        return 2
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('output')
    out_dir.mkdir(parents=True, exist_ok=True)

    orig = src.read_bytes()
    mems = parse_ar(orig)
    ctrl_payload = mems.get('control.tar.xz') or mems.get('control.tar.gz') or mems.get('control.tar')
    data_payload = mems.get('data.tar.xz') or mems.get('data.tar.gz') or mems.get('data.tar')
    if ctrl_payload is None or data_payload is None:
        print('ERROR: control/data tar member not found in deb')
        return 1

    # ---- control: bump Version ----
    ctrl_members = read_tar_members(ctrl_payload)
    new_version = None
    for i, (m, content) in enumerate(ctrl_members):
        if m.isfile() and m.name.rstrip('/') == 'control':
            s = content.decode('utf-8')
            m2 = re.search(r'^Version:\s*(.+)$', s, re.M)
            if not m2:
                print('ERROR: Version line not found in control')
                return 1
            new_version = bump_version(m2.group(1))
            s2 = s[:m2.start(1)] + new_version + s[m2.end(1):]
            b2 = s2.encode('utf-8')
            m.size = len(b2)
            ctrl_members[i] = (m, b2)
            print('control Version %r -> %r' % (m2.group(1), new_version))
            break
    if new_version is None:
        print('ERROR: control member not found')
        return 1

    # ---- data: find Payload, byte-assert + patch, rebuild ----
    data_members = read_tar_members(data_payload)
    patched = None
    for i, (m, content) in enumerate(data_members):
        if m.isfile() and m.name.lstrip('./') == PAYLOAD_REL:
            buf = bytearray(content)
            for off, exp, note in PATCHES:
                if off + 4 > len(buf):
                    print('ERROR: offset %#x out of range (%s)' % (off, note))
                    return 1
                cur = struct.unpack_from('<I', buf, off)[0]
                if cur != exp:
                    print('ERROR: offset %#x = %#010x, expected %#010x (%s)' % (off, cur, exp, note))
                    print('      该 deb 与已验证版本布局不符，拒绝盲改。')
                    return 1
                struct.pack_into('<I', buf, off, NOP)
                print('patched %#x: %#010x -> %#010x  (%s)' % (off, exp, NOP, note))
            m.size = len(buf)
            data_members[i] = (m, bytes(buf))
            patched = bytes(buf)
            break
    if patched is None:
        print('ERROR: Payload member %r not found' % PAYLOAD_REL)
        return 1

    new_data_tar = write_tar(data_members, tarfile.GNU_FORMAT)
    new_data_xz = lzma.compress(new_data_tar)
    new_ctrl_tar = write_tar(ctrl_members, tarfile.GNU_FORMAT)
    new_ctrl_xz = lzma.compress(new_ctrl_tar)

    out = b'!<arch>\n' + ar_member('debian-binary', b'2.0\n') + ar_member('control.tar.xz', new_ctrl_xz) + ar_member('data.tar.xz', new_data_xz)
    out_name = 'ccplayer-%s-去抢焦点.deb' % new_version
    out_path = out_dir / out_name
    out_path.write_bytes(out)
    print('written %s (%d bytes)' % (out_path, len(out)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
