#!/usr/bin/env python3
"""生成托盘状态图标

从 venlta.png 生成 5 个状态图标：
- venlta-stopped.png       灰色 — 已停止
- venlta-running.png       绿色 — 运行中
- venlta-system-proxy.png  蓝色 — 系统代理
- venlta-tun.png           红棕 — TUN
- venlta-both.png          紫色 — 系统代理 + TUN

每个图标：圆角矩形底色 + 白色剪影 logo + 细边框
"""
from PIL import Image, ImageDraw, ImageFilter
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.join(SCRIPT_DIR, "resources", "icons")

# 状态颜色 (R, G, B)
STATUSES = {
    "stopped":       (100, 116, 139),   # 石板灰
    "running":       (22, 163, 74),     # 绿色
    "system-proxy":  (37, 99, 235),     # 蓝色
    "tun":           (185, 28, 28),     # 红棕
    "both":          (126, 34, 206),    # 紫色
}

SIZE = 512
CORNER_RADIUS = 96  # 与原 SVG 的 rx=96 一致
MARGIN = 12  # 留出边框空间


def create_rounded_rect(size, radius, color):
    """创建圆角矩形"""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [(0, 0), (size[0] - 1, size[1] - 1)],
        radius=radius,
        fill=color
    )
    return img


def create_logo_mask(size):
    """从 venlta.png 提取 logo 蒙版（alpha 通道）"""
    src = Image.open(os.path.join(ICONS_DIR, "venlta.png")).convert("RGBA")
    src = src.resize(size, Image.LANCZOS)
    # 提取 alpha 通道作为蒙版
    return src.split()[3]


def tint_logo(logo_mask, color, alpha=230, canvas_size=None):
    """用指定颜色填充 logo 蒙版，可选居中放置在更大画布上"""
    img = Image.new("RGBA", logo_mask.size, (0, 0, 0, 0))
    r, g, b = color
    img.paste((r, g, b, alpha), mask=logo_mask)
    if canvas_size and canvas_size != logo_mask.size:
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        # 居中放置
        x = (canvas_size[0] - logo_mask.size[0]) // 2
        y = (canvas_size[1] - logo_mask.size[1]) // 2
        canvas.paste(img, (x, y), mask=img)
        return canvas
    return img


def create_status_icon(bg_color, logo_mask):
    """创建一个状态图标"""
    # 1. 圆角矩形背景
    canvas_size = (SIZE, SIZE)
    bg = create_rounded_rect(canvas_size, CORNER_RADIUS, bg_color + (255,))

    # 2. 在背景上画一个稍深的内边框（增加立体感）
    border_img = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_img)
    # 外边框（浅色，高光）
    border_draw.rounded_rectangle(
        [(2, 2), (SIZE - 3, SIZE - 3)],
        radius=CORNER_RADIUS - 1,
        outline=(255, 255, 255, 40),
        width=2
    )
    # 内边框（深色，阴影）
    border_draw.rounded_rectangle(
        [(4, 4), (SIZE - 5, SIZE - 5)],
        radius=CORNER_RADIUS - 2,
        outline=(0, 0, 0, 50),
        width=1
    )

    # 3. 白色剪影 logo（居中放置在 SIZE x SIZE 画布上）
    white_logo = tint_logo(logo_mask, (255, 255, 255), alpha=230, canvas_size=canvas_size)

    # 4. 合成
    result = bg
    result = Image.alpha_composite(result, border_img)
    result = Image.alpha_composite(result, white_logo)

    return result


def main():
    # 提取 logo 蒙版（从原始图标）
    logo_inner_size = (SIZE - MARGIN * 2, SIZE - MARGIN * 2)
    logo_mask = create_logo_mask(logo_inner_size)

    for status_name, color in STATUSES.items():
        icon = create_status_icon(color, logo_mask)
        filename = f"venlta-{status_name}.png"
        filepath = os.path.join(ICONS_DIR, filename)
        icon.save(filepath, "PNG")
        print(f"  ✓ {filename} ({color})")

    print(f"\n已生成 {len(STATUSES)} 个图标 → {ICONS_DIR}")


if __name__ == "__main__":
    main()
