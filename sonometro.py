import pyaudio
import numpy as np
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import os
import time  # Asegúrate de importar time al inicio de tu archivo
from queue import Queue, Empty  # Asegúrate de importar Queue y Empty

# Asumiendo que en alguna parte de tu código inicializas event_queue así:
event_queue = Queue()
# Configuración del audio
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
CHUNK = 1024
UMBRAL = 70  # Umbral de nivel de sonido para determinar si alguien está gritando
ALERT_DURATION = 3000  # Duración de la alerta en milisegundos
GROWTH_RATE = 1.5  # Factor de crecimiento del rectángulo
PERIMETER_WIDTH_PERCENT = 5  # Porcentaje del ancho de la pantalla para el perímetro

# Inicializar PyAudio
audio = pyaudio.PyAudio()

# Archivo para guardar la selección del micrófono
CONFIG_FILE = "microphone_config.txt"
# Variable global para el tamaño del rectángulo
rect_size = 10
# Función para obtener la lista de dispositivos de entrada de audio
def get_input_devices():
    input_devices = []
    for i in range(audio.get_device_count()):
        device_info = audio.get_device_info_by_index(i)
        if device_info['maxInputChannels'] > 0:
            input_devices.append((i, device_info['name']))
    return input_devices

# Función para obtener el nivel de sonido
def get_sound_level(stream):
    data = np.frombuffer(stream.read(CHUNK), dtype=np.int16)
    peak = np.average(np.abs(data)) * 2
    level = int(20 * np.log10(peak))
    return level


# Función para mostrar una alerta visual inicial
def show_visual_alert():
    overlay = tk.Toplevel(root)
    overlay.attributes("-fullscreen", True)
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", 0.5)  # Transparencia del rectángulo
    overlay.configure(bg="")

    screen_width = overlay.winfo_screenwidth()
    screen_height = overlay.winfo_screenheight()
    
    # Calcular el tamaño del borde como el 10% del menor entre el ancho y el alto de la pantalla
    border_size = min(screen_width, screen_height) * 0.1
    rect_size = border_size  # Actualizar rect_size para usarlo en el dibujo del perímetro

    # Calcular el ancho y el alto del perímetro para que ocupe el 10% del borde de la pantalla
    perimeter_width = int(screen_width * 0.9)  # Ancho del perímetro ajustado
    perimeter_height = int(screen_height * 0.9)  # Alto del perímetro ajustado

    # Ajustar la posición de las líneas para que el rectángulo ocupe el 10% del borde
    offset_x = (screen_width - perimeter_width) / 2
    offset_y = (screen_height - perimeter_height) / 2

    # Crear líneas para el perímetro ajustado
    top_line = tk.Frame(overlay, bg="red", width=perimeter_width, height=rect_size)
    top_line.place(x=offset_x, y=offset_y)
    bottom_line = tk.Frame(overlay, bg="red", width=perimeter_width, height=rect_size)
    bottom_line.place(x=offset_x, y=screen_height - offset_y - rect_size)
    left_line = tk.Frame(overlay, bg="red", width=rect_size, height=perimeter_height)
    left_line.place(x=offset_x, y=offset_y)
    right_line = tk.Frame(overlay, bg="red", width=rect_size, height=perimeter_height)
    right_line.place(x=screen_width - offset_x - rect_size, y=offset_y)

    return overlay
# Función para actualizar el tamaño del rectángulo de alerta
def update_visual_alert(overlay, level):
    screen_width = overlay.winfo_screenwidth()
    screen_height = overlay.winfo_screenheight()
    
    # El nivel de ruido determina qué tan lejos del borde comienza el rectángulo
    # Un nivel más alto de ruido significa un rectángulo más grande, más cerca del centro
    offset_percentage = max(0, min(1, level / 100))  # Asegurar que esté entre 0 y 1
    offset_x = screen_width * offset_percentage * 0.5  # Ajustar para que el rectángulo crezca hacia el centro
    offset_y = screen_height * offset_percentage * 0.5

    # Calcular el nuevo tamaño del rectángulo
    new_width = screen_width - (2 * offset_x)
    new_height = screen_height - (2 * offset_y)

    # Actualizar el tamaño y la posición de las líneas del perímetro
    for child in overlay.winfo_children():
        child.place_forget()  # Remover la posición actual de los widgets

    # Crear nuevas líneas ajustadas
    top_line = tk.Frame(overlay, bg="red", width=new_width, height=rect_size)
    top_line.place(x=offset_x, y=offset_y)
    bottom_line = tk.Frame(overlay, bg="red", width=new_width, height=rect_size)
    bottom_line.place(x=offset_x, y=screen_height - offset_y - rect_size)
    left_line = tk.Frame(overlay, bg="red", width=rect_size, height=new_height - 2 * rect_size)
    left_line.place(x=offset_x, y=offset_y + rect_size)
    right_line = tk.Frame(overlay, bg="red", width=rect_size, height=new_height - 2 * rect_size)
    right_line.place(x=screen_width - offset_x - rect_size, y=offset_y + rect_size)
    
