# Datos que registra el tutor

Esquema de eventos, versión `ESQUEMA_VERSION = 1` (`escritorio/registro.py`).

Sólo se registra a quien aceptó el aviso de consentimiento. Quien no acepta usa el tutor igual, pero no
se escribe ningún evento suyo. Se registra únicamente lo que ocurre dentro de la ventana del tutor: nada de
otros programas o páginas, ni la pantalla, la cámara o el micrófono.

Todo vive en `%LOCALAPPDATA%\TutorSocratico\datos`:

| Archivo | Contenido |
|---|---|
| `instalacion.json` | En claro: `instalacion` (identificador del equipo), `sal` y `creado`. |
| `<código>/eventos.tsd` | Los eventos del alumno, **cifrados**. Formato TSD1: el encabezado `TSD1` y, por cada lote, su longitud (4 bytes, little-endian) y el lote sellado con la llave pública del equipo de investigación (compresión y `SealedBox`). El programa sólo tiene la llave pública: puede escribir, no leer. |
| `<código>/eventos.idx` | En claro, sólo conteos: por arranque, el último `seq` y el número de lotes escritos. Sirve para que la exportación declare qué debería traer el archivo. |
| `<código>/estado.bin` | El estado del alumno (consentimiento, intentos en curso, cola de anotaciones), **cifrado** con una llave derivada de su código y la sal de la instalación. |

Cada llamada a guardar eventos es un lote (de hasta 50 eventos), escrito de una vez; un lote que quedó
a medias por un cierre abrupto se corta al arrancar de nuevo. Sin la llave pública del estudio el programa no
registra nada: nunca escribe eventos en claro.

Todo el texto libre pasa por `tutor.privacidad.scrub()`, que cambia correos, teléfonos y números de 10
dígitos (boletas) por `[correo]`, `[teléfono]` y `[número]`. Los números pegados a un punto, una coma u
otro dígito, o seguidos de una unidad, se conservan, porque son valores del circuito. Los identificadores
y huellas (`arranque_turno`, `intento`, `archivo`, `construccion`, `banco_hash`, `prompts_hash` y
`modelo_sha256`) no pasan por el filtro: un UUID o un hash pueden tener la forma de un teléfono.

## Sobre

Todos los eventos llevan estos campos:

| Campo | Contenido |
|---|---|
| `v` | Versión del esquema. |
| `seq` | Número consecutivo del evento, por alumno y por arranque del programa. Empieza en 1. Un hueco indica un evento perdido. |
| `t` | Fecha y hora local en ISO 8601, con zona y milisegundos. |
| `ms` | La misma hora en milisegundos desde 1970. En los eventos de la interfaz es la hora en que ocurrió, no la de llegada. |
| `mono` | Milisegundos desde el arranque del programa, con reloj monotónico: no cambia si se ajusta la hora del equipo. Los eventos de la interfaz se pasan a este reloj al llegar. |
| `codigo` | Código del alumno (`TS-XXXX-C`). |
| `instalacion` | Identificador de la instalación (`datos/instalacion.json`). |
| `arranque` | Identificador del arranque del programa. |
| `intento` | `<problema>:<modo>` del intento abierto (`propio_<hash>:<modo>` para un problema capturado), o `null`. |
| `origen` | `app` (lo escribe el programa) o `ui` (viene de la interfaz). |
| `tipo` | Tipo de evento (abajo). |
| `datos` | Contenido del evento. |

Para ordenar los eventos de un alumno: por `instalacion`, `arranque` y `seq`. Un mismo alumno puede tener
eventos de varios arranques y de varias computadoras.

## Eventos del programa (`origen: app`)

### `sesion_iniciada`

Al entrar con el código (si ya había aceptado) o al aceptar el aviso.

