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
3. En la barra del editor, elegir la función `configurar` y presionar **Ejecutar**. Google pide
   autorizar el acceso a la hoja; como el script es propio y no está verificado, muestra
   "Google no verificó esta app": **Configuración avanzada > Ir a … (no seguro) > Permitir**.
   La función crea la hoja `sesiones`, pone la zona horaria de la Ciudad de México y genera
   el `SECRETO`.
4. **Configuración del proyecto > Propiedades del script**: copiar el `SECRETO` a un lugar
   seguro. Si se pierde (por ejemplo, al borrar el script), ya no se pueden vincular sesiones
   nuevas con las anteriores. Aquí también se puede agregar `CLAVE` (opcional), una palabra
   para la clase, por ejemplo `circuitos-2026`.
5. **Implementar > Nueva implementación > engrane > Aplicación web**.
   - Ejecutar como: **Yo**.
   - Quién tiene acceso: **Cualquier usuario**.
6. Copiar la URL de la aplicación web (termina en `/exec`) y ponerla en `web/config.js`
   como `registroURL`. Si se usó `CLAVE`, ponerla también en `clave`.
7. Volver a publicar el sitio.

Para probar: abrir la URL `/exec` en el navegador debe mostrar `"ok":true` y `"secreto":true`.
Después, resolver un problema en el sitio; en unos segundos aparece un renglón en la hoja
`sesiones` y el contador `sesiones` de la URL sube.

## Límites a tener en cuenta

- Apps Script atiende hasta 30 envíos simultáneos. El sitio envía la sesión cada 5
  respuestas y al terminar; si un envío falla, lo guarda en el dispositivo y lo reintenta
  la siguiente vez que se abre el sitio.
- Cada celda admite 50,000 caracteres; la transcripción se recorta a 45,000.
- Si el profesor cambia el código del script, hay que crear una **nueva versión** de la
  implementación para que la URL use el código nuevo.
