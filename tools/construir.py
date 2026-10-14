"""Script de construcción de la distribución empaquetada e instalador de Tutor Socrático.

Antes de empezar comprueba que el código esté en un commit, sin cambios pendientes: el commit
queda en version.json y en los datos de cada sesión, para saber con qué código se generaron.

Pasos:
  1. Verificación de la llave pública y ejecución de la suite de pruebas.
  2. Empaquetado con PyInstaller (escritorio.spec).
  3. Copia de los binarios mínimos de llama y del modelo GGUF.
  4. Generación de version.json con metadatos y hashes.
  5. Compilación del instalador con Inno Setup (ISCC.exe).
  6. Comprobación del instalador generado en salida/.
  7. Reporte final de tamaños y SHA-256.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from escritorio import __version__
from escritorio.cifrado import huella_publica, leer_llave_publica
from escritorio.config import MODELO_ARCHIVO

ARCHIVOS_LLAMA = [
    "llama-server.exe",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-rpc.dll",
    "ggml-cpu-alderlake.dll",
    "ggml-cpu-cannonlake.dll",
    "ggml-cpu-cascadelake.dll",
    "ggml-cpu-cooperlake.dll",
    "ggml-cpu-haswell.dll",
    "ggml-cpu-icelake.dll",
    "ggml-cpu-ivybridge.dll",
    "ggml-cpu-piledriver.dll",
    "ggml-cpu-sandybridge.dll",
    "ggml-cpu-sapphirerapids.dll",
    "ggml-cpu-skylakex.dll",
    "ggml-cpu-sse42.dll",
    "ggml-cpu-x64.dll",
    "ggml-cpu-zen4.dll",
    "llama.dll",
    "llama-common.dll",
    "llama-server-impl.dll",
    "mtmd.dll",
    "libomp.dll",
    "LICENSE-LLVM-OpenMP",
]


def sha256_archivo(ruta: Path) -> str:
    """Calcula el SHA-256 de un archivo en bloques de 1 MB."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while bloque := f.read(1024 * 1024):
            h.update(bloque)
    return h.hexdigest()


def tamano_directorio(ruta: Path) -> int:
    """Calcula el tamaño total en bytes de todos los archivos de un directorio."""
    return sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())


def formato_tamano(bytes_: int) -> str:
    """Formatea bytes en formato legible (B, KB, MB, GB)."""
    val = float(bytes_)
    for u in ["B", "KB", "MB", "GB"]:
        if val < 1024.0 or u == "GB":
            return f"{val:.2f} {u}"
        val /= 1024.0
    return f"{val:.2f} GB"


def buscar_iscc() -> Path:
    """Localiza el compilador de Inno Setup (ISCC.exe)."""
    rutas_candidatas: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        rutas_candidatas.append(Path(local_app_data) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)")
    if prog_files_x86:
        rutas_candidatas.append(Path(prog_files_x86) / "Inno Setup 6" / "ISCC.exe")
    prog_files = os.environ.get("ProgramFiles")
    if prog_files:
        rutas_candidatas.append(Path(prog_files) / "Inno Setup 6" / "ISCC.exe")

    for r in rutas_candidatas:
        if r.is_file():
            return r.resolve()

    en_path = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if en_path:
        return Path(en_path).resolve()

    raise FileNotFoundError(
        "No se encontró Inno Setup (ISCC.exe).\n"
        "Instálalo con: winget install JRSoftware.InnoSetup"
    )