- `equipo`: `so`, `version_so`, `build`, `plataforma`, `cpu`, `nucleos_fisicos`, `nucleos_logicos`,
  `ram_total_gb`, `ram_disponible_gb`, `disco_libre_gb` (de la unidad de los datos),
  `pantalla` (`ancho`, `alto`, `escala`, `ancho_ventana`, `alto_ventana`, en píxeles CSS),
  `webview2` (versión, o `null` si no está), `modo` (`ventana` o `navegador`) y `variante_cpu` (la
  biblioteca de llama.cpp que eligió el procesador, p. ej. `haswell`; `null` si el modelo no ha cargado).
- `versiones`: `app`, `construccion` (los primeros 12 caracteres del commit con el que se construyó el
  instalador, seguidos de `+cambios` si tenía cambios sin commit; `null` si el programa corre desde el
  código), `banco_hash` (SHA-256 corto de los problemas y el banco de errores),
  `prompts_hash` (SHA-256 corto de los prompts), `modelo` (archivo) y `modelo_sha256` (corto; `null` la
  primera vez, mientras se calcula).
- `modelo`: lo mismo que `modelo_estado`.
- `version_consentimiento`: versión del aviso.

### `sesion_terminada`

- `motivo`: `boton`, `inactividad` (30 min sin actividad), `cierre` (se cerró la ventana) o `cambio`
  (entró otro código sin cerrar sesión).
- `ms`: duración de la sesión.

### `consentimiento`

`version` y `acepta: true`. Quien no acepta no genera este ni ningún otro evento.

### `modelo_estado`

Cuando cambia el estado del modelo durante la sesión (por ejemplo, termina de cargar o se cae).

- `estado`: `cargando`, `listo`, `error`, `detenido` o `sin_modelo`.
- `ms_carga`: lo que tardó `llama-server` en cargar el modelo.
- `ms_precalentar`: lo que tardó en dejar los prompts en caché, antes de quedar listo.
- `variante_cpu`, `hilos`, `ctx`, `error`.

### `intento_iniciado`, `intento_pausado` e `intento_terminado`

Todos llevan `problema` (id, o `propio`), `tema`, `metodo`, `modo` y `segundos_activos` (tiempo con el
problema abierto, sumando las sesiones anteriores). En un problema capturado, `circuito_propio` trae el texto.

- `intento_iniciado`: `retomado`, `paso_id` y `paso_tipo` del paso en el que empieza. Si es nuevo, `tutor`
  trae el primer mensaje, con los mismos campos que en `turno`.
- `intento_pausado`: `motivo` (`otro_problema` o el de `sesion_terminada`).
- `intento_terminado`: `indicadores`, los 16 del motor (`Session.indicators()`): intentos del estudiante,
  preguntas socráticas realizadas, pistas de nivel 1, 2 y 3 o superiores, errores conceptuales y
  procedimentales, corrección autónoma, solicitó respuesta directa, verificó con simulador, verificó con
  balance de potencias, justificaciones dadas, nivel final de ayuda, códigos de error, completado y duración.

### `turno`

Un mensaje del alumno (o un clic en "Pista") y la respuesta del tutor.

- `alumno`: `texto`, `caracteres` y `boton` (`true` si vino del botón "Pista").
- `composicion`: cómo escribió el mensaje; `null` si vino del botón.
  - `ms_desde_tutor`: desde que empezó a aparecer el último mensaje del tutor hasta el envío. El mensaje
    aparece letra por letra (unos 60 caracteres por segundo), así que este tiempo incluye el de leerlo.
  - `ms_primera_tecla`: desde ese momento hasta la primera tecla. Es negativo si empezó a escribir
    mientras esperaba la respuesta del tutor.
  - `ms_escribiendo`: desde la primera edición hasta el envío.
  - `teclas` (sin contar el Enter que envía), `borrados` (Retroceso y Supr), `pegados`, `caracteres_pegados`.
  - `pausas_2s`: veces que pasaron 2 s o más entre dos ediciones.
  - `foco_perdido`: veces que salió de la ventana con el mensaje a medio escribir.
  - `eventos`: cada edición del cuadro de texto, `[ms desde la primera edición, inputType, texto,
    posición del cursor]`; los pegados aparecen como `paste` con el texto pegado (hasta 2000 caracteres).
  - `teclas_sin_texto`: `true` si el mensaje o lo tecleado tenía un dato personal; entonces `eventos`
    va sin el texto de cada tecla.
