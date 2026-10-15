# Tutor Socrático · Circuitos Eléctricos

Tutor que guía al estudiante con preguntas y pistas graduadas (escalera de andamiaje 0–5) para
resolver problemas de circuitos de CD: Ley de Ohm y potencia, serie/paralelo, mallas (con mallas
fantasma y supermallas), nodos (con nodos fantasma y supernodos), divisor de voltaje y de corriente,
superposición y equivalentes de Thévenin y Norton. Sigue el **método de formato**
que se usa en clase y tiene costo **$0**. Se está convirtiendo en una aplicación de escritorio para
Windows 10/11 que funcione sin internet.

## Idea central

El tutor conoce la solución exacta de cada circuito, pero no la entrega: revisa cada paso del
alumno contra esa solución, identifica el error concreto y decide la mínima ayuda necesaria.

| Capa | Qué hace |
|---|---|
| **Motor de circuitos** (`solver.py`, `analysis.py`) | Resuelve de forma exacta cualquier circuito DC resistivo (análisis nodal modificado con SymPy) y construye las ecuaciones de referencia de mallas y nodos, con los nombres de clase: ecuación directa, ecuación auxiliar, ecuación real, supernodo. |
| **Diagnóstico** (`diagnose.py`, `reduction.py`) | Compara cada ecuación o valor del alumno con la referencia y con las ecuaciones que producirían los errores típicos: sumar corrientes en la rama compartida, invertir la polaridad de una fuente, usar R en vez de 1/R, sumar resistencias en paralelo, combinar resistencias que no están ni en serie ni en paralelo… |
| **Política pedagógica** (`engine.py`, `data/banco_errores.csv`) | Plan de pasos, escalera de ayuda, modos (Aprender, Practicar, Verificar, Simulador), solicitud de justificación, verificación con balance de potencias o simulador, reflexión final e indicadores de la interacción. |
| **Modelo de lenguaje** (`tutor/llm.py`) | Cliente de un modelo que corre en la misma computadora (`llama-server`, de llama.cpp). Sólo redacta los mensajes; no decide si una respuesta es correcta. En desarrollo. |

Las decisiones pedagógicas son deterministas: todos los alumnos reciben la misma intervención ante
el mismo error, lo que permite comparar resultados en la investigación.

## Cómo sigue el método de clase

- **Mallas:** el alumno cuenta y describe las mallas, las clasifica (real, fantasma o supermalla),
  identifica la rama compartida y su corriente neta, plantea las ecuaciones en el orden de clase
  (directa o auxiliar primero, después la real) y puede escribirlas en forma simbólica,
  `I1(R1 + R2) − I2R2 = VX − VY`, o con valores, `I1(10 + 20) − I2(20) = 100 − 100`.
  Acepta sentido horario (FMR) o antihorario (CMR) mientras todas giren igual, y la notación
  `ia, ib, ic`.
- **Nodos:** clasificación (real, fantasma, supernodo), ecuación directa, ecuación de la fuente del
  supernodo y ecuación complementaria, en el formato `V1(1/4 + 1/2) − V2(1/2) = 5`.
- **Serie/paralelo:** reducción paso a paso con equivalentes intermedios R_T1, R_T2…; cada paso
  correcto se acepta aunque no sea el resultado final. Reconoce resistencias en corto.
- **Tabla de resultados:** ecuación de rama, I, V y P de cada resistencia.
- **Verificación:** balance de potencias PE = PC, sustitución o comparación con PhET/Multisim.

## Banco de problemas

Cada problema es un archivo de texto en `problemas/` que se puede editar sin programar:

```
TITULO: Mallas · Caso 1: malla real
TEMA: mallas
METODO: mallas
NIVEL: básico
FUENTE: Material de clase, Tema 4
ENUNCIADO: ...
PIDE: VIP
RESPUESTA: I1=1.82 I2=2.73

VX a 0 100          fuente de voltaje: el primer nodo es la terminal +
R1 a b 10           resistencia en Ω
R2 b m 20
VY m 0 100
R3 b 0 30
MALLA 1: VX R1 R2 VY    mallas como están dibujadas, en sentido horario
MALLA 2: VY R2 R3
```

- `PIDE`: `VIP` (tabla de V, I y P en cada resistencia), `Req VT` (la que ve la fuente),
  `Req A-0`, `I R2`, `V R1`, `P R3`, separados por `;`.
