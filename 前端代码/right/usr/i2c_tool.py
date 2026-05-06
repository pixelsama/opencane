import log
import utime
from machine import I2C

# --------------------------------------------------------------------------------
# 日志初始化
# --------------------------------------------------------------------------------
log.basicConfig(level=log.INFO)
logger = log.getLogger("RC522")

# --------------------------------------------------------------------------------
# RC522 I2C 地址假设
#   在 STM32 例子里, 0x54 / 0x55 是 8 位地址(含 R/W 位).
#   通常将其右移 1 位(去掉读写位)得到 7 位地址 0x2A.
#   若你的硬件地址不同，请自行修改.
# --------------------------------------------------------------------------------
RC522_I2C_ADDR = 0x28

# --------------------------------------------------------------------------------
# 与 C 代码中的寄存器宏常量对应 (根据 rc522.h)
# 可能需要根据你的 RC522 I2C 模块做适当调整
# --------------------------------------------------------------------------------
MFRC_CommandReg      = 0x01
MFRC_ComIEnReg       = 0x02
MFRC_ComIrqReg       = 0x04
MFRC_DivIrqReg       = 0x05
MFRC_ErrorReg        = 0x06
MFRC_Status1Reg      = 0x07
MFRC_Status2Reg      = 0x08
MFRC_FIFODataReg     = 0x09
MFRC_FIFOLevelReg    = 0x0A
MFRC_ControlReg      = 0x0C
MFRC_BitFramingReg   = 0x0D
MFRC_CollReg         = 0x0E
MFRC_ModeReg         = 0x11
MFRC_TxModeReg       = 0x12
MFRC_RxModeReg       = 0x13
MFRC_TxControlReg    = 0x14
MFRC_TxAutoReg       = 0x15
MFRC_TModeReg        = 0x2A
MFRC_TPrescalerReg   = 0x2B
MFRC_TReloadRegH     = 0x2C
MFRC_TReloadRegL     = 0x2D
MFRC_CRCResultRegL   = 0x22
MFRC_CRCResultRegM   = 0x21
MFRC_VersionReg      = 0x37

# 命令字
MFRC_IDLE       = 0x00
MFRC_CALCCRC    = 0x03
MFRC_TRANSCEIVE = 0x0C
MFRC_AUTHENT    = 0x0E
MFRC_RESETPHASE = 0x0F

# 返回状态
MFRC_OK         = 0
MFRC_NOTAGERR   = -1
MFRC_ERR        = -2

# 一些常量
MFRC_MAXRLEN    = 18

# 请求命令
PICC_REQALL     = 0x52  # 寻天线区内全部卡
PICC_ANTICOLL1  = 0x93
PICC_HALT       = 0x50


# --------------------------------------------------------------------------------
# 工具函数：I2C 读写
#   注意QuecPython的I2C.write/read原型:
#       write(devAddr, cmd_buf, cmd_len, data_buf, data_len)
#       read(devAddr, cmd_buf, cmd_len, data_buf, data_len, stop)
# --------------------------------------------------------------------------------

def i2c_write_reg(i2c_obj, reg_addr, data_val):
    """
    模拟 stm32 中 HAL_I2C_Mem_Write(&hi2c1, 0x54, memaddr, 1, &data, 1, ...) 的效果
    这里 0x2A 是 7位地址 => 8位写地址 0x54
    """
    # cmd_buf 放寄存器地址
    cmd_buf = bytearray(1)
    cmd_buf[0] = reg_addr & 0x7F  # 是否要加读写标记，视具体模块要求而定

    # data_buf 放要写的 1字节数据
    data_buf = bytearray(1)
    data_buf[0] = data_val & 0xFF

    # 注意：必须传 5 个参数
    i2c_obj.write(RC522_I2C_ADDR, cmd_buf, 1, data_buf, 1)


def i2c_read_reg(i2c_obj, reg_addr):
    """
    模拟 stm32 中 HAL_I2C_Mem_Read(&hi2c1, 0x55, memaddr, 1, &data, 1, ...)
    这里 0x2A 是 7位地址 => 8位读地址 0x55
    """
    # cmd_buf 放寄存器地址
    cmd_buf = bytearray(1)
    # 如果你的 I2C RC522 要求读时 reg_addr |= 0x80，可在此加
    cmd_buf[0] = reg_addr & 0x7F

    # data_buf 接收1字节
    data_buf = bytearray(1)

    # 6个参数: devAddr, cmd_buf, cmd_len, data_buf, data_len, stop(0或1)
    # stop=0/1 依实际需要
    i2c_obj.read(RC522_I2C_ADDR, cmd_buf, 1, data_buf, 1, 0)
    return data_buf[0]