- `ruta`: `motor` (lo decidió una regla) o `ambiguo` (se le preguntó al modelo la intención).
- `intencion`: `etiqueta` con la que se atendió (`respuesta`, `ayuda`, `pregunta_teoria`,
  `pregunta_paso`, `animo`, `fuera_de_tema` o `charla`), `modelo` (lo que dijo el modelo, o `null` si no
  se le preguntó o no respondió) y `ms`. Una pregunta de teoría en un paso de concepto (`concept`, `sup_apagar` o `th_equivalentes`) se atiende como
  `ayuda`, pero `modelo` conserva `pregunta_teoria`.
- `confianza`: `null` si no se preguntó en este turno; si se preguntó, `valor` (1 a 5, o `null` si eligió
  "Prefiero no decir") y `ms` (lo que tardó en contestar).
- `motor`:
  - `respondio`: `false` si el mensaje lo atendió una plantilla o la respuesta de teoría; entonces el resto
    describe el paso en el que estaba y `correcto` y `codigo` son `null`.
  - `paso_id`, `paso_tipo`, `correcto` (`true`, `false` o `null` si no evaluó), `codigo` (`OK` o el código
    de error del banco), `nivel_antes`, `nivel_despues` (nivel de ayuda, 0 a 5), `pista`,
    `intentos_paso` y `terminado`.
- `tutor`: `texto_base` (el mensaje del motor o la plantilla), `texto_mostrado`, `fuente` (`modelo`,
  `plantilla` o `ficha`), `guardia` (`ok`, `motivo`), `reintentos`, `intentos` (cada texto del modelo con
  el veredicto de la guardia), `ms`, `tokens_prompt`, `tokens_salida` y `tokens_cache`.
- `teoria`: sólo en preguntas de teoría; `fichas` (`[id, puntaje]`) y `encontro`.

### `anotacion`

La rúbrica que el modelo da a una respuesta abierta. Es sólo un dato: no cambia lo que decide el motor.
Se calcula en segundo plano cuando el modelo lleva 20 s sin atender al alumno, así que puede llegar varios
eventos después del turno, o en otra sesión.

- `seq_turno` y `arranque_turno`: el `turno` que se anotó.
- `tipo`: `justificacion`, `concepto`, `verificacion` o `metacognicion`.
- `idea_esperada`: la referencia que recibió el modelo.
- `rubrica`: `expresa_idea` (`si`, `parcial`, `no`), `claridad` (1 a 3), `usa_terminos_tecnicos`,
  `idea_erronea` (hasta 100 caracteres, o vacía) y `tipo` (`explicacion`, `repeticion`, `vago` o
  `fuera_de_tema`). Es `null` si el modelo no respondió bien dos veces.
- `ms`, `tokens_prompt`, `tokens_salida` y `modelo` (archivo).

### `cuestionario_mostrado`

Una pregunta de un cuestionario aparece en pantalla (ver `cuestionarios/LEEME.md`). Se repite si
vuelve a aparecer, por ejemplo después de recargar o de cerrar el programa a mitad del cuestionario.
No pertenece a ningún intento (`intento: null`).

- `cuestionario`: su `ID`.
- `version`: su `VERSION` (entero).
- `item`: identificador de la pregunta.

### `cuestionario_respuesta`

El alumno contesta esa pregunta. Cada pregunta se contesta una sola vez y en orden; una respuesta
inválida no se registra.

- `cuestionario`, `version` e `item`, como arriba.
- `respuesta`: entero del 1 al 5 (`likert5`), texto de la opción (`opcion`), texto (`abierta`) o
  número (`numero`). El texto pasa por el mismo filtro de datos personales que la conversación.
- `ms`: desde que apareció la pregunta hasta que se envió la respuesta.

## Eventos de la interfaz (`origen: ui`)

