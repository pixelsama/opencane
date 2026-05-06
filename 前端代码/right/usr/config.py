import utime
from machine import Pin
from usr import parameter as pa
import log
import _thread
import request
import ujson
from usr import ota

# 设置日志输出级别
config_log = log.getLogger("config")

bind_statue_0_cnt = 0
bind_func = 1

def getBaseConfig():
    global bind_statue_0_cnt, bind_func
    """获取配置文件"""
    url = pa.config_url
    # 构建 JSON 请求体
    # 修改：始终将bind_status设为1，表示已绑定
    # pa.bind_status = 1  # 强制设置为已绑定状态
    if pa.bind_status == 0:
        payload = {
        "devId": pa.devSN,
        "last_nfcId": "0",
        "nfcId": "0",
        "language": "cn",
        "version": pa.VERSION,
        "bat": pa.power_val,
        "device_type": "4G",
        "imei"  : pa.imei,
        "bind_status": 0
    }
    else:
        payload = {
            "devId": pa.devSN,
            "last_nfcId": "0",
            "nfcId": "0",
            "language": "cn",
            "version": pa.VERSION,
            "bat": pa.power_val,
            "device_type": "4G",
            "imei"  : pa.imei,
        }

    

    # 设置请求头
    headers = {
        'Content-Type': 'application/json'
    }

    # 发送请求
    response = request.post(url, data=ujson.dumps(payload), headers=headers)

    if response.status_code == 200:
        json_data = response.json()
        # config_log.info(json_data)
        if json_data.get("data") not in [None, ""]:
            data_content = json_data.get("data")
            # print(data_content)
            if data_content:
                parsed_data = ujson.loads(data_content)
                if parsed_data.get("bind_func") not in [None, ""]:
                    bind_func = parsed_data.get("bind_func")
                # 修改：忽略服务器返回的bind_status，保持本地设置为已绑定
                if parsed_data.get("bind_status") not in [None, ""]:
                    pa.bind_status = parsed_data.get("bind_status")
                    if bind_func == 0:
                        pa.bind_status = 1
                    print("bind_status:", pa.bind_status)
                if parsed_data.get("base_url") not in [None, ""]:
                    pa.base_url = parsed_data.get("base_url")
                    pa.url = pa.base_url + "/chatbyvoiceAsync22/"
                if parsed_data.get("mqtt_server") not in [None, ""]:
                    pa.mqtt_server = parsed_data.get("mqtt_server")
                if parsed_data.get("mqtt_port") not in [None, ""]:
                    pa.mqtt_port = parsed_data.get("mqtt_port")
                if parsed_data.get("on_wifi_standby_time") not in [None, ""]:
                    pa.timer_max = parsed_data.get("on_wifi_standby_time") / 1000 / 60
                    print("timer_max:", pa.timer_max)
                if parsed_data.get("app_version") not in [None, ""]:
                    pa.app_version = parsed_data.get("app_version")
                if parsed_data.get("app_ota_bin1") not in [None, ""]:
                    pa.app_ota_bin1 = parsed_data.get("app_ota_bin1")
                if parsed_data.get("app_ota_bin2") not in [None, ""]:
                    pa.app_ota_bin2 = parsed_data.get("app_ota_bin2")
                if parsed_data.get("ota_model") not in [None, ""]:
                    pa.ota_model = parsed_data.get("ota_model")
                if parsed_data.get("ota_files") not in [None, ""]:
                    pa.ota_files = parsed_data.get("ota_files", [])   
                if parsed_data.get("app_ota_bin") not in [None, ""]:
                    pa.app_ota_bin = parsed_data.get("app_ota_bin")   
                if parsed_data.get("asr") not in [None, ""]:
                    asr_content = parsed_data.get("asr")
                    if asr_content.get("mode") not in [None, ""]:
                        pa.asr_mode = asr_content.get("mode")
                    if asr_content.get("type") not in [None, ""]:
                        pa.asr_type = asr_content.get("type")
                    if asr_content.get("action") not in [None, ""]:
                        pa.asr_action = asr_content.get("action")
                    if asr_content.get("asr_url") not in [None, ""]:
                        asr_url_temp = asr_content.get("asr_url")
                        if asr_url_temp.startswith("wss"):
                            # pa.asr_url = asr_url_temp.replace("ws", "wss")
                            pass
                        else:
                            pa.asr_url = asr_url_temp 
                        # pa.asr_url = asr_content.get("asr_url")
                        # print("asr_url:", pa.asr_url)
                    if asr_content.get("asr_token") not in [None, ""]:
                        pa.asr_token = asr_content.get("asr_token")
                    
                    pa.wss_url = pa.asr_url
                    pa.wss_token = pa.asr_token

                if parsed_data.get("welcome_mp3") not in [None, ""]:
                    if pa.bind_status == 0:
                        if bind_statue_0_cnt == 0:
                            bind_statue_0_cnt += 1
                            pa.player.system_sound(pa.BIND_NOT)
                    # 注释掉欢迎语音播放，防止每次启动都播放"魔法生效了"
                    else:
                        pa.mp3_queue_audio.put(str(parsed_data.get("welcome_mp3")))
                    #pass

                # 如果app_version > pa.VERSION, quec_0.0.1 如果前缀quec一样，且后面版本号大于当前版本号，则进行升级
                if pa.app_version != None and pa.app_version.split('_')[0] == pa.VERSION.split('_')[0]:
                    if pa.app_version.split('_')[1] > pa.VERSION.split('_')[1]:
                        if pa.ota_model == 0:
                            if pa.app_ota_bin1 != None and pa.app_ota_bin2 != None:
                                ota.run()
                        elif pa.ota_model == 1:
                            if pa.ota_files != None and pa.app_ota_bin != None:
                                ota.app_ota_run()

            return True
    else:
        print("Upload failed, status code:", response.status_code)
        return None