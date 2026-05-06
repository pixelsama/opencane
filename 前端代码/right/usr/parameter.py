import urandom


#   /** Major version number (X.x.x) */
VERSION_MAJOR = "0"
#   /** Minor version number (x.X.x) */
VERSION_MINOR = "0"
#   /** Patch version number (x.x.X) */
VERSION_PATCH = "3"

# VERSION_NAME = "quec"
VERSION_NAME = "QUECA"

VERSION = VERSION_NAME + "_" + VERSION_MAJOR + "." + VERSION_MINOR + "." + VERSION_PATCH 

"""---------------setting---------------"""
power_val = 0
min_power = 2000
timer_max = 15
timer_cnt = 0

"""---------------ota---------------"""
app_version = None
app_ota_bin1 = None
app_ota_bin2 = None
ota_flag = False

ota_model = 0  # 0: mini fota 1: full fota
ota_files = None
app_ota_bin = None

"""---------------LED---------------"""
LED_IDLE = 0
LED_ON = 1
LED_OFF = 2
LED_FLOW = 3
LED_BREATH = 4
LED_RAINBOW = 5

led_mode = LED_IDLE
"""---------------devInfo---------------"""
devSN = -1
imei = None
net_state = False

"""---------------player---------------"""
PA_PIN = 21
player = None
mp3_player = None
mp3_queue_audio = None
mp3_stop_flag = False

MAX_VOLUME = 11
MIN_VOLUME = 7
DEFAULT_VOLUME = 11
TURN_ON =  "U:/turn_on.mp3"
PING = "U:/ping.mp3"
BIND_NOT = "U:/bind_not.mp3"
BINDING = "U:/binding.mp3"
BIND_SUCCESS = "U:/bind_success.mp3"
TURNING = "正在开机"
VOLUME_CHANGE = 'U:/change_volume.mp3'
SHUTDOWN = 'U:/shutdown.mp3'
LOW_POWER = 'U:/low_battery.mp3'



"""---------------config---------------"""
# config_url = "http://openpin-config-cdn.buu123.com/dev/"
config_url = "http://openpin-config.buu123.com/dev/"
# config_url = "http://frp.z33.fun:23807/api/role/device_config"
base_url = None
asr_mode = "aispea"
asr_type = "websocket"
asr_action = "gen_wss"
# asr_url = "ws://openpin.aisp24.com/ws/v1/realtime"
asr_url = "ws://huoshan.z33.fun/ws/v1/realtime"
# asr_url = "ws://frp.z33.fun:23812/ws/v1/realtime"
asr_token = "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJHcm91cE5hbWUiOiLljJfkuqzotZvljZrliJvlipvnp5HmioDmnInpmZDlhazlj7giLCJVc2VyTmFtZSI6IuWRqOe-v-aXrSIsIkFjY291bnQiOiIiLCJTdWJqZWN0SUQiOiIxNzkzMjYyMTIwMDA4MTcyMzYxIiwiUGhvbmUiOiIxNTMyMTUzMzE1MSIsIkdyb3VwSUQiOiIxNzkzMjYyMTE5OTk5NzgzNzUzIiwiUGFnZU5hbWUiOiIiLCJNYWlsIjoiIiwiQ3JlYXRlVGltZSI6IjIwMjUtMDEtMDIgMTQ6MjM6MTQiLCJUb2tlblR5cGUiOjEsImlzcyI6Im1pbmltYXgifQ.YE-UyCBBwiS0ep2-jpPKtpJzZoB5kMjgOwxXOq2RalVkXqeGJAVzDqS3fptbHhCoae7JH0zZaL2E0VI0BvsnomlQpP8On7dXUfK2KCfX1JbdYqTznb1yfqGubc5DyBi-HbJQAUQw2N7RT2uqu6bdf_vCEjoljP-k_2LVDudSXcWlgMUqEvlmv-hWcCfQUExRQm3TEPsE3mFz4rhJRDtP0PAXf6q1LukFQ9Z-WSw0CGap1GPDceUy5I0Ta2ui4UqTGRj6Efa3yzfa1WLg6h5hnJGGyRLWTV8XA5Anr72DKsUZAcR4iPGBmz1jCV38rvpWGQbakpm4CB2Cljw8Y24vww"  # 鉴权Token

