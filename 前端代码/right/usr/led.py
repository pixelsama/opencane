# -*- coding: UTF-8 -*-
import utime
import _thread
import machine
from machine import SPI
from usr import parameter as pa

##############################################################################
# 1) 用户可调参数：灯数、模式、各模式颜色、动画速度等
##############################################################################
LED_COUNT = 1  # 这里设定灯带上有多少颗灯 (可自行修改)
MODE = 0       # 当前模式：0=常亮, 1=流水, 2=呼吸, 3=炫彩
BRIGHTNESS = 0x10  # 全局亮度(0~0xFF); 也可在各模式里细调

# 三种基本模式的演示颜色(可自行修改)
COLOR_SOLID    = (  125, 125,   255 )  # 绿色
COLOR_FLOW     = (255,   0,   0 )  # 红色
COLOR_BREATH   = (  0,   0, 255 )  # 蓝色
# 炫彩模式不需要单色，每个灯会呈现彩虹色

# 动画速度调节（毫秒）
FLOW_SPEED_MS     = 100
BREATH_SPEED_MS   = 2
RAINBOW_SPEED_MS  = 300
SOLID_REFRESH_MS  = 500  # 常亮可以刷新慢一些

##############################################################################
# 2) 计算 SPI 发送缓冲区长度
#    每颗灯需要 24 位，但现在是 16倍展开 => 24*16=384 bit => 48 字节/灯
#    额外 +20 字节用于复位信号, +1 字节预留(可做头部标识)
##############################################################################
BYTES_PER_LED = 48   # 24 bits -> each bit => 16 SPI bits => 2 bytes => 24*2=48
BUF_LEN = LED_COUNT * BYTES_PER_LED + 1
w_data = bytearray(BUF_LEN)  # 全局发送缓冲

##############################################################################
# 3) 初始化硬件 SPI(参数因平台/固件不同而异，注意引脚连线和时钟频率)
##############################################################################
spi_obj = SPI(1, 0, 4)  # 你自行设定频率/极性等

##############################################################################
# 4) 基础工具函数：打包颜色、写入缓冲区、刷新SPI
##############################################################################

def pack_color(r, g, b):
    """
    将 R/G/B(0~255) 各通道值转换为 WS2812 所需的 GRB 顺序 24位整数:
      最高字节 G, 中间字节 R, 最低字节 B
    同时可做全局亮度衰减(BRIGHTNESS)。
    """
    # 简单做个全局亮度衰减
    r = (r * BRIGHTNESS) >> 8
    g = (g * BRIGHTNESS) >> 8
    b = (b * BRIGHTNESS) >> 8
    return ((g & 0xFF) << 16) | ((r & 0xFF) << 8) | (b & 0xFF)


def clear_buffer():
    """将全局缓冲区 w_data 清零(或填充默认值)"""
    for i in range(BUF_LEN):
        w_data[i] = 0


def show_buffer(led_count):
    """
    将 w_data 中 [0..(led_count*48 + 20 + 1)) 区域写出到 SPI，
    注意末尾要补 20 字节的低电平(这里写 0x00)保证复位/锁存。
    """
    global spi_obj
    send_len = led_count*BYTES_PER_LED + 1
    spi_obj.write(w_data, send_len)

    # 如果你想强制拉低某IO，可以在此操作；以下是示例:
    led_port = machine.Pin(20, machine.Pin.OUT)
    led_port.write(0)
    utime.sleep_us(50)

    # 重新初始化SPI(根据平台需求，可有可无)
    spi_obj = SPI(1, 0, 4)


##############################################################################
# 这里最核心的：16 倍展开
# 定义 WS2812 "0" 和 "1" 的16位模式(高电平/低电平分布)
##############################################################################
# 比如我们定义:
#  逻辑0 -> 0xF800 => 二进制: 1111 1000 0000 0000
#     (高5 bit, 低11 bit)
#  逻辑1 -> 0xFFC0 => 二进制: 1111 1111 1100 0000
#     (高10 bit, 低6 bit)
ws0_pattern = 0xF800
ws1_pattern = 0xFFC0

def set_pixel_color(n, color_24bit):
    """
    将第 n 颗灯设置为 color_24bit (GRB 24位整数), 但不立即刷新SPI。
    采用 16 倍展开 => 每 bit -> 2 字节, 共 48 字节/颗。
    n 从 0 开始计数。
    """
    if n < 0 or n >= LED_COUNT:
        return

    # 本灯在 w_data 里的起始位置(+1 是因为我们保留了 w_data[0] 用做头部标识)
    base = n * BYTES_PER_LED + 1

    # 从最高位(G通道bit7)到最低位(B通道bit0)循环
    temp = 1 << 23  # 对应最高 bit(23)
    for i in range(24):
        if (color_24bit & temp):
            pattern16 = ws1_pattern
        else:
            pattern16 = ws0_pattern
        temp >>= 1

        # 把 16 位 pattern16 拆成 2 字节写入 w_data
        byte_index = base + i*2
        w_data[byte_index]   = (pattern16 >> 8) & 0xFF  # 高字节
        w_data[byte_index+1] = pattern16 & 0xFF         # 低字节


