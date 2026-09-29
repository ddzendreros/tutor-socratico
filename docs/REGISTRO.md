# Registro de sesiones en Google Sheets

La versión web del tutor corre en el navegador de cada alumno. Para que el profesor
reciba los indicadores y las transcripciones, cada sesión se envía a una hoja de
cálculo suya mediante Google Apps Script. No cuesta nada y los datos quedan en su
cuenta de Google.

## Qué se guarda

Un renglón por sesión: código del alumno, grupo, problema, método, modo, inicio,
fin, los indicadores de la interacción (intentos, pistas por nivel, errores
conceptuales y procedimentales, corrección autónoma, justificaciones, verificación
con simulador o balance de potencias, nivel final de ayuda) y la transcripción.

La boleta **no sale del dispositivo**. El navegador calcula su huella SHA-256 y el
script la convierte en un código con HMAC-SHA256 y el `SECRETO`, que sólo conoce
el profesor. El mismo alumno siempre produce el mismo código, así que se pueden
seguir sus sesiones durante el semestre, pero sin el secreto no se puede saber de
quién es.

## Configuración (una sola vez, unos 10 minutos)

1. Crear una hoja de cálculo nueva en Google Drive (por ejemplo, "Tutor socrático - registros").
2. Menú **Extensiones > Apps Script**. Borrar el contenido y pegar `docs/registro.gs`. Guardar.
3. En el editor: **Configuración del proyecto > Propiedades del script > Agregar propiedad**:
   - `SECRETO`: un texto largo y aleatorio. Guardarlo en un lugar seguro: si se pierde,
     ya no se pueden vincular sesiones nuevas con las anteriores.
   - `CLAVE` (opcional): una palabra para la clase, por ejemplo `circuitos-2026`.
4. **Implementar > Nueva implementación > Tipo: Aplicación web**.
   - Ejecutar como: **Yo**.
   - Quién tiene acceso: **Cualquier usuario**.
   - Autorizar los permisos que pide Google (sólo acceso a esa hoja).
5. Copiar la URL de la aplicación web (termina en `/exec`) y ponerla en `web/config.js`
   como `registroURL`. Si se usó `CLAVE`, ponerla también en `clave`.
6. Volver a publicar el sitio.

Para probar: resolver un problema en el sitio; en unos segundos aparece un renglón en la
hoja `sesiones`.

## Límites a tener en cuenta

- Apps Script atiende hasta 30 envíos simultáneos. El sitio envía la sesión cada 5
  respuestas y al terminar; si un envío falla, lo guarda en el dispositivo y lo reintenta
  la siguiente vez que se abre el sitio.
- Cada celda admite 50,000 caracteres; la transcripción se recorta a 45,000.
- Si el profesor cambia el código del script, hay que crear una **nueva versión** de la
  implementación para que la URL use el código nuevo.