"""---------------mqtt---------------"""
mqtt_server = None
mqtt_port = None
mqtt_c = None
mqtt_th_id = None

bind_status = None
# 0 未绑定
# 1 已绑定


"""---------------button---------------"""
BUTTON_FUNCTION_PIN = 13  
# 移除不需要的按钮定义
# BUTTON_MODE_PIN = 13  
# BUTTON_SHUTDOWN_PIN = 39

button_func_state = 1  # 0:按下 1:松开
button_press_time = 0  # 按钮按下的时间戳
LONG_PRESS_TIME = 1000  # 长按时间阈值(毫秒)
button_press_type = 0  # 0:未定义 1:短按 2:长按

"""---------------camera---------------"""
CAMERA_ENABLED = True  # 是否启用摄像头
CAMERA_MODEL = 0  # 摄像头型号，应该是整数而非字符串
camera_obj = None
PHOTO_UPLOAD_URL = "http://openpin3.z33.fun/upload_image22"  # 图片上传地址
last_photo_url = None  # 最后一张照片的URL
last_photo_id = None   # 最后一张照片的ID
TAKE_PHOTO_SOUND = "U:/ping.mp3"  # 拍照提示音
PHOTO_DESCRIPTION = "这是最近拍摄的照片"  # 默认照片描述
BT_MAC = ""  # 设备MAC地址，将在初始化时设置
CAMERA_RESOLUTION = (640, 480)  # 默认分辨率
CAMERA_MODE = (1, 0)  # 默认模式
CAMERA_QUALITY = 80  # 图片质量(1-100)
CAMERA_BRIGHTNESS = 0  # 亮度调整(-4到4)
CAMERA_CONTRAST = 0  # 对比度调整(-4到4)
CAMERA_SATURATION = 0  # 饱和度调整(-4到4)
CAMERA_PREVIEW_TIME = 300  # 预览时间(毫秒)
CAMERA_FLASH_MODE = 0  # 闪光灯模式(0:关闭, 1:开启, 2:自动)

# 摄像头引脚定义
CAM_IIC_SCL_PIN   = 2  # 对应原理图网表 CAM_IIC_SCL 
CAM_IIC_SDA_PIN   = 3  # 对应原理图网表 CAM_IIC_SDA 
CAM_SPI_CLK_PIN   = 4  # 对应 CAM_SPI_CLK 
CAM_SPI_DATA0_PIN = 5  # 对应 CAM_SPI_DATA0 
CAM_SPI_DATA1_PIN = 6  # 对应 CAM_SPI_DATA1 
CAM_MCLK_PIN      = 1  # 对应 CAM_MCLK，用于提供传感器外部时钟 
CAM_PWDN_PIN      = 7  # 对应 CAM_PWDN，控制摄像头上电/省电模式 
CAM_LIGHT1_PIN    = 8  # 对应 CAM_LIGHT1，引脚连接到 Q3 基极 
CAM_LIGHT2_PIN    = 9  # 对应 CAM_LIGHT2，引脚连接到 Q4 基极

"""---------------chat---------------"""
wss_url = None
wss_token = None

flow_step = 0
record_flag = 0
record_start = 0

wss_flow = 0

wss_running = False
sent_audio = False



"""---------------uuid---------------"""
def generate_uuid():
    # 使用 urandom.getrandbits 生成各部分的随机数并格式化为 UUID4
    return '{:08x}-{:04x}-4{:03x}-{:04x}-{:012x}'.format(
        urandom.getrandbits(32),                         # 前 32 位
        urandom.getrandbits(16),                         # 接下来的 16 位
        urandom.getrandbits(12) & 0x0FFF,                # 接下来的 12 位，确保版本位为 4
        (urandom.getrandbits(14) & 0x3FFF) | 0x8000,     # 接下来的 16 位，确保变体位符合规范
        (urandom.getrandbits(32) << 16) | urandom.getrandbits(16)  # 最后 48 位，组合32位和16位的随机数
    )
