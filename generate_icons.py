#!/usr/bin/env python3
"""从 SVG 源文件生成 PNG 托盘图标

用法：
    pip install cairosvg
    python generate_icons.py

会在 resources/icons/ 下生成 4 个 PNG 文件：
- venlta-stopped.png   深蓝灰底 — 未启动代理
- venlta-running.png   深红底   — 启动了代理（系统代理）
- venlta-tun.png       深绿底   — 启动了 TUN
- venlta-both.png      深紫底   — 系统代理 + TUN 都启动
"""

from pathlib import Path

try:
    import cairosvg
except ImportError:
    print("请先安装 cairosvg: pip install cairosvg")
    raise SystemExit(1)

ICONS_DIR = Path(__file__).parent / "resources" / "icons"

SVG_FILES = [
    "venlta-stopped",
    "venlta-running",
    "venlta-tun",
    "venlta-both",
]


def main():
    for name in SVG_FILES:
        svg_path = ICONS_DIR / f"{name}.svg"
        png_path = ICONS_DIR / f"{name}.png"

        if not svg_path.exists():
            print(f"⚠ 跳过 {name}：SVG 源文件不存在 ({svg_path})")
            continue

        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=512,
            output_height=512,
        )
        print(f"✓ {name}.svg → {name}.png")

    print("\n完成！")


if __name__ == "__main__":
    main()
