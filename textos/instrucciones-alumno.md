# Guía de instalación y uso del Tutor Socrático

El Tutor Socrático es un programa para practicar problemas de circuitos eléctricos. Corre en tu computadora y no necesita internet para usarse.

---

## 1. Descarga del instalador

1. Entra al enlace de Google Drive que te dio tu profesor.
2. Descarga el archivo `TutorSocratico-Instalador-1.0.0.exe`.
3. Guárdalo en tu carpeta de Descargas o en el Escritorio.

---

## 2. Instalación en Windows

1. Haz doble clic en `TutorSocratico-Instalador-1.0.0.exe`.
2. **Aviso de SmartScreen.** El instalador no tiene firma digital, así que Windows puede mostrar el mensaje *«Windows protegió su PC»*:
   - haz clic en **Más información**;
   - haz clic en **Ejecutar de todas formas**.
3. No se piden permisos de administrador: el programa se instala sólo para tu usuario, en `%LOCALAPPDATA%\Programs\Tutor Socratico`.
4. Sigue el asistente. Ofrece crear un acceso directo en el Escritorio; el del Menú Inicio se crea siempre.
5. Al terminar, el asistente te deja abrir el programa en ese momento.

---

## 3. Entrar con tu código

1. Al abrir el programa aparece una pantalla con el campo **Código de acceso** y el botón **Entrar**.
2. Escribe el código que te dio tu profesor (tiene la forma `TS-XXXX-C`, por ejemplo `TS-4B8K-3`) y haz clic en **Entrar**.
3. La primera vez que entras en esa computadora aparece el **Aviso de participación en un estudio**. Léelo y elige una de dos opciones:
   - **Acepto participar:** el tutor registra lo que haces dentro de su ventana, como explica el aviso.
   - **Usar el tutor sin participar:** el tutor funciona igual, pero no se registra nada.

En la parte de arriba de la ventana se ve tu código y el estado del tutor. Mientras diga *«Preparando al tutor…»* el modelo de lenguaje está cargando; cuando diga *«Tutor listo»* ya puedes conversar con él. Si dice *«Tutor en modo básico»*, el tutor sigue funcionando con mensajes más simples.

---

## 4. Uso del tutor

1. **Elige un modo y un problema.** En la pantalla principal están los modos *Aprender*, *Practicar*, *Verificar* y *Simulador*, con una línea que explica cada uno, y debajo los temas: Ley de Ohm, Serie y paralelo, Método de mallas, Método de nodos, Divisor de voltaje y corriente, Superposición y Thévenin y Norton. Cada problema marca si está *En curso* o *Terminado* en el modo elegido.
2. **Cuestionarios.** Si tu profesor activó alguno, aparece antes o después de ciertos problemas, una pregunta a la vez, con el botón **Continuar**.
3. **Conversa con el tutor.**
   - Escribe en el cuadro de abajo y presiona **Enter** o el botón **Enviar** (con **Mayús+Enter** pasas a otra línea).
   - Si no sabes cómo seguir, usa el botón **Pista**.
   - También puedes hacerle preguntas sobre la teoría.
4. **Confianza.** En algunos pasos, antes de revisar tu respuesta, el tutor pregunta *«¿qué tan seguro estás?»*. Elige del 1 al 5 (1 = nada, 5 = totalmente) o **Prefiero no decir**.
5. **Salir.** El botón **Cerrar sesión** (arriba a la derecha) guarda tu avance y vuelve a la pantalla del código. Si dejas la ventana sin tocarla 30 minutos, la sesión se cierra sola. Un problema a medias se retoma donde lo dejaste.

---

## 5. Exportar tus datos

Sólo si aceptaste participar. Hazlo cuando tu profesor lo pida.

1. En la barra de arriba, haz clic en **Exportar mis datos**.
2. Aparece el aviso *«Tus datos quedaron exportados»* con la ruta del archivo.
3. El archivo se llama como tu código y la fecha y hora, por ejemplo `TS-4B8K-3_20261014-153045.zip`, y queda en `Documentos\Tutor socratico\Exportaciones`.
4. El programa no abre la carpeta por su cuenta: haz clic en **Abrir carpeta** para verla.
5. Sube ese `.zip` a la carpeta compartida que te indicó tu profesor. No hace falta abrirlo: va cifrado y sólo el equipo de investigación puede leerlo.

Puedes exportar las veces que quieras; cada archivo trae todo lo anterior.

---

## 6. Problemas comunes

- **«El tutor ya está abierto».** Busca su ventana en la barra de tareas. Si no la ves, abre el Administrador de tareas (Ctrl+Mayús+Esc), termina el proceso `TutorSocratico.exe` y abre el programa de nuevo.
- **Desinstalar.** Ve a *Configuración > Aplicaciones* o usa el desinstalador del Menú Inicio. Se conservan tus datos (en `%LOCALAPPDATA%\TutorSocratico\datos`) y las exportaciones que ya hiciste.