- `RESPUESTA`: resultados publicados; las pruebas verifican que el motor los reproduce.
- `NOTA`: comentario para el profesor; no se muestra al alumno.
- `IMAGEN`: dibujo del circuito (SVG o PNG en `problemas/`), que el alumno ve junto al enunciado.
  Los del banco se generan con `python tools/dibujos.py` (requiere `pip install schemdraw`); el
  script compara cada dibujo con la tabla del problema, incluida la polaridad de las fuentes, y no
  lo genera si no coinciden.
- Las **mallas se declaran** porque una lista de elementos no dice cómo está dibujado el
  circuito: el mismo circuito admite varios dibujos, con distintas ventanas. El tutor valida que
  cada malla sea un lazo cerrado, que sean independientes, que haya B − N + 1 y que todas puedan
  girar en el mismo sentido.

Los alumnos también pueden capturar un problema propio con el mismo formato.

## Datos de la investigación

Cada alumno entra con un código. Si acepta el aviso de participación, el tutor registra lo que hace
dentro de su ventana: la conversación, cómo escribe cada mensaje, la confianza que declara, las
decisiones del motor y del modelo, y los datos técnicos del equipo. Quien no acepta usa el tutor igual,
sin que se registre nada. El esquema completo está en [`docs/DATOS.md`](docs/DATOS.md).

Los eventos se cifran desde que se escriben, con una llave pública que viaja con el programa; sólo quien
tiene la llave privada puede leerlos. Con «Exportar mis datos», el alumno genera un `.zip` en
`Documentos\Tutor socratico\Exportaciones` (cada exportación incluye las anteriores) y lo sube a la carpeta
que le indique su profesor. Para el equipo de investigación:

- `python -m tools.llaves generar --salida <carpeta fuera del repositorio>` crea el par de llaves, y
  `python -m tools.llaves verificar <llave_privada.tsk>` comprueba la contraseña.
- `python -m tools.abrir_paquetes --llave <llave_privada.tsk> --entrada <carpeta con .zip> --salida <carpeta>`
  revisa la integridad de los paquetes y escribe las tablas (CSV, JSONL) y su diccionario de datos.

## Instalación

El tutor se reparte como un instalador de Windows 10/11 de 64 bits que no pide permisos de
administrador y se instala sólo para el usuario. La guía para el alumno (descarga, aviso de
SmartScreen, entrada con el código y exportación) está en
[`textos/instrucciones-alumno.md`](textos/instrucciones-alumno.md). Para revisar un equipo,
`TutorSocratico.exe --autodiagnostico` prueba el motor y el modelo sin abrir la ventana y deja el
resultado en `%LOCALAPPDATA%\TutorSocratico\autodiagnostico.txt`.

## Construcción

`python -m tools.construir` corre las pruebas, empaqueta el programa, agrega llama.cpp y el modelo, y
genera el instalador con Inno Setup. Requisitos, llave del estudio y pasos en
[`docs/CONSTRUIR.md`](docs/CONSTRUIR.md).

## Pruebas

```bash
python -m pytest -q
```

- `tests/test_material.py`: cada problema del banco reproduce los resultados publicados en el
  material de clase.
- `tests/test_flow.py`: diálogos completos con la notación de clase.
- `tests/test_core.py`: solver, mallas y nodos contra MNA en 40 circuitos aleatorios, diagnóstico.

## Estructura

```
tutor/circuit.py     netlist, mallas declaradas, validación
tutor/solver.py      MNA exacto, resistencia equivalente, magnitudes pedidas
tutor/analysis.py    ecuaciones de mallas y nodos; tipos de malla y de nodo
tutor/mathparse.py   lectura de ecuaciones y valores: unidades, prefijos, ia/ib, forma simbólica
tutor/diagnose.py    ecuación o valor del alumno -> código de error
tutor/reduction.py   reducción serie/paralelo paso a paso
tutor/engine.py      plan de pasos, escalera 0–5, modos, indicadores
tutor/llm.py         cliente de modelos de lenguaje
tutor/privacidad.py  eliminación de información personal en texto libre
tutor/problems.py    banco de problemas
web/                 página, estilos y scripts de la interfaz
problemas/           banco de problemas y sus dibujos
tools/               dibujos de los circuitos
data/                banco de errores (editable en Excel)
```

## Límites conocidos

- Sólo CD resistivo. Las fuentes dependientes y la CA quedan para la etapa 2.
- La conversión delta-estrella se acepta por su resultado final, sin revisar los pasos intermedios.
- Las respuestas conceptuales abiertas se evalúan con palabras clave y son permisivas a propósito:
  en la duda, el tutor acepta y pide justificación.