# --------------------------------------------------------------------------------
# RC522 基本函数移植 (对应 rc522.c)
# --------------------------------------------------------------------------------

def rc522_reset_pin_set():
    """
    若你硬件上连接了 RC522 RST 引脚到某个 GPIO，可在此拉高
    如果没有，则可忽略
    """
    # 示例：若无单独复位引脚，就留空
    pass

def rc522_reset_pin_clr():
    """
    若你硬件上连接了 RC522 RST 引脚，可在此拉低
    """
    pass

def MFRC_Init():
    """
    对应 C 代码里的 MFRC_Init()：
    RS522_RST_SET
    """
    rc522_reset_pin_set()

def MFRC_WriteReg(i2c_obj, memaddr, data_val):
    """
    对应 stm32 里的:
        HAL_I2C_Mem_Write(&hi2c1, 0x54, memaddr, I2C_MEMADD_SIZE_8BIT, &data, 1, ...)
    """
    i2c_write_reg(i2c_obj, memaddr, data_val)

def MFRC_ReadReg(i2c_obj, memaddr):
    """
    对应 stm32 里的:
        HAL_I2C_Mem_Read(&hi2c1, 0x55, memaddr, I2C_MEMADD_SIZE_8BIT, &data, 1, ...)
    """
    return i2c_read_reg(i2c_obj, memaddr)

def MFRC_SetBitMask(i2c_obj, addr, mask):
    temp = MFRC_ReadReg(i2c_obj, addr)
    MFRC_WriteReg(i2c_obj, addr, temp | mask)

def MFRC_ClrBitMask(i2c_obj, addr, mask):
    temp = MFRC_ReadReg(i2c_obj, addr)
    MFRC_WriteReg(i2c_obj, addr, temp & (~mask))

def MFRC_CalulateCRC(i2c_obj, pInData):
    """
    计算 CRC。对应 rc522.c 的 MFRC_CalulateCRC()
    pInData: 待算的数据(列表或字节数组)
    返回: [low, high]
    """
    # 1) 使能 CRC 中断
    MFRC_ClrBitMask(i2c_obj, MFRC_DivIrqReg, 0x04)
    # 2) 取消当前命令
    MFRC_WriteReg(i2c_obj, MFRC_CommandReg, MFRC_IDLE)
    # 3) FIFO 清空
    MFRC_SetBitMask(i2c_obj, MFRC_FIFOLevelReg, 0x80)

    # 写入数据
    for b in pInData:
        MFRC_WriteReg(i2c_obj, MFRC_FIFODataReg, b)

    # 启动计算
    MFRC_WriteReg(i2c_obj, MFRC_CommandReg, MFRC_CALCCRC)

    i = 0xFFFF
    while True:
        n = MFRC_ReadReg(i2c_obj, MFRC_DivIrqReg)
        if n & 0x04:  # 表示计算完成
            break
        i -= 1
        if i == 0:
            # 超时
            return [0, 0]
    # 取结果
    l_val = MFRC_ReadReg(i2c_obj, MFRC_CRCResultRegL)
    m_val = MFRC_ReadReg(i2c_obj, MFRC_CRCResultRegM)
    return [l_val, m_val]

