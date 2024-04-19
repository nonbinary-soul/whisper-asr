#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
#    Copyright (C) 2024 by YOUR NAME HERE
#
#    This file is part of RoboComp
#
#    RoboComp is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    RoboComp is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with RoboComp.  If not, see <http://www.gnu.org/licenses/>.
#

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QApplication
from rich.console import Console
from genericworker import *
import interfaces as ifaces

from multiprocessing import Process, Queue, Event

# picovoice
import pvporcupine
import numpy as np

# audio managment
import pyaudio
import wave

# voice detection with respeaker
from tuning import Tuning
import usb.core
import usb.util

# allows to execute commands
import subprocess
import os

#################################### PORCUPINE #####################################

ACCESS_KEY = "YhpQKilovfhz5/6XxLxq+Wmiz45bbtBUVruBptzYOdHqfyHhaUTpLw=="
PPN_PATH = "./src/audio-config/hello-shadow_en_linux_v3_0_0/hello-shadow_en_linux_v3_0_0.ppn"

############################### AUDIO DEVICE CONFIG ################################

####### initial configuration
RESPEAKER_RATE = 16000
RESPEAKER_CHANNELS = 1  # Cambia según tus ajustes
RESPEAKER_WIDTH = 2
FORMAT = pyaudio.paInt16  # calidad de audio. probar float32 o float64
OUTPUT_FILENAME = "record.wav"

# instancia
audio = pyaudio.PyAudio()

# print available audio devices 
# num_devices = audio.get_device_count()

# print("Lista de dispositivos de audio disponibles:")
# for i in range(num_devices):
#     device_info = audio.get_device_info_by_index(i)
#     device_name = device_info["name"]
#     print(f"Dispositivo {i}: {device_name}")

# searching its index
target_device_name = "ReSpeaker 4 Mic Array (UAC1.0): USB Audio" # our device name
target_device_index = 0
info = audio.get_host_api_info_by_index(0)
numdevices = info.get('deviceCount')

for i in range(numdevices):
    device_info = audio.get_device_info_by_host_api_device_index(0, i)
    if device_info.get('maxInputChannels') > 0:
        if target_device_name in device_info.get('name'):
            target_device_index = i

# opening audio stream if the device was found
if target_device_index is not None:
    stream = audio.open(
        format=audio.get_format_from_width(RESPEAKER_WIDTH),
        channels=RESPEAKER_CHANNELS,
        rate=RESPEAKER_RATE,
        input=True,
        input_device_index=target_device_index
    )
else:
    print(f"{target_device_name} was not found.")

############################### SILENCES AND PAUSES ################################
SILENCE_DURATION = 2  # silence duration required to finish the program
PAUSE_DURATION = 0.5  # pause duration required to transcript a record

################################## ROBOCOMP ########################################
sys.path.append('/opt/robocomp/lib')
console = Console(highlight=False)

# if RoboComp was compiled with Python bindings you can use InnerModel in Python
# import librobocomp_qmat
# import librobocomp_osgviewer
# import librobocomp_innermodel

################################# SPECIFICWORKER ###################################