| tipo | datos |
|---|---|
| `ventana` | `estado`: `oculta`, `visible`, `desenfocada` o `enfocada`. En la ventana del programa, `desenfocada`/`enfocada` se disparan al cambiar de aplicación y `oculta`/`visible` al minimizar. |
| `inactividad` | `estado`: `inicio` (tras 60 s sin mouse ni teclado) o `fin`; `ms_sin_actividad` desde la última actividad. |
| `clic` | `control`: `pista`, `elementos_circuito`, `modo` (con `modo`), `problema` (con `problema`), `cambiar_problema`, `exportar`, `abrir_carpeta` o `salir`. |
| `copiar` / `cortar` / `pegar` | `origen` (`tutor`, `alumno`, `enunciado`, `entrada` u `otro`), `caracteres` y `texto` (hasta 500). |
| `chat_desplazamiento` | `posicion` de la página de la sesión (problema arriba, chat abajo): 0 arriba a 1 abajo. Sólo los que hace el alumno (no los de un mensaje nuevo ni los de cambiar el tamaño), como mucho uno cada 2 s. |
| `dibujo` | `accion`: `abrir` o `cerrar`; al cerrar, `ms_abierto`. |
| `pantalla` | `ancho`, `alto` y `escala`, cuando cambian durante la sesión. Los del inicio van en `sesion_iniciada`. |
| `tamano_ventana` | `ancho` y `alto` de la ventana, cuando cambian. |

## Eventos del programa — exportación

### `exportacion`

Se registra cuando el alumno pulsa «Exportar mis datos» y el archivo se genera con éxito. Se escribe
**después** de generar el archivo, así que queda en la exportación siguiente y no en la que lo produjo.

- `archivo`: nombre del .zip generado, sin carpeta (la ruta lleva el nombre de usuario de Windows y no
  se guarda).
- `lotes`: lotes sellados que trae ese .zip en `eventos.tsd`.
- `bytes`: tamaño del .zip.
- `seq_hasta`: el último `seq` del alumno en este arranque antes de la exportación, es decir, el último
  evento que ya estaba en disco al exportar.

## Exportación y lector

El botón «Exportar mis datos» escribe `Documentos\Tutor socratico\Exportaciones\<CÓDIGO>_<AAAAMMDD-HHMMSS>.zip`
con cuatro archivos:

- `LEEME.txt`: en claro, explica qué es y que se sube a la carpeta del profesor.
- `paquete.json`: en claro, `formato` (`tutor-paquete-1`) y `huella_llave` (SHA-256 corto de la llave
  pública con la que se cifró).
- `manifiesto.tsd`: sellado. Trae el código, la instalación, las versiones del programa y del modelo, la hora
  de exportación, el número de lotes, los bytes incompletos al final del archivo, los rangos de `seq` por
  arranque y el SHA-256 de `eventos.tsd`.
- `eventos.tsd`: copia completa del archivo del alumno. Por eso cada exportación incluye las anteriores y
  basta con subir la más reciente.

`python -m tools.abrir_paquetes` abre los .zip con la llave privada y deja en la carpeta de salida:
`eventos.jsonl` y `eventos.csv` (todos los eventos, sin duplicados entre exportaciones y equipos),
`sesiones_uso.csv`, `intentos.csv`, `turnos.csv`, `teclas.csv`, `anotaciones.csv`, `equipos.csv`,
`cuestionarios.csv`, `integridad.csv`, `diccionario.csv` y `LEEME.md`. Las tablas van en UTF-8 con BOM,
separadas por comas. `diccionario.csv` define cada columna (tipo, unidad y descripción).
En los CSV, un texto que empieza con `=`, `+`, `-`, `@`, tabulador o retorno de carro (y no es un número)
lleva un apóstrofo al inicio para que Excel no lo ejecute como fórmula; `eventos.jsonl` guarda el texto original.

`integridad.csv` trae una fila `paquete` por cada .zip y una fila por cada problema: formato, llave,
manifiesto, SHA-256 que no coincide, número de lotes distinto, lote ilegible, bytes sobrantes, código
distinto, eventos en conflicto, huecos de `seq` y eventos que faltan al final. El lector termina bien aunque
haya problemas: los reporta, no los corrige.
