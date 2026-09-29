/**
 * Registro de sesiones del Tutor Socrático en una hoja de cálculo de Google.
 *
 * Se pega en Extensiones > Apps Script de la hoja y se publica como aplicación
 * web (ver docs/REGISTRO.md). Cada sesión ocupa un renglón que se actualiza
 * mientras el alumno avanza.
 *
 * Propiedades del script (Configuración del proyecto > Propiedades del script):
 *   SECRETO  texto largo y aleatorio; sin él no se puede vincular un código con una boleta.
 *   CLAVE    (opcional) la misma clave que config.js; evita registros ajenos a la clase.
 */
const HOJA = "sesiones";
const FIJAS = ["sesion", "alumno", "grupo", "problema", "metodo", "modo", "inicio", "fin"];

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const datos = JSON.parse(e.postData.contents);
    const props = PropertiesService.getScriptProperties();
    const clave = props.getProperty("CLAVE") || "";
    if (clave && datos.clave !== clave) return respuesta({ ok: false, error: "clave" });
    const secreto = props.getProperty("SECRETO");
    if (!secreto) return respuesta({ ok: false, error: "falta SECRETO" });

    const hoja = SpreadsheetApp.getActive().getSheetByName(HOJA) ||
      SpreadsheetApp.getActive().insertSheet(HOJA);
    const indicadores = datos.indicadores || {};
    let encabezado = hoja.getLastRow() ? hoja.getRange(1, 1, 1, hoja.getLastColumn()).getValues()[0] : [];
    const faltan = FIJAS.concat(Object.keys(indicadores), ["transcripcion"])
      .filter((c) => encabezado.indexOf(c) < 0);
    if (faltan.length) {
      hoja.getRange(1, encabezado.length + 1, 1, faltan.length).setValues([faltan]);
      encabezado = encabezado.concat(faltan);
    }
    const valores = {
      sesion: datos.sesion,
      alumno: seudonimo(secreto, datos.alumno_h || ""),
      grupo: datos.grupo || "",
      problema: datos.problema || "",
      metodo: datos.metodo || "",
      modo: datos.modo || "",
      inicio: new Date((datos.inicio || 0) * 1000),
      fin: new Date((datos.fin || 0) * 1000),
      transcripcion: JSON.stringify(datos.transcripcion || []).slice(0, 45000),
    };
    Object.keys(indicadores).forEach((k) => { valores[k] = indicadores[k]; });
    const fila = encabezado.map((c) => (c in valores ? valores[c] : ""));

    const encontrado = hoja.getRange(1, 1, Math.max(hoja.getLastRow(), 1), 1)
      .createTextFinder(String(datos.sesion)).matchEntireCell(true).findNext();
    const r = encontrado ? encontrado.getRow() : hoja.getLastRow() + 1;
    hoja.getRange(r, 1, 1, fila.length).setValues([fila]);
    return respuesta({ ok: true });
  } catch (err) {
    return respuesta({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function seudonimo(secreto, huella) {
  const firma = Utilities.computeHmacSha256Signature(huella, secreto);
  return firma.map((b) => ("0" + (b & 0xff).toString(16)).slice(-2)).join("").slice(0, 16);
}

function respuesta(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
