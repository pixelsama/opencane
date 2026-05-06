import camera
import utime
import request
import _thread
import machine
import log
from usr import parameter as pa

camera_log = log.getLogger("CAMERA")

class CameraManager:
    def __init__(self):
        self.cam = None
        self.initialized = False
        self.is_capturing = False
        self.take_photo_flag = False

    def init(self):
        """初始化摄像头硬件并创建 camCapture 实例，禁用 LCD 预览"""
        if not pa.CAMERA_ENABLED:
            camera_log.warning("Camera feature disabled by configuration")
            return False
        if self.initialized and self.cam:
            return True
        try:
            # 供电及灯光
            machine.Pin(pa.CAM_PWDN_PIN, machine.Pin.OUT).write(0)
            machine.Pin(pa.CAM_LIGHT1_PIN, machine.Pin.OUT).write(0)
            machine.Pin(pa.CAM_LIGHT2_PIN, machine.Pin.OUT).write(0)
            camera_log.info("Power and lights enabled")
            utime.sleep_ms(100)

            # 创建 camCapture 实例，lcd_w/lcd_h=0 禁用预览
            w, h = pa.CAMERA_RESOLUTION
            model = pa.CAMERA_MODEL
            level = pa.CAMERA_MODE[0]
            self.cam = camera.camCapture(model, w, h, level, 0, 0)
            ret = self.cam.open()
            if ret != 0:
                camera_log.error("camCapture.open failed, ret=%d", ret)
                return False
            camera_log.info("Camera initialized (LCD disabled)")
            self.initialized = True
            return True
        except Exception as e:
            camera_log.error("Camera init exception: %s", e)
            return False

    def upload_photo(self, photo_path="/usr/photo_temp.jpg"):
        """上传 JPEG 数据，成功返回 True，否则返回 False"""
        if not photo_path:
            photo_path = "/usr/photo_temp.jpg"
        camera_log.info("Attempt to upload photo: %s", photo_path)
        try:
            with open(photo_path, 'rb') as f:
                img_bytes = f.read()
                camera_log.info("Photo file read successfully")
        except Exception as e:
            camera_log.error("Open photo failed: %s", e)
            return False
        boundary = "----------------------------767695881516779500683722"
        fname = photo_path.split("/")[-1]
        header = (
            "--{b}\r\n"
            "Content-Disposition: form-data; name=\"devId\"\r\n\r\n"
            "{dev}\r\n"
            "--{b}\r\n"
            "Content-Disposition: form-data; name=\"image\"; filename=\"{fn}\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n"
        ).format(b=boundary, dev=pa.devSN, fn=fname.replace(".jpeg", ".jpg"))
        footer = "\r\n--{b}--\r\n".format(b=boundary)
        body = header.encode() + img_bytes + footer.encode()
        try:
            headers = {
                 "Content-Type": "multipart/form-data; boundary={b}".format(b=boundary),
                 
            }
            camera_log.info("Sending POST request to %s", pa.PHOTO_UPLOAD_URL)
            resp = request.post(pa.PHOTO_UPLOAD_URL, data=body, headers=headers)
            camera_log.info("Received response with status code: %d", resp.status_code)
            if resp.status_code != 200:
                camera_log.error("Upload failed, status=%d", resp.status_code)
                return False
            try:
                js = resp.json()
                camera_log.info("Upload response: %s", js)
                if 'image_id' in js:
                    pa.last_photo_id = js['image_id']
                    # 增加保存图片URL
                if 'image_url' in js:
                    pa.last_photo_url = js['image_url']
                    camera_log.info("Saved image_id: %s, url: %s", pa.last_photo_id, pa.last_photo_url)
            except Exception as e:
                camera_log.warning("Failed parse JSON: %s", e)
            return True
        except Exception as e:
            camera_log.error("Upload exception: %s", e)
            return False

    def trigger_capture(self):
        """外部调用触发一次拍照"""
        self.take_photo_flag = True

    def close(self):
        """关闭摄像头，释放资源"""
        if self.cam:
            try:
                self.cam.close()
                camera_log.info("Camera closed")
            except Exception as e:
                camera_log.error("Error closing camera: %s", e)
            finally:
                self.cam = None
                self.initialized = False

# 单例管理器
camera_manager = CameraManager()

# 后台线程：持续检测拍照标志并上传

def capture_and_upload_thread():
    camera_log.info("capture_and_upload_thread started")
    camera_manager.init()
    if not camera_manager.init():
        camera_log.error("Camera initialization failed in capture_and_upload_thread")
        return
    while True:
        if not camera_manager.take_photo_flag:
            utime.sleep_ms(100)
            continue
        camera_log.info("take_photo_flag is True, starting capture and upload process")
        camera_manager.is_capturing = True
        # 系统拍照音效
        try:
            pa.player.system_sound(pa.TAKE_PHOTO_SOUND)
        except Exception as e:
            camera_log.warning("System sound playback failed: %s", e)
        pa.led_mode = pa.LED_BREATH

        # 确保摄像头已初始化
        if not camera_manager.initialized:
            if not camera_manager.init():
                camera_log.error("Camera not ready")
                pa.led_mode = pa.LED_OFF
                camera_manager.is_capturing = False
                camera_manager.take_photo_flag = False
                continue

        # 拍照
        filename = "photo_temp"
        w, h = pa.CAMERA_RESOLUTION
        ret = camera_manager.cam.start(w, h, filename)
        if ret != 0:
            camera_log.error("Capture failed, ret=%d", ret)
            pa.led_mode = pa.LED_OFF
            camera_manager.is_capturing = False
            camera_manager.take_photo_flag = False
            continue
        utime.sleep_ms(pa.CAMERA_PREVIEW_TIME)
        camera_log.info("Photo captured: %s.jpg", filename)

        # 上传
        path = "/usr/%s.jpg" % filename
        if camera_manager.upload_photo(path):
            camera_log.info("Upload succeeded, image_id=%s", pa.last_photo_id)
        else:
            camera_log.error("Upload failed for %s", path)

        # 重置状态
        pa.led_mode = pa.LED_OFF
        camera_manager.is_capturing = False
        camera_manager.take_photo_flag = False
        utime.sleep_ms(1000)

# 启动摄像头任务

def camera_task():
    """在主程序中调用启动摄像头后台线程"""
    if pa.CAMERA_ENABLED:
        try:
            _thread.start_new_thread(capture_and_upload_thread, ())
            camera_log.info("camera_task created")
        except Exception as e:
            camera_log.error("camera_task_create error: %s", e)
    else:
        camera_log.warning("Camera feature is disabled in configuration")

# 销毁任务

def destroy_camera_task():
    """关机时调用，关闭摄像头和线程"""
    camera_manager.close()

__all__ = ['camera_manager', 'camera_task', 'destroy_camera_task']


