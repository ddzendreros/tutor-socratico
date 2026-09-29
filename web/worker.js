// Motor del tutor en un hilo aparte (worker de tipo módulo, el que admite pyodide.mjs).
// Arrancar Python e importar SymPy toma varios segundos (decenas en un celular sencillo);
// en el hilo de la página la dejaría congelada todo ese tiempo.

// SymPy trae sólo el código fuente y Python lo compila en cada visita. Los .pyc de las
// bibliotecas se guardan en IndexedDB para las siguientes; el código del tutor no se guarda
// porque cambia con cada versión del sitio.
const CACHE_DIR = "/pyc";
const CACHE_PY = `
import os, shutil, sys
import importlib._bootstrap_external as _be

def _setup(root, key):
    for name in os.listdir(root):
        if name != key:
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    write = _be.SourceFileLoader._cache_bytecode

    def cache(self, source_path, bytecode_path, data):
        if "/site-packages/" in source_path:
            # pyc por hash sin verificar: el paquete se desempaca en cada visita con otra fecha
            header = bytes(data[:4]) + (1).to_bytes(4, "little") + bytes(8)
            write(self, source_path, bytecode_path, header + bytes(data[16:]))

    _be.SourceFileLoader._cache_bytecode = cache
    sys.pycache_prefix = os.path.join(root, key)
    sys.dont_write_bytecode = False
    return any(files for _, _, files in os.walk(sys.pycache_prefix))
`;

let py = null;
let bridge = null;

const syncfs = (populate) =>
  new Promise((resolve, reject) => py.FS.syncfs(populate, (err) => (err ? reject(err) : resolve())));

async function openCache(key) {
  try {
    py.FS.mkdirTree(CACHE_DIR);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, CACHE_DIR);
    await syncfs(true);
    py.runPython(CACHE_PY);
    return py.globals.get("_setup")(CACHE_DIR, key);
  } catch (err) {
    console.warn("Sin caché de bytecode:", err);
    return false;
  }
}

async function clearCache() {
  try {
    py.runPython(`import shutil, os\nfor n in os.listdir("${CACHE_DIR}"): shutil.rmtree("${CACHE_DIR}/" + n, ignore_errors=True)`);
    await syncfs(false);
  } catch { /* se intentará en la siguiente visita */ }
}

async function init(indexURL) {
  postMessage({ type: "status", text: "Descargando el tutor… la primera vez tarda un poco." });
  const { loadPyodide } = await import(indexURL + "pyodide.mjs");
  py = await loadPyodide({ indexURL });
  await py.loadPackage(["sympy", "numpy"]);
  const bundle = await (await fetch("bundle.json", { cache: "no-cache" })).json();
  const home = "/home/pyodide";
  for (const [path, content] of Object.entries(bundle.files)) {
    const parts = path.split("/");
    if (parts.length > 1) py.FS.mkdirTree(home + "/" + parts.slice(0, -1).join("/"));
    py.FS.writeFile(home + "/" + path, content);
  }
  py.runPython(`import os, sys\nos.chdir("${home}")\nsys.path.insert(0, "${home}")`);
  postMessage({ type: "status", text: "Preparando el tutor…" });
  const cached = await openCache((indexURL.match(/v[\d.]+/) || ["pyodide"])[0]);
  try {
    bridge = py.pyimport("bridge");
  } catch (err) {
    // un .pyc dañado: se borra la caché y la siguiente visita compila de nuevo
    if (cached) await clearCache();
    throw err;
  }
  postMessage({ type: "ready", problems: bridge.problems() });
  syncfs(false).catch((err) => console.warn("No se guardó la caché:", err));   // sólo escribe lo nuevo
}

const CALLS = new Set(["start", "reply", "record"]);

self.onmessage = async ({ data }) => {
  if (data.type === "init") {
    try {
      await init(data.indexURL);
    } catch (err) {
      postMessage({ type: "error", message: String(err && err.message || err) });
    }
    return;
  }
  const { id, fn, args } = data;
  try {
    if (!bridge || !CALLS.has(fn)) throw new Error("El tutor no está listo.");
    postMessage({ id, result: bridge[fn](...args) });
  } catch (err) {
    postMessage({ id, error: String(err && err.message || err) });
  }
};
