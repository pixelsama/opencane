#远程升级

import fota
import utime
import log
import checkNet
from usr import parameter as pa
import app_fota
from misc import Power

# 设置日志输出级别
log.basicConfig(level=log.INFO)
fota_log = log.getLogger("Fota")

def result(args):
    print('download status:',args[0],'download process:',args[1])

def run():
    if pa.app_ota_bin1 != None and pa.app_ota_bin2 != None:
        pa.ota_flag = True
        fota_obj = fota()  # 创建Fota对象
        fota_log.info("httpDownload...")
        pa.player.play_tts("新版本更新，请耐心等待")
        #mini fota方式
        res = fota_obj.httpDownload(url1=pa.app_ota_bin1,url2=pa.app_ota_bin2)
        if res != 0:
            fota_log.error("httpDownload error")
            pa.ota_flag = False
            return
        fota_log.info("wait httpDownload update...")
        utime.sleep(2)
    else:
        pa.player.play_tts("更新失败")
        fota_log.info('Network connection failed! ')

def app_ota_run():
    # SOTA升级
    pa.ota_flag = True
    ota_data = [{"url": i["fileUrl"], "file_name": "/usr/" + i["fileName"].replace(".bin", ".py")} for i in pa.ota_files]
    _app_fota = app_fota.new()
    _app_fota.bulk_download(ota_data)
    _app_fota.set_update_flag() # 设置升级标志
    # 升级完成之后需要重启设备
    Power.powerRestart()