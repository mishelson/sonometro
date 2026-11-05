# Sonometro: tu voz, en equilibrio

Indicador visual local para Windows: cuando superas tu volumen habitual,
los bordes de la pantalla pasan de amarillo a naranja y rojo, creciendo
suavemente hacia dentro, como un efecto de dano en un videojuego.
Con silencio o voz normal, los bordes permanecen ocultos.

## Instalar y ejecutar

Requiere Python 3.10 o posterior y Windows de 64 bits recomendado.
Validado con Python 3.13; PyAudio necesita una rueda compatible con tu
version de Python o una instalacion de PortAudio para compilar.

En PowerShell, desde esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe .\sonometro.py
```

No necesitas activar el entorno. Si Windows bloquea el microfono, habilita
el acceso para aplicaciones de escritorio en Configuracion > Privacidad
y seguridad > Microfono.

## Usar

1. Selecciona el microfono. El nombre incluye su API e indice para distinguir
   entradas repetidas. Preferimos la entrada de WASAPI si esta disponible.
2. Pulsa **Iniciar**. Sin calibracion veras dBFS, pero no se activaran alertas.
3. Pulsa **Calibrar mi voz**: silencio durante 3 segundos, preparacion de
   2 segundos y habla normal durante 6 segundos. Mantente a tu distancia
   habitual. Si hay saturacion o mucho ruido, se solicitara repetir.
4. Ajusta la tolerancia: menos tolerancia avisa antes. Los tres niveles
   comienzan en +3, +7 y +12 dB respecto a tu voz normal, desplazados por
   el ajuste de tolerancia. Hay suavizado, histeresis y persistencia para
   evitar destellos por picos aislados.
5. Ajusta intensidad y profundidad. **Vista previa** muestra el efecto
   durante 5 segundos sin necesitar captura ni calibracion, incluso con
   las alertas desactivadas. No bloquea clics ni toma el foco.
6. **Detener** libera el microfono. Cerrar la ventana tambien detiene
   la captura; no queda ejecutandose en la bandeja del sistema.

El medidor usa verde para el nivel normal, y texto ademas del color.
La linea blanca marca la referencia personal. La escala del medidor es
de -80 a 0 dBFS; las lecturas mas bajas siguen apareciendo en el texto.

Los ajustes se guardan en `sonometro_config.json` junto al programa.
La seleccion anterior de `microphone_config.txt` se interpreta como
indice real de dispositivo, no posicion en la lista; no se elimina.
Si una identidad guardada es ambigua o desaparece, vuelve a seleccionar
y calibrar. Cambiar de microfono invalida la calibracion y referencia SPL.
Un archivo de configuracion invalido se notifica; al guardar se reemplaza
por los ajustes nuevos validados.

## Decibelios: que significa cada cifra

- **dBFS:** nivel digital RMS respecto al maximo que admite el microfono.
  0 dBFS es el limite; numeros negativos son normales. El silencio se
  representa con un piso de -100 dBFS, sin errores numericos.
- **Voz normal:** referencia aprendida de tu voz y del ruido de fondo.
  No existe un valor universal de dBFS equivalente a hablar normal.
- **dB SPL estimados:** solo se muestran tras pulsar **Referencia dB SPL**
  e introducir una lectura real de un sonometro externo junto al microfono
  mientras hablas estable. El programa toma una lectura digital reciente
  al confirmar y calcula un desfase. No introduzcas una cifra generica.
  Escribe `borrar` en ese dialogo para quitar la referencia.

La aproximacion con un solo punto no sustituye un sonometro calibrado:
ponderacion del instrumento, respuesta del microfono, ganancia automatica,
cancelacion de ruido, frecuencia y distancia pueden alterar el resultado.
Recalibra al cambiar ganancia, posicion o procesamiento de audio; la app
no puede detectar automaticamente todos esos cambios. No es una herramienta
certificada de salud, seguridad o diagnostico.

El programa mide **todo el sonido captado**, no reconoce exclusivamente
tu voz. Musica, otras personas y ruido pueden activar los bordes.

## Privacidad y limites

El audio se procesa en memoria, localmente. No se guarda, transcribe ni
envia. Solo se conservan ajustes y referencias numericas. El microfono
permanece abierto exclusivamente al iniciar el monitoreo.

El efecto se muestra en el monitor principal con ventanas nativas sin
activacion y que dejan pasar clics. No admite varios monitores ni garantiza
superposicion sobre juegos en pantalla completa exclusiva o escritorios
protegidos de Windows. Puedes desactivar los bordes y conservar el medidor.

Errores de captura, permisos o desconexion se muestran en la ventana y
en la consola. Si un controlador bloquea la parada, se impide iniciar otra
captura y se avisa en lugar de cerrar el flujo desde un hilo incorrecto.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Las pruebas usan audio sintetico y dobles de captura: no abren tu microfono.
Cubren RMS, silencio, extremos int16, incremento de +6.02 dB al duplicar
amplitud, calibracion, limites, histeresis, ajustes y liberacion de recursos.
En Windows tambien abren una interfaz de prueba con dispositivos simulados
y comprueban geometria, ocultamiento, estabilidad del foco y paso de clics
de los bordes. Estas pruebas muestran el efecto brevemente en tu pantalla.

Para comprobar tu equipo: usa Vista previa mientras haces clic en otra
aplicacion; calibra y alterna voz normal/elevada; detiene e inicia de nuevo;
desconecta el microfono durante captura; comprueba el efecto despues de
cambiar resolucion o escala. Una cifra SPL solo se valida con instrumento.
