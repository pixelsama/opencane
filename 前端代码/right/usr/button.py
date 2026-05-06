import machine
from usr import parameter as pa
from usr import chat
import utime
import _thread
from usr import config
from usr import cam_manager
import ujson

# =======================================
# 按键检测
# =======================================
BUTTON_FUNCTION_PIN = pa.BUTTON_FUNCTION_PIN 

button_func = machine.Pin(BUTTON_FUNCTION_PIN, machine.Pin.IN, machine.Pin.PULL_PU)  # 按钮输入，使用上拉电阻

button_th_id = None
g_current_ws = None

def button_th():
    global g_current_ws
    while True:
        if pa.ota_flag:
            # 更新过程中不响应按钮
            utime.sleep_ms(100)
            continue
            
        if button_func.value() == 0:
            # 按钮按下
            print("[button_th] button_func pressed")
            pa.button_func_state = 0  # 按下
            pa.timer_cnt = 0  # 重置计时器
            pa.button_press_time = utime.ticks_ms()  # 记录按下时间
            pa.button_press_type = 0  # 重置按键类型
            
            # 检查网络状态
            if pa.net_state == False:
                pa.player.play_tts(pa.TURNING)
                utime.sleep_ms(100)
                continue
            else:
                # 修改：不再检查绑定状态，直接视为已绑定
                if pa.bind_status == 0:  # 强制设置为已绑定状态
                    pa.player.system_sound(pa.BINDING)
                    config.getBaseConfig()
                    utime.sleep_ms(100)
                # 等待判断是短按还是长按
                while button_func.value() == 0:
                    # 检查是否达到长按阈值
                    if utime.ticks_diff(utime.ticks_ms(), pa.button_press_time) >= pa.LONG_PRESS_TIME and pa.button_press_type == 0:
                        # 长按触发
                        pa.button_press_type = 2
                        print("[button_th] Long press detected")
                        
                        # 长按操作：开始录音对话流程
                        # 1. 关闭之前的连接和播放
                        pa.flow_step = 0
                        pa.wss_flow = 0
                        pa.led_mode = pa.LED_OFF
                        try:
                            if g_current_ws:
                                g_current_ws.close_ws()
                            pa.player.stop_stream()
                        except Exception as e:
                            print("[button_th] close_ws error:", str(e))
                        
                        # 2. 开始录音
                        pa.led_mode = pa.LED_ON
                        pa.record_flag = 1

                        # 3. 建立新的WebSocket连接
                        g_current_ws = chat.VTTFlow(url=pa.wss_url, token=pa.wss_token)
                        g_current_ws.connect_ws()
                        pa.wss_running = True
                        
                        pa.flow_step = 1
                        
                    utime.sleep_ms(10)
                
                # 按钮释放
                print("[button_th] button_func released")
                pa.button_func_state = 1  # 松开
                
                if pa.button_press_type == 0:
                    # 短按操作：拍照
                    pa.button_press_type = 1
                    print("[button_th] Short press detected - Taking photo")
                    # 使用cam_manager模块 - 修改为调用trigger_capture方法
                    cam_manager.camera_manager.trigger_capture()
                elif pa.button_press_type == 2:
                    # 长按操作：结束录音，发送音频
                    print("[button_th] Long press released - Sending audio")
                    pa.led_mode = pa.LED_OFF
                    pa.record_flag = 3
                    pa.flow_step = 3
                    
                    # 如果有最近的照片，且用户询问照片内容，则获取照片描述
                    if pa.last_photo_url and chat.temp_read_buffer:
                        # 这里简单判断，实际可能需要更复杂的语音识别来确定用户是否在询问照片
                        try:
                            # 将照片URL添加到会话上下文中
                            if g_current_ws and g_current_ws.client:
                                context_msg = {
                                    "type": "context.update",
                                    "context": {
                                        "last_photo_url": pa.last_photo_url
                                    }
                                }
                                g_current_ws.client.send(ujson.dumps(context_msg))
                                
                                # 尝试获取照片描述 - 修改为使用camera_manager
                                if cam_manager.camera_manager:
                                    description = pa.PHOTO_DESCRIPTION  # 使用默认描述
                                    if description:
                                        # 将照片描述添加到会话上下文中
                                        context_msg = {
                                            "type": "context.update",
                                            "context": {
                                                "photo_description": description
                                            }
                                        }
                                        g_current_ws.client.send(ujson.dumps(context_msg))
                        except Exception as e:
                            print("[button_th] Error handling photo context:", str(e))
        
        utime.sleep_ms(10)

def button_task():
    global button_th_id
    # 创建一个线程，用来检测按键是否按下
    button_th_id = _thread.start_new_thread(button_th, ())
    print("[BUTTON] : button_task_create")

def destory_button_th():
    global button_th_id
    # 停止线程
    _thread.stop_thread(button_th_id)   # 删除线程
    print("[BUTTON] : destory_button_th")
