#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 SchLib 符号编辑器打包成单个窗口程序。"""

import os
import sys
import subprocess
import shutil
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC_NAME_BASE = "Altium_Schlib_Symbol_Editor"
RELEASE_DIR = "release"
ENTRY_SCRIPT = "gui.py"


def check_pyinstaller():
    """检查 PyInstaller 是否已安装。"""
    try:
        import PyInstaller
        print(f"[OK] 已安装 PyInstaller {PyInstaller.__version__}")
        return True
    except ImportError:
        print("[ERROR] 未安装 PyInstaller")
        return False


def install_pyinstaller():
    """安装 PyInstaller。"""
    print("正在安装 PyInstaller...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
            cwd=ROOT,
        )
        print("[OK] PyInstaller 安装完成")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] PyInstaller 安装失败: {exc}")
        return False


def increment_version():
    """把 version.py 的末位加一，超过 9 则进位。"""
    print("正在递增版本号...")
    try:
        from version_manager import ToolVersionManager

        success, new_version = ToolVersionManager().increment_and_update_advanced()
        if success and new_version:
            print(f"[OK] 版本号已更新为 {new_version}")
            return new_version
        print("[ERROR] 版本号递增失败")
        return None
    except Exception as exc:
        print(f"[ERROR] 版本号递增失败: {exc}")
        return None


def get_current_version():
    """读取 version.py 里的当前版本号。"""
    try:
        content = (ROOT / "version.py").read_text(encoding="utf-8")
        match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
        print("[ERROR] version.py 里没有 VERSION")
        return None
    except OSError as exc:
        print(f"[ERROR] 读取版本号失败: {exc}")
        return None


def create_spec_file(spec_name, exe_name):
    """生成当前项目的 PyInstaller spec。"""
    root = str(ROOT).replace("\\", "\\\\")
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

a = Analysis(
    ['{ENTRY_SCRIPT}'],
    pathex=[r'{root}'],
    binaries=[],
    datas=collect_data_files('matplotlib'),
    hiddenimports=[
        'olefile',
        'matplotlib.backends.backend_tkagg',
        'matplotlib.backends.backend_agg',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{exe_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''
    try:
        (ROOT / f"{spec_name}.spec").write_text(spec_content, encoding="utf-8")
        print(f"[OK] 已生成 {spec_name}.spec")
        return True
    except OSError as exc:
        print(f"[ERROR] 生成 spec 失败: {exc}")
        return False


def build_exe_from_spec(spec_name):
    """用 spec 调用 PyInstaller。"""
    print("正在打包...")
    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", f"{spec_name}.spec"]
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        print("[OK] 打包完成")
        return True
    print("[ERROR] 打包失败")
    if result.stdout:
        print(result.stdout[-4000:])
    if result.stderr:
        print(result.stderr[-4000:])
    return False


def build_exe():
    """递增版本并打包。"""
    os.chdir(ROOT)
    if not get_current_version():
        return False
    new_version = increment_version()
    if not new_version:
        return False
    exe_name = f"{SPEC_NAME_BASE}_v{new_version}"
    if not create_spec_file(SPEC_NAME_BASE, exe_name):
        return False
    if not build_exe_from_spec(SPEC_NAME_BASE):
        return False
    return new_version


def create_release_package(final_version):
    """把 exe 复制到 release 目录。"""
    print("正在整理发布文件...")
    release_dir = ROOT / RELEASE_DIR
    release_dir.mkdir(exist_ok=True)
    exe_name = f"{SPEC_NAME_BASE}_v{final_version}.exe"
    exe_path = ROOT / "dist" / exe_name
    if not exe_path.is_file():
        print(f"[ERROR] 找不到 {exe_path}")
        return False
    shutil.copy2(exe_path, release_dir / exe_name)
    readme = (
        f"Altium SchLib 符号编辑器 v{final_version}\n\n"
        f"运行 {exe_name}。\n"
        "可以打开一个 SchLib，或打开文件夹里的全部 SchLib，编辑多显示模式符号。\n"
        "保存前请关闭正在使用这些库的 Altium Designer。\n"
        "备份写到库文件同级的 History 文件夹，文件名带精确到秒的时间戳。\n"
    )
    (release_dir / "README.txt").write_text(readme, encoding="utf-8")
    print(f"[OK] 发布文件在 {release_dir}")
    return True


def main():
    print("=" * 50)
    print("Altium SchLib 符号编辑器 - 打包")
    print("=" * 50)
    os.chdir(ROOT)
    if not check_pyinstaller() and not install_pyinstaller():
        print("请手动安装: python -m pip install pyinstaller")
        return False
    final_version = build_exe()
    if not final_version:
        print("打包失败")
        return False
    if not create_release_package(final_version):
        print("整理发布文件失败")
        return False
    print("[OK] 打包完成")
    print(f"可执行文件: {RELEASE_DIR}/{SPEC_NAME_BASE}_v{final_version}.exe")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
