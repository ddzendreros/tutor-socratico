# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

datas = [
    ('web', 'web'),
    ('problemas', 'problemas'),
    ('data/banco_errores.csv', 'data'),
    ('conocimiento', 'conocimiento'),
    ('textos', 'textos'),
    ('cuestionarios', 'cuestionarios'),
    ('escritorio/llave_publica.txt', 'escritorio'),
]

excludes = [
    'tkinter',
    'matplotlib',
    'pytest',
    'schemdraw',
]

hiddenimports = [
    'escritorio',
    'escritorio.alumnos',
    'escritorio.api',
    'escritorio.autodiagnostico',
    'escritorio.cifrado',
    'escritorio.config',
    'escritorio.conocimiento',
    'escritorio.conversacion',
    'escritorio.cuestionarios',
    'escritorio.equipo',
    'escritorio.exportar',
    'escritorio.modelo',
    'escritorio.registro',
    'escritorio.rutas',
    'escritorio.servidor',
    'escritorio.ventana',
    'tutor',
    'tutor.analysis',
    'tutor.circuit',
    'tutor.diagnose',
    'tutor.engine',
    'tutor.errorbank',
    'tutor.llm',
    'tutor.mathparse',
    'tutor.privacidad',
    'tutor.problems',
    'tutor.reduction',
    'tutor.solver',
]

a = Analysis(
    ['escritorio/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='TutorSocratico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='instalador/tutor.ico',
    version='instalador/version_info.txt',
    contents_directory='.',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TutorSocratico',
)
