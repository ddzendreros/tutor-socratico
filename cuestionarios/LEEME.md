# Cuestionarios

Cada archivo `<id>.txt` de esta carpeta es un cuestionario. El repositorio no trae ninguno: los
instrumentos del estudio se agregan aquí antes de construir el programa. Sólo los ven quienes
aceptaron participar.

## Formato

```
ID: autoeficacia_inicial
TITULO: Antes de empezar
VERSION: 1
ACTIVO: si
CUANDO: primer_ingreso
INSTRUCCIONES: Contesta según lo que piensas hoy. No hay respuestas correctas.
P1 | likert5 | Puedo resolver un circuito por mallas sin ayuda. | Nada de acuerdo; Totalmente de acuerdo
P2 | opcion | ¿Qué método usas primero? | Mallas; Nodos; Reducción serie-paralelo
P3 | abierta | ¿Qué es lo que más se te dificulta de un circuito?
P4 | numero | ¿Cuántas horas a la semana estudias la materia?
```

- Archivo en UTF-8. Los renglones vacíos y los que empiezan con `#` se ignoran.
- `ID`: minúsculas, números y `_`. No puede repetirse en otro archivo.
- `TITULO`, `VERSION` (entero, desde 1), `ACTIVO` (`si` o `no`) y `CUANDO` son obligatorios;
  `INSTRUCCIONES` es opcional.
- Cada pregunta es `<id> | <tipo> | <texto>`, más las opciones cuando el tipo las lleva. Los
  identificadores de pregunta no se repiten dentro del archivo.

## Tipos de pregunta

| tipo | opciones | respuesta que se guarda |
|---|---|---|
| `likert5` | etiqueta del 1 y del 5, separadas por `;` | entero del 1 al 5 |
| `opcion` | dos o más opciones distintas, separadas por `;` | el texto de la opción elegida |
| `abierta` | ninguna | texto, de 1 a 2000 caracteres |
| `numero` | ninguna | número (acepta coma o punto decimal) |

## Cuándo aparece (`CUANDO`)

| valor | aparece |
|---|---|
| `primer_ingreso` | al entrar, si el alumno no lo ha contestado |
| `antes_de_tema:<tema>` | al abrir un problema de ese tema |
| `al_terminar_tema:<tema>` | al terminar un problema de ese tema |
| `despues_de_problemas:<N>` | cuando el alumno lleva N problemas terminados (cada problema y modo cuenta una vez) |
| `manual` | nunca por sí solo; queda reservado |

Los temas son los del campo `TEMA` del banco de problemas: hoy `ohm`, `serie_paralelo`, `mallas`,
`nodos`, `divisor` y `superposicion`. Un tema que no está en el banco es un error.

- Se contesta una pregunta a la vez, en orden, y no se puede saltar ni volver. Mientras haya un
  cuestionario a medias no se puede abrir ningún problema; si el alumno cierra el programa, lo
  retoma en la pregunta donde iba.
- Hay a lo más un cuestionario abierto a la vez. Si en el mismo momento tocan varios, va primero el
  del archivo que está antes en orden alfabético.
- Un cuestionario contestado no vuelve a aparecer. Para aplicarlo otra vez, sube su `VERSION`.
  Cambiar la versión o desactivarlo descarta lo que un alumno tenía a medias (lo ya contestado sí
  queda registrado).

## Errores

Un archivo con un error no se carga: el registro del programa (`registro-app.log`) dice el archivo, el
renglón y el problema, y el tutor sigue funcionando sin ese cuestionario.