def MFRC_CmdFrame(i2c_obj, cmd, pInData):
    """
    对应 rc522.c 中的 MFRC_CmdFrame
    pInData: 要发送到 FIFO 的字节列表
    返回: (status, outDataList, outLenBit)
    """
    # 根据 cmd 设置 irqEn / waitFor (简化)
    if cmd == MFRC_AUTHENT:
        irqEn = 0x12
        waitFor = 0x10
    elif cmd == MFRC_TRANSCEIVE:
        irqEn = 0x77
        waitFor = 0x30
    else:
        irqEn = 0x00
        waitFor = 0x00

    # 1) 设置中断
    MFRC_WriteReg(i2c_obj, MFRC_ComIEnReg, irqEn | 0x80)  # 使能中断
    MFRC_ClrBitMask(i2c_obj, MFRC_ComIrqReg, 0x80)        # 清中断标志
    MFRC_WriteReg(i2c_obj, MFRC_CommandReg, MFRC_IDLE)    # 取消当前命令
    MFRC_SetBitMask(i2c_obj, MFRC_FIFOLevelReg, 0x80)     # FIFO flush

    # 2) 把 pInData 写入 FIFO
    for b in pInData:
        MFRC_WriteReg(i2c_obj, MFRC_FIFODataReg, b)

    # 3) 启动命令
    MFRC_WriteReg(i2c_obj, MFRC_CommandReg, cmd)
    if cmd == MFRC_TRANSCEIVE:
        # 启动发送
        MFRC_SetBitMask(i2c_obj, MFRC_BitFramingReg, 0x80)

    # 4) 等待完成
    i = 20000
    outData = []
    outLenBit = 0
    while True:
        n = MFRC_ReadReg(i2c_obj, MFRC_ComIrqReg)
        if (n & 0x01) or (n & waitFor):
            break
        i -= 1
        if i == 0:
            return (MFRC_ERR, outData, outLenBit)

    # 停止发送
    MFRC_ClrBitMask(i2c_obj, MFRC_BitFramingReg, 0x80)

    # 5) 判断错误
    errorVal = MFRC_ReadReg(i2c_obj, MFRC_ErrorReg)
    if errorVal & 0x1B:  # 有错误
        return (MFRC_ERR, outData, outLenBit)

    # 6) 读取 FIFO 返回数据
    if cmd == MFRC_TRANSCEIVE:
        fifo_level = MFRC_ReadReg(i2c_obj, MFRC_FIFOLevelReg)
        controlVal = MFRC_ReadReg(i2c_obj, MFRC_ControlReg)
        lastBits = controlVal & 0x07

        outLenBit = fifo_level * 8
        if lastBits:
            outLenBit = (fifo_level - 1) * 8 + lastBits

        for _ in range(fifo_level):
            outData.append(MFRC_ReadReg(i2c_obj, MFRC_FIFODataReg))

    return (MFRC_OK, outData, outLenBit)

def PCD_Reset(i2c_obj):
    """
    对应 rc522.c 里的 PCD_Reset()
    先硬复位(如果有 RST 引脚),再软复位
    """
    # 硬复位(若有专门的复位 GPIO)
    rc522_reset_pin_set()
    utime.sleep_ms(5)
    rc522_reset_pin_clr()
    utime.sleep_ms(5)
    rc522_reset_pin_set()
    utime.sleep_ms(5)

    # 软复位
    MFRC_WriteReg(i2c_obj, MFRC_CommandReg, MFRC_RESETPHASE)
    utime.sleep_ms(5)

    # 复位后初始化配置
    MFRC_WriteReg(i2c_obj, MFRC_ModeReg, 0x3D)         # CRC初始值0x6363
    MFRC_WriteReg(i2c_obj, MFRC_TReloadRegL, 30)
    MFRC_WriteReg(i2c_obj, MFRC_TReloadRegH, 0)
    MFRC_WriteReg(i2c_obj, MFRC_TModeReg, 0x8D)
    MFRC_WriteReg(i2c_obj, MFRC_TPrescalerReg, 0x3E)
    MFRC_WriteReg(i2c_obj, MFRC_TxAutoReg, 0x40)

    PCD_AntennaOff(i2c_obj)
    utime.sleep_ms(2)
    PCD_AntennaOn(i2c_obj)

def PCD_AntennaOn(i2c_obj):
    """
    对应 rc522.c
    """
    temp = MFRC_ReadReg(i2c_obj, MFRC_TxControlReg)
    if (temp & 0x03) != 0x03:
        MFRC_SetBitMask(i2c_obj, MFRC_TxControlReg, 0x03)

def PCD_AntennaOff(i2c_obj):
    MFRC_ClrBitMask(i2c_obj, MFRC_TxControlReg, 0x03)