class SpecificWorker(GenericWorker):

    ############################
    # initial configuration 
    ############################
    def __init__(self, proxy_map, startup_check=False):
        super(SpecificWorker, self).__init__(proxy_map)

        # initial params definition
        self.Period = 1000
        self.novoice_counter = 0
        self.silence_detected = Event()
        self.pause_detected = False
        self.is_recording = False
        self.record_queue = Queue()
        global ACCESS_KEY, PPN_PATH  
        self.porcupine = pvporcupine.create(access_key=ACCESS_KEY, keyword_paths=[PPN_PATH])

        if startup_check:
            self.startup_check()
        else:
            self.timer.timeout.connect(self.compute)
            self.timer.start(self.Period)

    def __del__(self):
        """Destructor"""

    def setParams(self, params):
        # try:
        #	self.innermodel = InnerModel(params["InnerModelPath"])
        # except:
        #	traceback.print_exc()
        #	print("Error reading config params")
        return True


    ############################
    # transcription management
    ############################
    def generate_wav(self, file_name, record): 
        """
        Generate a WAV file with the specified file_name using the provided audio record.

        Parameters:
            file_name (str): The name of the WAV file to be generated.
            record (bytes): The audio record data to be written to the WAV file.

        Returns:
            None

        Raises:
            IOError: If there is an error writing the WAV file.

        Example:
            generate_wav("output.wav", record_data)
        """
        with wave.open(file_name, 'wb') as wf:
            wf.setnchannels(RESPEAKER_CHANNELS)
            wf.setsampwidth(audio.get_sample_size(FORMAT))
            wf.setframerate(RESPEAKER_RATE)
            wf.writeframes(b''.join(record))

    def call_whisper(self, audio_file): 
        """
        Call the Whisper speech recognition system to transcribe the audio file.

        Parameters:
            audio_file (str): The path to the audio file to be transcribed.

        Returns:
            None

        Raises:
            subprocess.CalledProcessError: If the whisper command fails or returns a non-zero exit status.

        Example:
            call_whisper("audio.wav")
        """
        command = ["whisper", audio_file, "--model", "small", "--language", "Spanish"]
        subprocess.run(command, check=True)

    def transcript(self, frame): 
        """
        Transcribe the given audio frame using the Whisper speech recognition system.

        This function generates a WAV file from the provided audio frame, transcribes it using
        the Whisper speech recognition system, and appends the transcribed text to a file named
        'prompt.txt'.

        Parameters:
            frame (bytes): The audio frame to be transcribed.

        Returns:
            None

        Example:
            transcript(frame_data)
        """
        self.generate_wav(OUTPUT_FILENAME, frame)
        self.call_whisper(OUTPUT_FILENAME)
        with open("prompt.txt", "a") as prompt_file:
            subprocess.run(["cat", "record.txt"], stdout=prompt_file)

    def manage_transcription(self):
        """
        Manage transcription of audio frames until a silence is detected.

        This function continuously processes audio frames from the self.record_queue until
        a silence is detected. It calls the transcript function on each frame.

        If the self.record_queue is not empty, it retrieves a frame and calls the transcript function.
        This loop continues until a silence is detected (self.silence_detected.is_set()).

        Before finishing, it ensures that all remaining frames in the self.record_queue are processed.

        Parameters:
            None

        Returns:
            None

        Example:
            manage_transcription()
        """
        while not self.silence_detected.is_set():
            if not self.record_queue.empty():
                frame = self.record_queue.get()
                self.transcript(frame)

        # vaciar la cola antes de terminar
        while not self.record_queue.empty():
            frame = self.record_queue.get()
            self.transcript(frame)

    def send_transcription(self):
        """
        Send the transcription message using the content of the 'prompt.txt' file.

        This function reads the content of the 'prompt.txt' file and sends it as a transcription message
        using the whisperstream proxy.

        Parameters:
            None

        Returns:
            None

        Example:
            send_transcription()
        """
        with open('prompt.txt', 'r') as file:
            message = file.read().strip()
            
        self.whisperstream_proxy.OnMessageTranscribed(message)

    def terminate(self):
        """
        Stop the audio stream and release resources.

        This function stops the audio stream, closes it, and terminates the PyAudio object.
        Additionally, it deletes the Porcupine object, releasing its resources.

        Parameters:
            None

        Returns:
            None

        Example:
            terminate()
        """
        stream.stop_stream()
        stream.close()
        audio.terminate()
        self.porcupine.delete()

    def delete_llama_prompt(self):
        """
        Delete the 'prompt.txt' file if it exists.

        This function checks if the 'prompt.txt' file exists in the current directory.
        If it exists, it deletes the file using the 'rm' command.

        Parameters:
            None

        Returns:
            None

        Example:
            delete_llama_prompt()
        """
        if os.path.exists("prompt.txt"):
            subprocess.run(["rm", "prompt.txt"])


    ############################
    # Compute
    ############################
    @QtCore.Slot()
    def compute(self):
        print('SpecificWorker.compute...')

        # clean the directory
        self.delete_llama_prompt()

        # initialize params
        self.novoice_counter = 0
        self.silence_detected = Event()
        self.pause_detected = False
        self.is_recording = False
        self.record_queue = Queue()
        global ACCESS_KEY, PPN_PATH  
        self.porcupine = pvporcupine.create(access_key=ACCESS_KEY, keyword_paths=[PPN_PATH])

        # start multiprocessing management
        transcription_process = Process(target=self.manage_transcription)
        transcription_process.start()        

        # initialize detector of Reaspeaker
        mic_tunning = Tuning(usb.core.find(idVendor=0x2886, idProduct=0x0018))
        record = []  # save the recording after the wake word has been detected

        try:
            self.silence_detected.clear()
            while not self.silence_detected.is_set():
                # take an audio fragment
                pcm = stream.read(self.porcupine.frame_length, exception_on_overflow=False)
                pcm = np.frombuffer(pcm, dtype=np.int16)
                
                # process fragment to detect the wake word
                keyword_index = self.porcupine.process(pcm)

                # if the wake word has been detected, start recording
                if keyword_index >= 0:
                    print("Listening...")
                    # delete recoring till now
                    record.clear()
                    # start recording
                    self.is_recording = True

                if self.is_recording:
                    record.append(pcm.copy()) # add audio fragment

                if mic_tunning.is_voice(): # if voice detected
                    self.novoice_counter = 0  # restart no voice detection
                    self.pause_detected = False
                else: 
                    if self.is_recording: 
                        self.novoice_counter += 1
                        
                        # check if a pause duration has been reached
                        if self.novoice_counter >= PAUSE_DURATION*64 and not self.pause_detected:
                            print("Pause")
                            self.pause_detected = True
                            # enqueue the fragment for transcription 
                            self.record_queue.put(record.copy())
                            record.clear()

                        # check if a silence duration has been reached to finish the program
                        if self.novoice_counter >= SILENCE_DURATION*64:
                            print("Silence")
                            self.silence_detected.set()
                            self.send_transcription()
                            transcription_process.join()

        except KeyboardInterrupt:
            transcription_process.join()
            self.terminate()
            pass

        return True

    def startup_check(self):
        QTimer.singleShot(200, QApplication.instance().quit)