def fill_color(color_24bit):
    """将所有灯设为同一颜色(已打包)"""
    for n in range(LED_COUNT):
        set_pixel_color(n, color_24bit)


##############################################################################
# 5) 各模式的“单步更新”函数：同你原先逻辑，只是底层改为16倍展开
##############################################################################

# --- 流水模式需要一个全局步进(flow_index)，以确定亮点位置 ---
flow_index = 0

def update_flow_mode(base_color):
    global flow_index
    clear_buffer()
    w_data[0] = 0x00  # 包头(可随意)

    for i in range(LED_COUNT):
        if i == flow_index:
            set_pixel_color(i, pack_color(*base_color))
        else:
            set_pixel_color(i, 0)  # 全灭

    # 复位/锁存字节(20个)
    # for k in range(64):
    #     w_data[LED_COUNT*BYTES_PER_LED + k + 1] = 0x00

    show_buffer(LED_COUNT)
    flow_index = (flow_index + 1) % LED_COUNT


# --- 呼吸模式 ---
breath_phase = 0
breath_direction = 1

def update_breath_mode(base_color):
    global breath_phase, breath_direction
    alpha = breath_phase / 255.0
    r0, g0, b0 = base_color
    r = int(r0 * alpha)
    g = int(g0 * alpha)
    b = int(b0 * alpha)

    cur_color = pack_color(r,g,b)
    clear_buffer()
    w_data[0] = 0x00
    for i in range(LED_COUNT):
        set_pixel_color(i, cur_color)

    # for k in range(64):
    #     w_data[LED_COUNT*BYTES_PER_LED + k + 1] = 0x00
    show_buffer(LED_COUNT)

    breath_phase += breath_direction * 5
    if breath_phase >= 255:
        breath_phase = 255
        breath_direction = -1
    elif breath_phase <= 0:
        breath_phase = 0
        breath_direction = 1


# --- 炫彩模式(彩虹循环) ---
rainbow_offset = 0

def color_wheel(pos):
    if pos < 85:
        return pack_color(pos * 3, 255 - pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return pack_color(255 - pos * 3, 0, pos * 3)
    else:
        pos -= 170
        return pack_color(0, pos * 3, 255 - pos * 3)

def update_rainbow_mode():
    global rainbow_offset
    clear_buffer()
    w_data[0] = 0x00

    for i in range(LED_COUNT):
        idx = (i * 256 // LED_COUNT + rainbow_offset) & 0xFF
        c24 = color_wheel(idx)
        set_pixel_color(i, c24)

    # for k in range(64):
    #     w_data[LED_COUNT*BYTES_PER_LED + k + 1] = 0x00
    show_buffer(LED_COUNT)

    rainbow_offset = (rainbow_offset + 5) & 0xFF


def turn_off_leds():
    clear_buffer()
    w_data[0] = 0x00
    for n in range(LED_COUNT):
        set_pixel_color(n, 0)
    # for k in range(64):  # 发送 64 字节 0x00
    #     w_data[LED_COUNT * BYTES_PER_LED + k + 1] = 0x00
    show_buffer(LED_COUNT)
    print('LEDs are turned off.')
    # 如果需要关机 GPIO，可以自行加


##############################################################################
# 6) 线程循环：根据当前 MODE，反复执行对应的“单步更新”，并延时
##############################################################################
def WS2812_Thread():
    while True:
        if pa.led_mode == pa.LED_ON:
            # 常亮模式
            solid_col = pack_color(*COLOR_SOLID)
            clear_buffer()
            w_data[0] = 0x00
            fill_color(solid_col)
            # for k in range(64):
            #     w_data[LED_COUNT*BYTES_PER_LED + k + 1] = 0x00
            show_buffer(LED_COUNT)
            utime.sleep_ms(SOLID_REFRESH_MS)

        elif pa.led_mode == pa.LED_OFF:
            turn_off_leds()
            pa.led_mode = pa.LED_IDLE
            utime.sleep_ms(10)

        elif pa.led_mode == pa.LED_FLOW:
            update_flow_mode(COLOR_FLOW)
            utime.sleep_ms(FLOW_SPEED_MS)

        elif pa.led_mode == pa.LED_BREATH:
            update_rainbow_mode()
            utime.sleep_ms(RAINBOW_SPEED_MS)
            # update_breath_mode(COLOR_BREATH)
            # utime.sleep_ms(BREATH_SPEED_MS)

        elif pa.led_mode == pa.LED_RAINBOW:
            update_rainbow_mode()
            utime.sleep_ms(RAINBOW_SPEED_MS)

        else:
            utime.sleep_ms(50)

##############################################################################
# 7) 主程序入口：启动独立线程
##############################################################################
def led_init():
    _thread.start_new_thread(WS2812_Thread, ())