# Función para ocultar la alerta visual
def hide_visual_alert(overlay):
    overlay.destroy()

# Función para iniciar el monitoreo del sonido
# Función para iniciar el monitoreo del sonido
def start_monitoring(mic_index):
    monitoring = True  # Definir monitoring en el ámbito de start_monitoring
    def monitor_sound(stream):
        alert_active = False
        overlay = None
        rect_size = 10
        last_level = 0

        while True:
            level = get_sound_level(stream)
            if level > UMBRAL:
                if not alert_active:
                    alert_active = True
                    event_queue.put("show_alert")
                else:
                    rect_size = min(rect_size * GROWTH_RATE, 0.5)  # Ajuste para limitar el crecimiento
                    event_queue.put(("update_alert", rect_size))
            elif alert_active:
                if last_level > UMBRAL:
                    rect_size = min(rect_size * GROWTH_RATE, 0.5)  # Ajuste para limitar el crecimiento
                    event_queue.put(("update_alert", rect_size))
                else:
                    alert_active = False
                    event_queue.put("hide_alert")
            last_level = level

    stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, input_device_index=mic_index, frames_per_buffer=CHUNK)

    # Iniciar el hilo de monitoreo de sonido
    monitor_thread = threading.Thread(target=monitor_sound, args=(stream,), daemon=True)
    monitor_thread.start()

    overlay = None  # Variable para almacenar la referencia al overlay

    # Función para manejar la alerta visual en el hilo de la interfaz gráfica
    def handle_visual_alert():
        nonlocal overlay
        while True:
            event = event_queue.get()
            if event == "show_alert":
                overlay = show_visual_alert()
            elif event == "hide_alert":
                if overlay:
                    overlay.destroy()
                    overlay = None  # Limpiar la referencia
            elif isinstance(event, tuple) and event[0] == "update_alert":
                if overlay:
                    update_visual_alert(event[1])

    # Iniciar el hilo para manejar la alerta visual
    visual_thread = threading.Thread(target=handle_visual_alert, daemon=True)
    visual_thread.start()

    # Función para detener el monitoreo
    def stop_monitoring():
        nonlocal monitoring
        monitoring = False
        stream.stop_stream()
        stream.close()

    return stop_monitoring

# Función para guardar el último dispositivo seleccionado
def save_last_selected_device(device_index):
    with open(CONFIG_FILE, "w") as file:
        file.write(str(device_index))

# Función para cargar el último dispositivo seleccionado
def load_last_selected_device():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            try:
                return int(file.read().strip())
            except ValueError:
                return None
    return None

# Configuración de la interfaz gráfica
root = tk.Tk()
root.title("Sonómetro en Segundo Plano")
root.geometry("400x300")  # Tamaño de la ventana ajustado

frame = tk.Frame(root, padx=20, pady=20)
frame.pack(expand=True, fill='both')

label = tk.Label(frame, text="Seleccione el micrófono:", font=("Arial", 14))
label.pack(pady=10)

# Crear un combo box para seleccionar el dispositivo de entrada
devices = get_input_devices()
device_names = [name for index, name in devices]
device_var = tk.StringVar()

last_selected_device = load_last_selected_device()
if last_selected_device is not None and last_selected_device < len(device_names):
    device_var.set(device_names[last_selected_device])
else:
    device_var.set(device_names[0])

device_combo = ttk.Combobox(frame, textvariable=device_var, values=device_names, state='readonly', font=("Arial", 12))
device_combo.pack(pady=10, fill='x')

def on_select_device():
    selected_device_name = device_var.get()
    selected_device_index = next(index for index, name in devices if name == selected_device_name)
    start_monitoring(selected_device_index)

# Botón para iniciar el monitoreo
start_button = tk.Button(frame, text="Iniciar Monitoreo", command=on_select_device, font=("Arial", 12))
start_button.pack(pady=20)

label_status = tk.Label(frame, text="", font=("Arial", 14))
label_status.pack(pady=10)
label = tk.Label(frame, text="", font=("Arial", 14))
label.pack(pady=10)

root.mainloop()

# Cerrar PyAudio al cerrar la aplicación
audio.terminate()
