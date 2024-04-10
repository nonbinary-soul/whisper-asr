#!/usr/bin/python3

# ------------------------------------------------------
# LIBRERÍAS
# ------------------------------------------------------
# picovoice
import pvporcupine
import numpy as np

# gestión de audio
import pyaudio
import wave

# ejecución de comandos en consola
import subprocess
import os

# para la detección de voz
from tuning import Tuning
import usb.core
import usb.util

from multiprocessing import Process, Queue, Event
# ------------------------------------------------------
# PORCUPINE 
# ------------------------------------------------------
access_key = "YhpQKilovfhz5/6XxLxq+Wmiz45bbtBUVruBptzYOdHqfyHhaUTpLw=="
ppn_path = "./hello-shadow_en_linux_v3_0_0/hello-shadow_en_linux_v3_0_0.ppn"

# instancia
porcupine = pvporcupine.create(access_key=access_key, keyword_paths=[ppn_path])

# ------------------------------------------------------
# PYAUDIO 
# ------------------------------------------------------
RESPEAKER_RATE = 16000
RESPEAKER_CHANNELS = 1  # Cambia según tus ajustes
RESPEAKER_WIDTH = 2
FORMAT = pyaudio.paInt16  # calidad de audio. probar float32 o float64
OUTPUT_FILENAME = "record.wav"

# instancia
audio = pyaudio.PyAudio()

# imprimir los dispositivos de audio disponibles
# num_devices = audio.get_device_count()

# print("Lista de dispositivos de audio disponibles:")
# for i in range(num_devices):
#     device_info = audio.get_device_info_by_index(i)
#     device_name = device_info["name"]
#     print(f"Dispositivo {i}: {device_name}")

# búsqueda del índice del dispositivo de audio
target_device_name = "ReSpeaker 4 Mic Array (UAC1.0): USB Audio" # nombre del dispositivo
target_device_index = 0
info = audio.get_host_api_info_by_index(0)
numdevices = info.get('deviceCount')

for i in range(numdevices):
    device_info = audio.get_device_info_by_host_api_device_index(0, i)
    if device_info.get('maxInputChannels') > 0:
        if target_device_name in device_info.get('name'):
            target_device_index = i

# Apertura del flujo de audio si se encontró un dispositivo de audio
if target_device_index is not None:
    stream = audio.open(
        format=audio.get_format_from_width(RESPEAKER_WIDTH),
        channels=RESPEAKER_CHANNELS,
        rate=RESPEAKER_RATE,
        input=True,
        input_device_index=target_device_index
    )
else:
    print(f"No se encontró el dispositivo {target_device_name}.")

# ------------------------------------------------------
# PAUSAS Y SILENCIOS
# ------------------------------------------------------
novoice_counter = 0
silence_detected = Event()
pause_detected = False
SILENCE_DURATION = 2  # duración de silencio requerida para finalizar la grabación
PAUSE_DURATION = 0.5  # duración de pausa requerida para transcribir

# ------------------------------------------------------
# GESTIÓN DE TRANSCRIPCIÓN
# ------------------------------------------------------
is_recording = False
record_queue = Queue()


# FUNCIÓN para generar archivo .wav
def generate_wav(file_name, record):
    with wave.open(file_name, 'wb') as wf:
        wf.setnchannels(RESPEAKER_CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RESPEAKER_RATE)
        wf.writeframes(b''.join(record))


# FUNCIÓN para transcribir audio usando whisper
def call_whisper(audio_file): 
    command = ["whisper", audio_file, "--model", "small", "--language", "Spanish"]
    subprocess.run(command, check=True)


def transcript(frame): 
    generate_wav(OUTPUT_FILENAME, frame)
    call_whisper(OUTPUT_FILENAME)
    subprocess.run(["cat", "record.txt"], stdout=open("prompt-llama.txt", "a"))


# FUNCIÓN del hilo de transcripción
def manage_transcription():

    while not silence_detected.is_set():
        if not record_queue.empty():
            frame = record_queue.get()
            transcript(frame)

    # vaciar la cola antes de terminar
    while not record_queue.empty():
        frame = record_queue.get()
        transcript(frame)
   
# ------------------------------------------------------
# BORRADOS
# ------------------------------------------------------


# FUNCIÓN para finalizar la grabación y limpiar los recursos
def terminate(): 
    stream.stop_stream()
    stream.close()
    audio.terminate()
    porcupine.delete()


# FUNCIÓN para limpiar nuestro directorio actual
def delete_llama_prompt(): 
    # archivo input-llama.txt
    if os.path.exists("prompt-llama.txt"):
        subprocess.run(["rm", "prompt-llama.txt"])


def main(): 

    global novoice_counter, is_recording, pause_detected

    # detector de voz
    mic_tunning = Tuning(usb.core.find(idVendor=0x2886, idProduct=0x0018))
    record = []  # grabación tras la wake word

    # limpiar el directorio antes de comenzar
    delete_llama_prompt()

    try:
        silence_detected.clear()
        while not silence_detected.is_set():
            # verificar si es la wake word
            pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = np.frombuffer(pcm, dtype=np.int16)
            
            # Procesar el audio para detectar la wake word
            keyword_index = porcupine.process(pcm)

            # Si se detecta la palabra clave, iniciar la grabación 
            if keyword_index >= 0:
                print(f"Escuchando...")
                # limpiar el directorio antes de comenzar
                delete_llama_prompt()
                # Vaciamos el contenido de la grabación hasta ahora
                record.clear()
                # Iniciamos grabación
                is_recording = True

            if is_recording:
                record.append(pcm.copy())

            if mic_tunning.is_voice(): # si se detecta voz
                novoice_counter = 0  # reiniciamos la captación de silencio
                pause_detected = False
            else:  # sino
                if is_recording: 
                    novoice_counter += 1
                    
                    # Verificar si se ha alcanzado la pausa especificada
                    if novoice_counter >= PAUSE_DURATION*64 and not pause_detected:
                        print("PAUSA")
                        pause_detected = True
                        # encolar el fragmento de audio para su transcripción
                        record_queue.put(record.copy())
                        record.clear()

                    # Verificar si se ha alcanzado la duración de silencio requerida
                    if novoice_counter >= SILENCE_DURATION*64:
                        print("SILENCIO")
                        silence_detected.set()
                        is_recording = False
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    transcription_process = Process(target=manage_transcription)
    transcription_process.start()
    main()
    transcription_process.join()
    terminate()

    