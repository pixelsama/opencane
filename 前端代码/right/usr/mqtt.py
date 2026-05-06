from umqtt import MQTTClient
import utime
import log
import checkNet
from usr import parameter as pa
import _thread
import ujson
from misc import Power

'''
下面两个全局变量是必须有的，用户可以根据自己的实际项目修改下面两个全局变量的值
'''
PROJECT_NAME = "QuecPython_MQTT_example"
PROJECT_VERSION = "1.0.0"

checknet = checkNet.CheckNetwork(PROJECT_NAME, PROJECT_VERSION)

# 设置日志输出级别
log.basicConfig(level=log.INFO)
mqtt_log = log.getLogger("MQTT")

def sub_cb(topic, msg):
    mqtt_log.info("Subscribe Recv: Topic={},Msg={}".format(topic.decode(), msg.decode()))
    json_msg = ujson.loads(msg.decode())
    mqtt_log.info("json_msg:{}".format(json_msg))
    # {"cmd": "text_to_speech", "msg": "success", "data": ["http://openpin3-cdn.z33.fun/static//voice/output/1730288070.9262433.mp3"], "msgId": "1"}
    # print(json_msg)
    if json_msg.get("cmd") == "text_to_speech":
        if json_msg.get("msg") == "success":
            mp3_url = json_msg["data"][0]
            # if (json_msg.get("msgId") == pa.msgid):
            #     pa.mp3_queue_audio.put(mp3_url)
            if(json_msg.get("msgId") == "-1"):
                # print(mp3_url)
                pa.player.stop_stream()
                pa.mp3_queue_audio.put(mp3_url)
    if json_msg.get("cmd") == "sys":
        if json_msg.get("msg") == "success":
            sys_data= ujson.loads(json_msg["data"])
            # 修改：忽略绑定状态更新，不播放绑定成功提示音
            if sys_data.get("bind_status") != None:
                print("bind success")
                pa.bind_status = sys_data.get("bind_status")
                if pa.bind_status == 1:
                    pa.player.system_sound(pa.BIND_SUCCESS)
            if sys_data.get("restart") != None:
                print("restart_now")
                Power.powerRestart()
                

# def mqtt_th():
#     while True:
#         pa.mqtt_c.wait_msg()
#         utime.sleep_ms(50)

def err_cb(err):
    print("thread err:%s"%err)
    if err == "reconnect_start":
        print("start reconnect")
    elif err == "reconnect_success":
        print("success reconnect")
    else:
        print("reconnect FAIL")

def mqtt_th():
# 此线程专门循环等待消息，如果异常就断开并重新连接
    while True:
        try:
            pa.mqtt_c.ping()
            pa.mqtt_c.wait_msg()
        except OSError as e:
            ret = pa.mqtt_c.get_mqttsta()
            mqtt_log.info("mqtt status:{}".format(ret))
            mqtt_log.info("wait_msg OSError[{}]. Trying to reconnect...".format(e))
            # 先尝试断开，释放资源
            try:
                pa.mqtt_c.disconnect()
            except:
                pass
            utime.sleep(1)  # 略微延迟再重连
            
            # 重连并重新订阅
            try:
                pa.mqtt_c.connect()
                paho_topic = "paho/test/" + pa.devSN
                pa.mqtt_c.subscribe(paho_topic)
            except Exception as ex:
                mqtt_log.error("Reconnection failed: {}".format(ex))
        utime.sleep_ms(50)

def mqtt_task():
    stagecode, subcode = checknet.wait_network_connected(30)
    if stagecode == 3 and subcode == 1:
        mqtt_log.info('Network connection successful!')

        # 创建一个mqtt实例
        pa.mqtt_c = MQTTClient(pa.devSN, pa.mqtt_server,port=pa.mqtt_port,password=None, keepalive=60, ssl=False, ssl_params={},reconn=True,version=4)
        pa.mqtt_c.error_register_cb(err_cb)
        # 设置消息回调
        pa.mqtt_c.set_callback(sub_cb)
        #建立连接
        pa.mqtt_c.connect()
        # 订阅主题
        paho_topic = "paho/test/" + pa.devSN
        pa.mqtt_c.subscribe(paho_topic)
        mqtt_log.info("Connected to {}, subscribed to /paho/test/{} topic".format(pa.mqtt_server, pa.devSN))
        task_stacksize =_thread.stack_size()
        _thread.stack_size(24 * 1024)
        pa.mqtt_th_id =  _thread.start_new_thread(mqtt_th, ())
        # 线程创建成功后，恢复平台线程栈默认大小。
        _thread.stack_size(task_stacksize)
        mqtt_log.info(" mqtt_task_create")