def PCD_Request(i2c_obj, requestMode, pCardType):
    """
    寻卡, 对应 rc522.c 中 PCD_Request
    pCardType 是一个列表,用于存放卡类型 [Byte0, Byte1]
    """
    # TxControlReg 置位
    MFRC_ClrBitMask(i2c_obj, MFRC_Status2Reg, 0x08)
    MFRC_WriteReg(i2c_obj, MFRC_BitFramingReg, 0x07)
    MFRC_SetBitMask(i2c_obj, MFRC_TxControlReg, 0x03)

    # 发送 cmd
    cmd_buf = [requestMode]
    (status, outData, outLenBit) = MFRC_CmdFrame(i2c_obj, MFRC_TRANSCEIVE, cmd_buf)
    if (status == MFRC_OK) and (outLenBit == 0x10):
        # outData[0], outData[1] => 卡类型
        pCardType[0] = outData[0]
        pCardType[1] = outData[1]
        return MFRC_OK
    else:
        return MFRC_ERR

def PCD_Anticoll(i2c_obj, pSnr):
    """
    防冲突，返回卡序列号
    pSnr: 用于存放UID, [uid0, uid1, uid2, uid3]
    """
    MFRC_ClrBitMask(i2c_obj, MFRC_Status2Reg, 0x08)
    MFRC_WriteReg(i2c_obj, MFRC_BitFramingReg, 0x00)
    MFRC_ClrBitMask(i2c_obj, MFRC_CollReg, 0x80)

    cmd_buf = [PICC_ANTICOLL1, 0x20]
    (status, outData, outLenBit) = MFRC_CmdFrame(i2c_obj, MFRC_TRANSCEIVE, cmd_buf)
    if status == MFRC_OK and len(outData) >= 5:
        # outData[0..3] => UID, outData[4] => BCC
        uid = outData[0:4]
        bcc = outData[4]
        # 校验
        snr_check = 0
        for i in range(4):
            pSnr[i] = uid[i]
            snr_check ^= uid[i]
        if snr_check != bcc:
            return MFRC_ERR
        MFRC_SetBitMask(i2c_obj, MFRC_CollReg, 0x80)
        return MFRC_OK
    else:
        return MFRC_ERR

def readCard(i2c_obj, readUid, funCallBack):
    """
    对应 main.c 中 readCard()
    - 先 PCD_Request(PICC_REQALL)
    - 若成功再 PCD_Anticoll
    - 若成功则回调
    - 返回 0 表示读卡成功，1 表示失败
    """
    temp = [0, 0]
    status_req = PCD_Request(i2c_obj, PICC_REQALL, temp)
    if status_req == MFRC_OK:
        # 再做防冲突
        status_anti = PCD_Anticoll(i2c_obj, readUid)
        if status_anti == MFRC_OK:
            if funCallBack is not None:
                funCallBack()
            return 0
    return 1

def PCD_Init(i2c_obj):
    """
    对应 rc522.c 中 PCD_Init()
    """
    MFRC_Init()      # 管脚配置(RST拉高等)
    PCD_Reset(i2c_obj)
    PCD_AntennaOff(i2c_obj)
    PCD_AntennaOn(i2c_obj)


# --------------------------------------------------------------------------------
# 主函数，参考 STM32 main.c 的流程:
#  - 定义 readUid[5]
#  - 定义 UID[4] 用于比对
#  - 初始化 RC522
#  - 轮询读卡 => 打印卡号 => 与 UID 比较
# --------------------------------------------------------------------------------

def main():
    # 1) 创建 I2C 对象
    #    根据你的 EC600M 引脚选择，如 I2C0, I2C1, 并配置速率
    i2c_obj = I2C(I2C.I2C2, I2C.STANDARD_MODE)

    # 2) 初始化 RC522
    PCD_Init(i2c_obj)
    logger.info("RC522 init complete")

    # 用于保存读取到的卡UID
    readUid = [0, 0, 0, 0, 0]  # 第 4 个字节可留作 BCC

    while True:
        # 每次循环尝试读卡
        ret = readCard(i2c_obj, readUid, None)
        if ret == 0:
            # 成功读取
            # readUid[0..3] 就是卡号
            logger.info("卡号: %X-%X-%X-%X" % (readUid[0], readUid[1], readUid[2], readUid[3]))

        utime.sleep_ms(1000)  # 1秒循环一次

# 入口
if __name__ == "__main__":
    main()
