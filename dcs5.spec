# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


a = Analysis(
    ['dcs5/gui.py'],
    pathex=[],
    binaries=[],
    datas=[('./*.html', '.'),
    ('doc/*.pdf', 'doc/'),
    ('doc/images', 'doc/images'),
    ('dcs5/static', 'static'),
    ('dcs5/default_configs', 'default_configs')
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
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
    [],
    exclude_binaries=True,
    name='dcs5',
    debug=False,
    icon='./dcs5/static/bigfin_logo.ico',
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='dcs5',
)
