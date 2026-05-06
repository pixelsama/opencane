import machine
from usr import parameter as pa
import audio
import utime
import _thread
import sys_bus
import request
from queue import Queue



class Player:
    def __init__(self):
        self.PA_PIN = pa.PA_PIN
        self.PA = machine.Pin(self.PA_PIN, machine.Pin.OUT, machine.Pin.PULL_DISABLE)
        self.set_pa(1)
        self.volume = pa.DEFAULT_VOLUME
        self.aud = audio.Audio(0)
        self.aud.setCallback(self.audio_cb)
        self.tts = audio.TTS(0)
        self.aud.setVolume(self.volume)
        player_task(self.aud)
        self.paly_start = False

    def set_pa(self, state):
        self.PA.write(state)

    def audio_cb(self, event):
        if event == 0:
            self.paly_start = True
            print('audio-play start.')
        elif event == 7:
            self.paly_start = False
            pa.led_mode = pa.LED_OFF
            print('audio-play finish.')


    def play_stream(self, data):
        try:
            ret = self.aud.playStream(3, data)
            print("playStream ret:", ret)
        except Exception as e:
            print("Error during playStream:", e)
        utime.sleep_ms(10)


    def stop_stream(self):
        try:
            pa.mp3_player.clear_queue_and_stop(pa.mp3_queue_audio)
            self.aud.stopAll()
            self.aud.stopPlayStream() # 停止播放流
        except Exception as e:
            print("Error during stopStream:", e)

    def system_sound(self, sound):
        self.aud.play(1, 1, sound)


    def play_tts(self, text):
        ret = self.tts.play(1, 1, 2, text)
        print("playTTS ret:", ret)


    def volume_up(self):
        if self.volume < pa.MAX_VOLUME:
            self.volume += 1
            self.aud.setVolume(self.volume)
        elif self.volume == pa.MAX_VOLUME:
            self.volume = pa.MAX_VOLUME
            self.aud.setVolume(self.volume)

    def volume_down(self):
        if self.volume > pa.MIN_VOLUME:
            self.volume -= 1
            self.aud.setVolume(self.volume)
        elif self.volume == pa.MIN_VOLUME:
            self.volume = pa.MIN_VOLUME
            self.aud.setVolume(self.volume)

    def volume_circle(self):
        if self.volume < pa.MAX_VOLUME:
            self.volume += 1
            self.aud.setVolume(self.volume)
        elif self.volume == pa.MAX_VOLUME:
            self.volume = pa.MIN_VOLUME
            self.aud.setVolume(self.volume)
        if self.paly_start != True:
            self.system_sound(pa.VOLUME_CHANGE)

class MP3Player:
    def __init__(self, player):
        self.player = player
        self.audio_play_id = None
        self.is_playing = False

    def get_audio_file_url(self, mp3_url):
        try:
            self.is_playing = True
            response = request.get(mp3_url)
            while True:
                audio_bytes_msg = response.raw.read(8 * 1024)  # 每次读取8KB
                if not audio_bytes_msg:
                    break
                self.player.playStream(3, audio_bytes_msg)  # 播放音频流
                utime.sleep_ms(20)
        except Exception as e:
            print("Error during playback:", e)
        finally:
            self.is_playing = False

    def play_music(self, mp3_url):
        # print("play_music >>>>> {}".format(mp3_url))
        self.audio_play_id = _thread.start_new_thread(self.get_audio_file_url, (mp3_url,))

    def stop_music(self):
        """停止播放音频"""
        if self.audio_play_id is not None:
            self.player.stopPlayStream()
            self.is_playing = False
            # 停止当前线程
            _thread.stop_thread(self.audio_play_id)
            self.audio_play_id = None

    def clear_queue_and_stop(self, queue):
        """停止播放并清空队列"""
        if self.is_playing:
            self.stop_music()
        while not queue.empty():
            queue.get()
        print("Playback stopped and queue cleared.")


def player_th():
    """管理播放队列"""
    while True:
        if not pa.mp3_queue_audio.empty() and not pa.mp3_player.is_playing:
            mp3_url = pa.mp3_queue_audio.get()
            pa.mp3_player.play_music(mp3_url)
        utime.sleep_ms(100)


def player_task(aud):
    """启动播放器任务"""
    pa.mp3_player = MP3Player(aud)
    pa.mp3_queue_audio = Queue()
    
    player_th_id = _thread.start_new_thread(player_th, ())
    print("player_task_create")