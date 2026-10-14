# Guía de Construcción y Empaquetado — Tutor Socrático

Este documento detalla los requisitos y el procedimiento completo para compilar la aplicación de escritorio y generar el instalador de Windows para distribución.

---

## 1. Requisitos del entorno de construcción

1. **Sistema Operativo:** Windows 10 o Windows 11 de 64 bits (x64).
2. **Python:** Versión 3.12 (por ejemplo Python 3.12.10 en `.venv312`).
   - Dependencias de ejecución (`requirements.txt`) y de desarrollo (`requirements-dev.txt`, que incluye `pyinstaller`) instaladas en ese entorno.
3. **Inno Setup:** versión 6.5.2 o posterior (la primera que permite un instalador de un solo archivo de casi 4 GB; la instalada aquí es la 6.7.3), en `%LOCALAPPDATA%\Programs\Inno Setup 6\`, en `Program Files` o en el PATH.
   - Si no está instalado:
     ```powershell
     winget install JRSoftware.InnoSetup
     ```
4. **Binarios de inferencia:**
   - Binarios de llama.cpp en `vendor/llama/` (win-cpu-x64, build 11429). `tools.construir` copia sólo los 24 archivos de su lista `ARCHIVOS_LLAMA`.
5. **Modelo de lenguaje:**
   - Archivo GGUF en `vendor/modelos/` correspondiente a `MODELO_ARCHIVO` de `escritorio/config.py` (por omisión `gemma-4-E2B_q4_0-it.gguf`).

---

## 2. Configuración de la llave de cifrado del estudio

Por seguridad y diseño criptográfico, la llave privada **nunca** viaja en el repositorio ni en el instalador. Solo la llave pública se incluye en la aplicación empaquetada.

### Generar el par de llaves real

Ejecuta el siguiente comando especificando una carpeta segura **fuera del repositorio**:

```powershell
.venv312\Scripts\python.exe -m tools.llaves generar --salida "C:\Ruta\Segura\LlavesEstudio"
```

El script solicitará una contraseña para proteger la llave privada. Se generarán dos archivos:
- `llave_privada.tsk`: Debe ser custodiada bajo contraseña exclusivamente por los investigadores responsables.
- `llave_publica.txt`: Contiene la llave pública en texto plano (Base64).

El comando imprimirá la huella corta (12 caracteres hexadecimales) de la llave:
```text
Huella de la llave: 4ac0f77512e4
```

### Instalar la llave pública para la construcción

Copia el archivo `llave_publica.txt` generado a la carpeta `escritorio/`:

```powershell
Copy-Item "C:\Ruta\Segura\LlavesEstudio\llave_publica.txt" "escritorio\llave_publica.txt"
```

El archivo `escritorio/llave_publica.txt` está en `.gitignore`: no se sube al repositorio, pero sí viaja dentro del programa construido.

### Comprobar la huella de la llave privada

Puedes verificar en cualquier momento que la contraseña y la huella de la llave privada coinciden con:

```powershell
.venv312\Scripts\python.exe -m tools.llaves verificar "C:\Ruta\Segura\LlavesEstudio\llave_privada.tsk"
```

---

## 3. Construcción automatizada de punta a punta

Para ejecutar el proceso completo de verificación, empaquetado y generación del instalador:

```powershell
.venv312\Scripts\python.exe -m tools.construir
```

### Pasos ejecutados por `tools.construir`:

1. **Paso 1 — Llave pública y pruebas:**  
   Comprueba que `escritorio/llave_publica.txt` exista (si falta, se detiene) y muestra su huella: compárala con la que imprime `tools.llaves verificar` de la llave privada. A continuación, corre la suite completa de pruebas (`pytest`).
2. **Paso 2 — PyInstaller:**  
   Compila la aplicación con `escritorio.spec` en modo `onedir` sin consola, con `contents_directory='.'`: los recursos quedan junto a `TutorSocratico.exe`, sin carpeta `_internal`, que es donde el programa los busca (la carpeta del ejecutable).
3. **Paso 3 — Recursos de inferencia:**  
   Copia los 24 archivos requeridos de `vendor/llama/` (incluyendo variantes CPU de ggml) a `dist/TutorSocratico/llama/` y el modelo GGUF a `dist/TutorSocratico/modelo/`.
4. **Paso 4 — Metadatos de versión:**  
   Calcula los hashes SHA-256 del modelo y del servidor, y escribe `dist/TutorSocratico/version.json`.
5. **Paso 5 — Instalador Inno Setup:**  
   Localiza `ISCC.exe` y compila `instalador/tutor.iss`, pasando la versión actual mediante `/DMyAppVersion`. El modelo GGUF se empaqueta con `Flags: nocompression` para acelerar la construcción.
6. **Paso 6 y 7 — Resumen y verificación:**  
   Comprueba que exista `salida/TutorSocratico-Instalador-<versión>.exe` e imprime el tamaño de `dist/TutorSocratico` y el tamaño y SHA-256 del instalador.

### Opciones de depuración

- `--saltar-pruebas`: Omite la ejecución de pytest antes de compilar (útil si ya pasaron las pruebas y se quiere ahorrar tiempo).
- `--sin-instalador`: Genera la carpeta `dist/TutorSocratico` sin compilar el `.exe` final de Inno Setup.

La versión sale de `escritorio/__init__.py` (`__version__`) y llega a Inno Setup con `/DMyAppVersion`; si se compila `instalador/tutor.iss` sin esa definición, falla con un error.

### Tamaño del instalador

Un instalador de Inno Setup de un solo archivo puede llegar a casi 4 GB desde la versión 6.5.2 (antes, casi 2 GB). Fuente: *Revision History* de Inno Setup (`whatsnew.htm` en su carpeta de instalación), sección «Removed main size limits». La misma fuente recomienda dividir el instalador en discos (`DiskSpanning`) cuando es muy grande, porque Windows tarda en arrancarlo.

---

## 4. Verificación previa con autodiagnóstico

Antes de distribuir el ejecutable, puedes probar su funcionamiento de punta a punta en la máquina local sin interfaz gráfica:

```powershell
dist\TutorSocratico\TutorSocratico.exe --autodiagnostico
```

- Sale con código `0` si pasó exitosamente, o `1` en caso de error.
- Escribe el reporte sólo en `%LOCALAPPDATA%\TutorSocratico\autodiagnostico.txt` (también lo imprime en la consola si se ejecuta desde una).
- Si el tutor está abierto, se niega: lo dice en el reporte y sale con `1`, porque al arrancar el modelo cerraría el `llama-server` que usa el tutor abierto. Mientras corre, reserva la instancia, así que el tutor tampoco se puede abrir al mismo tiempo.
- Usa una carpeta temporal para los datos del alumno de prueba, que se destruye al terminar. Deja la bitácora técnica y el registro del modelo (`registro-app.log`, `registro-modelo-autodiagnostico.log`) en `%LOCALAPPDATA%\TutorSocratico`.
- Reporta el equipo (con la variante de CPU del modelo ya arrancado), los tiempos de carga y precalentamiento y, de tres turnos de conversación, su tiempo, su fuente (`modelo` o `plantilla`) y los primeros 80 caracteres del mensaje. Termina en `EXITO` sólo si los tres mensajes tienen texto y al menos uno lo redactó el modelo.
