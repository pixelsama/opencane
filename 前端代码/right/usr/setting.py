from misc import USB
from usr import parameter as pa
from misc import Power
import utime
import _thread  # 只保留一次导入
import system
import log
import osTimer
from usr import button
import _thread


usb = USB()
def usb_callback(conn_status):
    status = conn_status
    if status == 0:
        print('USB.')
    elif status == 1:
        print('USB is connected.')

usb.setCallback(usb_callback)

# 移除关机定时器
# timer = osTimer()
# def timer_up_cb(arg):
#     pa.timer_cnt += 1
#     if (pa.timer_cnt == pa.timer_max):
#         print('timer is up now.')
#         pa.player.system_sound(pa.SHUTDOWN)
#         _thread.start_new_thread(button.shutdown_soft, ())
# timer.start(60*1000,1,timer_up_cb)

def power_detect():
    pa.power_val = Power.getVbatt()
    # print('power_val:', pa.power_val)

def setting_th():
    while True:
        power_detect()
        utime.sleep_ms(1000)
        pass

setting_th_id = None
def setting_task():
    global setting_th_id
    try:
        # 创建一个线程，用来检测按键是否按下
        setting_th_id = _thread.start_new_thread(setting_th, ())
        print("[SETTING] : setting_task_create")
    except Exception as e:
        print("[SETTING] Thread creation failed:", str(e))

def destory_setting_th():
    global setting_th_id
    try:
        # 停止线程
        _thread.stop_thread(setting_th_id)   # 删除线程
        print("[SETTING] : setting_button_th")
    except Exception as e:
        print("[SETTING] Thread destruction failed:", str(e))