def estado_git(raiz: Path) -> tuple[str | None, list[str]]:
    """El commit actual y los cambios sin commit (git status --porcelain); (None, []) fuera de un repositorio."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(raiz), capture_output=True,
                                text=True, check=True).stdout.strip()
        estado = subprocess.run(["git", "status", "--porcelain"], cwd=str(raiz), capture_output=True,
                                text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, []
    return commit, [linea for linea in estado.splitlines() if linea.strip()]


def paso_0_verificar_repositorio(permitir_cambios: bool = False) -> tuple[str | None, bool]:
    print("\n--- Antes de construir: estado del repositorio ---")
    commit, cambios = estado_git(RAIZ)
    if commit is None:
        if not permitir_cambios:
            print("ERROR: No se pudo leer el commit con git. Construye desde el repositorio clonado.",
                  file=sys.stderr)
            sys.exit(1)
        print("AVISO: Sin commit conocido (--permitir-cambios).")
        return None, True
    print(f"Commit: {commit}")
    if cambios:
        print("Cambios sin commit:")
        for linea in cambios:
            print(f"  {linea}")
        if not permitir_cambios:
            print("ERROR: Haz commit de los cambios antes de construir. Para una construcción de prueba usa\n"
                  "--permitir-cambios (quedará marcada en version.json y en los datos).", file=sys.stderr)
            sys.exit(1)
        print("AVISO: Se construye con cambios sin commit (--permitir-cambios). No la repartas.")
    return commit, bool(cambios)


def armar_version_json(
    ruta_dist: Path,
    huella_llave: str,
    nombre_modelo: str = MODELO_ARCHIVO,
    commit: str | None = None,
    cambios_sin_commit: bool = True,
) -> dict:
    """Construye el documento de metadatos de versión y hashes."""
    ruta_modelo = ruta_dist / "modelo" / nombre_modelo
    ruta_llama = ruta_dist / "llama" / "llama-server.exe"

    if not ruta_modelo.is_file():
        raise FileNotFoundError(f"No se encuentra el modelo en {ruta_modelo}")
    if not ruta_llama.is_file():
        raise FileNotFoundError(f"No se encuentra llama-server en {ruta_llama}")

    doc = {
        "aplicacion": "Tutor Socratico",
        "version": __version__,
        "commit": commit,
        "cambios_sin_commit": cambios_sin_commit,
        "fecha_construccion": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "llave_publica_huella": huella_llave,
        "modelo": {
            "archivo": nombre_modelo,
            "sha256": sha256_archivo(ruta_modelo),
            "tamano_bytes": ruta_modelo.stat().st_size,
        },
        "llama_server": {
            "archivo": "llama-server.exe",
            "sha256": sha256_archivo(ruta_llama),
            "tamano_bytes": ruta_llama.stat().st_size,
        },
    }
    return doc


def paso_1_verificar_llave_y_pruebas(saltar_pruebas: bool = False) -> str:
    print("\n--- PASO 1: Verificación de llave pública y pruebas ---")
    ruta_llave = RAIZ / "escritorio" / "llave_publica.txt"
    if not ruta_llave.is_file():
        print(
            "ERROR: Falta el archivo escritorio/llave_publica.txt.\n"
            "Sin esta llave el programa no podrá registrar eventos de los alumnos.\n"
            "Genera el par de llaves con:\n"
            "  python -m tools.llaves generar --salida <carpeta_segura>\n"
            "y copia llave_publica.txt a escritorio/ antes de construir.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        pk = leer_llave_publica(ruta_llave)
        huella = huella_publica(pk)
        print(f"Llave pública encontrada: {ruta_llave}")
        print(f"Huella de la llave pública: {huella}")
    except Exception as e:
        print(f"ERROR: No se pudo leer la llave pública: {e}", file=sys.stderr)
        sys.exit(1)

    if not saltar_pruebas:
        print("Ejecutando suite de pruebas...")
        cmd = [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        res = subprocess.run(cmd, cwd=str(RAIZ))
        if res.returncode != 0:
            print(f"ERROR: La suite de pruebas falló (código {res.returncode}). Deteniendo.", file=sys.stderr)
            sys.exit(res.returncode)
        print("Suite de pruebas superada exitosamente.")
    else:
        print("Pruebas omitidas por parámetro (--saltar-pruebas).")

    return huella


def paso_2_ejecutar_pyinstaller() -> None:
    print("\n--- PASO 2: Ejecutando PyInstaller ---")
    spec_path = RAIZ / "escritorio.spec"
    if not spec_path.is_file():
        print(f"ERROR: No existe el archivo spec: {spec_path}", file=sys.stderr)
        sys.exit(1)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec_path)]
    res = subprocess.run(cmd, cwd=str(RAIZ))
    if res.returncode != 0:
        print(f"ERROR: PyInstaller falló (código {res.returncode}).", file=sys.stderr)
        sys.exit(res.returncode)

    exe_generado = RAIZ / "dist" / "TutorSocratico" / "TutorSocratico.exe"
    if not exe_generado.is_file():
        print(f"ERROR: No se generó el ejecutable esperado en {exe_generado}", file=sys.stderr)
        sys.exit(1)
    print(f"Ejecutable generado con éxito: {exe_generado}")


def paso_3_copiar_llama_y_modelo() -> None:
    print("\n--- PASO 3: Copiando binarios de llama y modelo GGUF ---")
    dist_dir = RAIZ / "dist" / "TutorSocratico"
    llama_dest = dist_dir / "llama"
    modelo_dest = dist_dir / "modelo"

    llama_dest.mkdir(parents=True, exist_ok=True)
    modelo_dest.mkdir(parents=True, exist_ok=True)

    origen_llama = RAIZ / "vendor" / "llama"
    print(f"Copiando {len(ARCHIVOS_LLAMA)} archivos esenciales de llama.cpp...")
    for nom in ARCHIVOS_LLAMA:
        src = origen_llama / nom
        dst = llama_dest / nom
        if not src.is_file():
            print(f"ERROR: Archivo necesario no encontrado: {src}", file=sys.stderr)
            sys.exit(1)
        shutil.copy2(src, dst)

    origen_modelo = RAIZ / "vendor" / "modelos" / MODELO_ARCHIVO
    if not origen_modelo.is_file():
        print(f"ERROR: Modelo GGUF no encontrado en {origen_modelo}", file=sys.stderr)
        sys.exit(1)

    dst_mod = modelo_dest / MODELO_ARCHIVO
    if not dst_mod.is_file() or dst_mod.stat().st_size != origen_modelo.stat().st_size:
        print(f"Copiando modelo {MODELO_ARCHIVO} ({formato_tamano(origen_modelo.stat().st_size)})...")
        shutil.copy2(origen_modelo, dst_mod)
    else:
        print(f"Modelo {MODELO_ARCHIVO} ya presente en dist/ con tamaño correcto.")


def paso_4_escribir_version_json(huella_llave: str, commit: str | None, cambios_sin_commit: bool) -> Path:
    print("\n--- PASO 4: Generando version.json ---")
    dist_dir = RAIZ / "dist" / "TutorSocratico"
    doc = armar_version_json(dist_dir, huella_llave, commit=commit, cambios_sin_commit=cambios_sin_commit)
    ruta_version = dist_dir / "version.json"
    ruta_version.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"version.json escrito en {ruta_version}")
    return ruta_version


def paso_5_compilar_instalador(sin_instalador: bool = False) -> Path | None:
    print("\n--- PASO 5: Compilando instalador con Inno Setup ---")
    if sin_instalador:
        print("Compilación del instalador omitida por parámetro (--sin-instalador).")
        return None

    iscc_bin = buscar_iscc()
    print(f"Compilador Inno Setup localizado: {iscc_bin}")

    iss_path = RAIZ / "instalador" / "tutor.iss"
    salida_dir = RAIZ / "salida"
    salida_dir.mkdir(parents=True, exist_ok=True)

    cmd = [str(iscc_bin), f"/DMyAppVersion={__version__}", str(iss_path)]
    print(f"Ejecutando: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(RAIZ))
    if res.returncode != 0:
        print(f"ERROR: La compilación de Inno Setup falló (código {res.returncode}).", file=sys.stderr)
        sys.exit(res.returncode)

    instalador_esperado = salida_dir / f"TutorSocratico-Instalador-{__version__}.exe"
    if not instalador_esperado.is_file():
        print(f"ERROR: No se encontró el instalador generado en {instalador_esperado}", file=sys.stderr)
        sys.exit(1)

    print(f"Instalador generado con éxito: {instalador_esperado}")
    return instalador_esperado


def paso_6_7_reportar(instalador: Path | None) -> None:
    print("\n=== RESUMEN DE CONSTRUCCIÓN ===")
    dist_dir = RAIZ / "dist" / "TutorSocratico"
    bytes_dist = tamano_directorio(dist_dir)
    print(f"Carpeta distribuida: {dist_dir}")
    print(f"  Tamaño total desempaquetado: {formato_tamano(bytes_dist)} ({bytes_dist:,} bytes)")

    if instalador and instalador.is_file():
        bytes_inst = instalador.stat().st_size
        sha_inst = sha256_archivo(instalador)
        print(f"\nInstalador: {instalador}")
        print(f"  Tamaño: {formato_tamano(bytes_inst)} ({bytes_inst:,} bytes)")
        print(f"  SHA-256: {sha_inst}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Constructor de Tutor Socrático e Instalador")
    parser.add_argument("--saltar-pruebas", action="store_true", help="Omite la ejecución de pytest")
    parser.add_argument("--sin-instalador", action="store_true", help="Omite la compilación de Inno Setup")
    parser.add_argument("--permitir-cambios", action="store_true",
                        help="Construcción de prueba con cambios sin commit (queda marcada; no se reparte)")
    args = parser.parse_args(argv)

    commit, cambios_sin_commit = paso_0_verificar_repositorio(permitir_cambios=args.permitir_cambios)
    huella_llave = paso_1_verificar_llave_y_pruebas(saltar_pruebas=args.saltar_pruebas)
    paso_2_ejecutar_pyinstaller()
    paso_3_copiar_llama_y_modelo()
    paso_4_escribir_version_json(huella_llave, commit, cambios_sin_commit)
    instalador = paso_5_compilar_instalador(sin_instalador=args.sin_instalador)
    paso_6_7_reportar(instalador)
    return 0


if __name__ == "__main__":
    sys.exit(main())
