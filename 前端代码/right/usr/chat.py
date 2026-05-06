import uwebsocket
import utime
import _thread
import ujson
import audio
import modem
import machine
from usr import parameter as pa
from queue import Queue  # 如果系统没有 Queue，可以换用 list + 自己管理

# =======================================
# 录音
# =======================================
RECORD_MAX_TIME = 30  # 最大录音时间（秒）
READ_BLOCK_SIZE = 1024
read_buffer = bytearray(READ_BLOCK_SIZE)
record = audio.Record(0)
recorder_th_id = None

temp_read_buffer = bytearray()

audio_queue = Queue()   # 存放录音数据
queue_lock = _thread.allocate_lock()

def start_recording():
    ret = record.stream_start(record.AMRNB, 8000, RECORD_MAX_TIME)
    if ret != 0:
        print("[Audio] start stream recording failed, ret =", ret)
    else:
        print("[Audio] start stream recording (30s, AMRNB)...")
def stop_recording():
    ret = record.stream_stop()
    print("[Audio] stop recording, ret =", ret)

def get_audio_chunk():
    if not record.isBusy():
        print("[Audio] recording isBusy.")
        return None
    read_len = record.stream_read(read_buffer, READ_BLOCK_SIZE)
    if read_len > 0:
        # print('Read audio data:', bytes(read_buffer[:read_len]))
        print('Read audio data size:', len(bytes(read_buffer[:read_len])))
        return bytes(read_buffer[:read_len])
    return None

def record_th():
    global temp_read_buffer
    while True:
        if pa.record_flag == 1:
            print("[RECORD] record_th start")
            start_recording()
            pa.record_flag = 2
            pa.record_start = 1
            temp_read_buffer = bytearray()
        if pa.record_flag == 2:
            # get_audio_chunk()
            if record.isBusy():
                read_len = record.stream_read(read_buffer, READ_BLOCK_SIZE)
                if read_len > 0:
                    # 将读取到的数据持续存入temp_read_buffer
                    # print("[Audio] [TEMP] record read_len:", read_len)
                    temp_read_buffer += read_buffer[:read_len]
                # print("[Audio] record temp_read_buffer len:", len(temp_read_buffer))
            else:
                print("[Audio] record is not busy.")
                utime.sleep_ms(10)
        if pa.record_flag == 3:
            print("[RECORD] record_th stop")
            stop_recording()
            # 添加录音结束提示音
            try:
                pa.player.system_sound(pa.VOLUME_CHANGE)
                print("[RECORD] Playing recording end sound")
            except Exception as e:
                print("[RECORD] Failed to play end sound:", str(e))
            pa.record_flag = 0
            pa.record_start = 0
        utime.sleep_ms(10)

def record_task():
    global recorder_th_id
    recorder_th_id =  _thread.start_new_thread(record_th, ())
    print("[RECORD] recorder_task_create")

def destory_recorder_th():
    global recorder_th_id
    _thread.stop_thread(recorder_th_id) 
    print("[RECORD] destory_recorder_th") 



# =======================================
# WebSocket 逻辑
# =======================================

# 标记：是否允许发送音频、是否处于播放回答等
g_ready_to_upload = False
g_playing_audio   = False

