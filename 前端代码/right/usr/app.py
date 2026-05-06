from usr import parameter as pa
import modem
import checkNet
from usr import play
from usr import chat
from usr import button
from usr import config
from usr import mqtt
from usr import setting
from usr import cam_manager
import system
import log
from usr import led
import utime; utime.sleep(10)

# log.basicConfig(level=log.ERROR)
# system.replSetEnable(1,password='aispea12581')

def get_dev_info():
    pa.devSN = modem.getDevSN()
    pa.imei = modem.getDevImei()
    print('devSN:', pa.devSN)
    print('imei:', pa.imei)


def check_net_state():
    stage, state = checkNet.waitNetworkReady(30)
    if stage == 3 and state == 1:
        print('Network connection successful.')
        pa.net_state = True
        # 添加网络连接成功提示
        try:
            pa.player.play_tts("网络连接成功")
        except:
            pass
    else:
        print('Network connection failed, stage={}, state={}'.format(stage, state))
        pa.net_state = False
        # 添加网络连接失败提示
        try:
            pa.player.play_tts("网络连接失败，请检查网络设置")
            # 设置LED为红色闪烁表示网络错误
            pa.led_mode = pa.LED_FLOW
        except:
            pass

def app_main():
    led.led_init()
    pa.led_mode = pa.LED_RAINBOW
    get_dev_info()
    setting.setting_task()
    pa.player = play.Player()
    pa.player.system_sound(pa.TURN_ON)
    # 添加：在初始化时就设置为已绑定状态
    # pa.bind_status = 1
    utime.sleep(2)
    try:
        # 修改：使用trigger_capture方法
        button.set_short_press_callback(cam_manager.camera_manager.trigger_capture)
    except AttributeError:
        # 如果 button_task 支持传参，则使用下面方式
        # button.button_task(callback=cam_manager.camera_manager.trigger_capture)
        pass
    button.button_task()  # 初始化按钮
    check_net_state()
    config.getBaseConfig()
    chat.record_task()
    cam_manager.camera_task()
    pa.led_mode = pa.LED_OFF
    if(pa.mqtt_server != None and pa.mqtt_port != None):
        mqtt.mqtt_task()
    
    