class VTTFlow:
    def __init__(self, url, token):
        self.url = url
        self.token = token
        self.client = None
        self.running = True
        pa.wss_running = True
        self.speech_id = 0  # 区分不同对话轮次
        
    def connect_ws(self):
        """
        建立 WebSocket 连接
        """
        try:
            self.client = uwebsocket.Client.connect(
                self.url,
                headers={"Authorization": self.token},
                debug=True
            )
        except Exception as e:
            print("[VTT] connect error:", str(e))

        if self.client:
            # 启动子线程，循环接收服务器消息
            _thread.start_new_thread(self.recv_loop, ())
            _thread.start_new_thread(self.full_flow, ())

    def close_ws(self):
        """
        关闭 WebSocket 连接
        """
        self.running = False
        if self.client:
            try:
                self.client.close()
            except OSError as e:
                print("[VTT] close error:", str(e))
                # 即使关闭失败，也继续执行后续代码
            finally:
                self.client = None
        print("[VTT] closed.")

    # 在recv_loop函数中修改音频播放部分
    def recv_loop(self):
        """
        子线程：循环接收服务器下发的消息
        """
        global g_ready_to_upload, g_playing_audio

        while self.running:
            try:
                data = self.client.recv()
                print("[recv_loop] recv data, size =", len(data))
                if not data:
                    print("[recv_loop] no data or server closed")
                    break
                
                if self._is_text_data(data):
                    # print("[recv_loop] recv data =", data)
                    # print("[recv_loop] data type:", type(data))
                    self.handle_server_text(data)
                else:
                    # 音频数据处理
                    print("[recv_loop] audio data, size =", len(data))
                    
                    # 确保录音已停止
                    if pa.record_start == 1:
                        print("[recv_loop] stopping recording before playback")
                        pa.record_flag = 3  # 触发停止录音
                        utime.sleep_ms(100)  # 给录音线程一点时间停止
                    
                    # 播放音频
                    pa.player.play_stream(data)
                    pa.led_mode = pa.LED_BREATH
            except Exception as e:
                print("[recv_loop] error:", str(e))
                pa.wss_running = False
                break
            utime.sleep_ms(10)
        self.running = False
        self.close_ws()
        pa.flow_step = 0
        # if pa.record_start == 1:
        #     print("[recv_loop] stop recording")
        #     stop_recording()
        print("[recv_loop] ended.")

    def _is_text_data(self, data):
        try:
            # 判断第一位是否为 {，如果是则是 JSON 文本
            if data[0] == 123:
                return True
            obj = ujson.loads(data)# 如果能成功 => 是JSON文本
            return True  
        except:
            return False
    def handle_server_text(self, text):
        global g_ready_to_upload, g_playing_audio

        try:
            obj = ujson.loads(text)
        except:
            print("[handle_server_text] Not JSON:", text)
            return
        
        msg_type = obj.get("type", "")

        if msg_type == "session.updated":
            # 服务器允许开始上传音频
            g_ready_to_upload = True
            pa.wss_flow = 2
            pa.flow_step = 2
            pa.record_flag = 0  #前期存储可以停止了，开始正式上传存储
            if pa.button_func_state == 0:
                # pa.flow_step = 2
                # pa.led_mode = pa.LED_ON
                # print("[1]pa.flow_step; pa.wss_flow; pa.record_start", pa.flow_step, pa.wss_flow, pa.record_start)
                pass
            else:
                # print("[2]pa.flow_step; pa.wss_flow; pa.record_start", pa.flow_step, pa.wss_flow, pa.record_start)
                print("[Flow] Stopped but just updated")
            print("[Flow] server => updated => start sending audio ...")

        elif msg_type in ("input_audio_buffer.committed", "commited"):
            pa.wss_flow = 3
            pa.player.system_sound(pa.PING)
            # print("[Flow] server => input_audio_buffer.committed => analyzing now ...")

        elif msg_type == "response.created":
            pa.wss_flow = 4
            # print("[Flow] server => response.created => start playback")
            # print("[Flow] sever answer: {}".format(obj.get("user_text","")))
            g_playing_audio = True
            print("[Audio] start playback (server answer)")

        elif msg_type == "response.audio.done":
            # print("[Flow] server => response.audio.done => stop playback")
            g_playing_audio = False
            self.close_ws()
        else:
            pass
    def send_session_update(self):
        """
        (1) -> session.update
        设置输入音频格式为 amr，输出音频格式为 pcm16
        """
        self.speech_id += 1
        payload = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "input_audio_format": "amr",   # 与录音实际格式对应
                "output_audio_format": "mp3",
                "input_audio_rate": 8000,
                "output_audio_rate": 44100,
                "data_type": "binary",
                "instructions": "You are a helpful assistant. Knowledge cutoff is 2023-10.",
                "devId" : pa.devSN,
                "bat":pa.power_val,
                "version": pa.VERSION,
                "imei": pa.imei,
                "speech_id": self.speech_id,
                "image_id": pa.last_photo_id if hasattr(pa, 'last_photo_id') else None,
                "image_url": pa.last_photo_url if hasattr(pa, 'last_photo_url') else None,
            }
        }
        txt = ujson.dumps(payload)
        self.client.send(txt)
        print("[Flow] -> session.update, speech_id=", self.speech_id)
        print("[DEBUG] image_id in send_session_update:", pa.last_photo_id)
        print("[DEBUG] Saved image_url: ", pa.last_photo_url)


    def send_audio_chunks(self):
        """
        (3) -> 分块上传音频
        每次从 get_audio_chunk() 获取一包数据（AMR），然后发送二进制帧
        """
        # 可等待服务器发 session.updated 后再开始
        print("[Flow] record.isBusy()=", record.isBusy())
        if record.isBusy():
            chunk = get_audio_chunk()
            if chunk:
                self.client.send(chunk)
            else:
                utime.sleep_ms(10)
        else:
            print("[Flow] record.isBusy()=False")
            
        if pa.record_start == 0:
            print("[Flow] record_start == 0")
            # pa.flow_step = 3
        # print("[Flow] no more PCM to send (record finished)")

    def send_commit(self):
        """
        (4) -> commit (表示音频传输结束)
        """
        payload = {"type": "input_audio_buffer.commit"}
        txt = ujson.dumps(payload)
        self.client.send(txt)
        print("[Flow] -> commit")

    def send_response_create(self):
        """
        (5) -> response.create (准备接收回答音频)
        """
        payload = {
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"],
                "temperature": 0.7,
                "data_type": "binary",
                "max_response_output_tokens": "1000",
                "image_id": pa.last_photo_id if hasattr(pa, 'last_photo_id') and pa.last_photo_id else None,
                "image_url": pa.last_photo_url if hasattr(pa, 'last_photo_url') and pa.last_photo_url else None
            }
        }
        txt = ujson.dumps(payload)
        self.client.send(txt)
        print("[Flow] -> response.create with image_id:")
        print("[DEBUG] image_id in send_response_create:", pa.last_photo_id)
        print("[DEBUG] Saved image_url: ", pa.last_photo_url)


    def full_flow(self):
        global temp_read_buffer
        while self.running:
            if pa.flow_step == 0:
                utime.sleep_ms(10)
            elif pa.flow_step == 1:
                pa.sent_audio = False  # 重置标记位
                self.send_session_update()
                pa.flow_step = 0 # 等待服务器回应 updated
            elif pa.flow_step == 2:
                if pa.button_func_state == 0:  # 如果按钮还在按下，则继续发送音频
                    if len(temp_read_buffer) > 0:
                        print("[Flow] [1]temp_read_buffer len:", len(temp_read_buffer))
                        self.client.send(bytes(temp_read_buffer[:len(temp_read_buffer)]))
                        # 清空temp_read_buffer
                        temp_read_buffer = bytearray()
                    self.send_audio_chunks()
                    pa.sent_audio = True
                else:
                    # 判断temp_read_buffer是否有数据，如果有则发送
                    if len(temp_read_buffer) > 0:
                        print("[Flow] [2]temp_read_buffer len:", len(temp_read_buffer))
                        self.client.send(bytes(temp_read_buffer[:len(temp_read_buffer)]))
                        temp_read_buffer = bytearray()
                        pa.sent_audio = True
                        pa.flow_step = 3
            elif pa.flow_step == 3:
                if pa.wss_flow == 2 and pa.button_func_state == 1 and pa.sent_audio == True:
                    self.send_commit()
                    pa.flow_step = 4
            elif pa.flow_step == 4:
                if pa.wss_flow == 3:
                    self.send_response_create()
                    pa.flow_step = 0
            utime.sleep_ms(10)
        print("[Flow] full_flow ended.")




def is_asking_about_photo(self):
    """判断用户是否在询问最近的照片"""
    # 这里可以添加简单的关键词检测逻辑
    # 如果有语音识别的文本结果，可以基于文本判断
    # 如果没有，可以根据拍照时间和提问时间的间隔来粗略判断
    return bool(pa.last_photo_id) and (utime.ticks_diff(utime.ticks_ms(), pa.last_photo_time) < 60000)  # 1分钟内

