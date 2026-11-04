import math
import os
from PIL import Image, ImageDraw, ImageFilter

def draw_dollyteria_badge(size=512):
    """
    绘制 Dollyteria 专用蓝色高质感图标：
    - 渐变蓝色高识别度 Squircle 徽章底座
    - 纯白粗体品牌大写字母 "D"
    - 右上角标志性洋娃娃蝴蝶结 (Ribbon Bow)
    - 右下角闪烁星芒 (Sparkle)
    - 细腻微光与高光描边
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 1. 蓝色渐变徽章背景 (Squircle)
    margin = int(size * 0.046)  # 约 24px
    rect = [margin, margin, size - margin, size - margin]
    radius = int(size * 0.26)    # 约 133px

    # 渐变背景 (左上浅天蓝 #38bdf8 -> 中间宝蓝 #2563eb -> 右下沉稳海蓝 #1d4ed8)
    bg_gradient = Image.new("RGBA", (size, size))
    for y in range(size):
        for x in range(size):
            # 对角线渐变因子
            t = (x * 0.4 + y * 0.6) / size
            t = max(0.0, min(1.0, t))
            if t < 0.4:
                sub_t = t / 0.4
                # #38bdf8 (56, 189, 248) -> #2563eb (37, 99, 235)
                r = int(56 + (37 - 56) * sub_t)
                g = int(189 + (99 - 189) * sub_t)
                b = int(248 + (235 - 248) * sub_t)
            else:
                sub_t = (t - 0.4) / 0.6
                # #2563eb (37, 99, 235) -> #1e3a8a (30, 58, 138)
                r = int(37 + (30 - 37) * sub_t)
                g = int(99 + (58 - 99) * sub_t)
                b = int(235 + (138 - 235) * sub_t)

            bg_gradient.putpixel((x, y), (r, g, b, 255))

    # 徽章蒙版
    badge_mask = Image.new("L", (size, size), 0)
    b_draw = ImageDraw.Draw(badge_mask)
    b_draw.rounded_rectangle(rect, radius=radius, fill=255)

    img.paste(bg_gradient, (0, 0), badge_mask)

    # 2. 顶部弧形柔光 (增强苹果 App 图标式质感)
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gloss_draw = ImageDraw.Draw(gloss)
    gloss_draw.ellipse([margin - int(size * 0.2), margin - int(size * 0.35), size - margin + int(size * 0.2), int(size * 0.52)], fill=(255, 255, 255, 45))
    gloss_composite = Image.composite(gloss, Image.new("RGBA", (size, size), (0, 0, 0, 0)), badge_mask)
    img = Image.alpha_composite(img, gloss_composite)

    # 3. 外边框微描边 (亮色边缘增强轮廓，在暗色/亮色标签页都一目了然)
    border_draw = ImageDraw.Draw(img)
    border_draw.rounded_rectangle(rect, radius=radius, outline=(255, 255, 255, 110), width=int(size * 0.016))

    # 4. 前景图层 (字母 D + 蝴蝶结 + 星芒)
    fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fg_draw = ImageDraw.Draw(fg)

    # (A) 大写字母 "D"
    d_x0 = int(size * 0.22)
    d_y0 = int(size * 0.20)
    d_x1 = int(size * 0.77)
    d_y1 = int(size * 0.80)

    stem_w = int(size * 0.14)
    bar_h = int(size * 0.13)

    # D 外轮廓
    d_outer = Image.new("L", (size, size), 0)
    d_o_draw = ImageDraw.Draw(d_outer)
    d_o_draw.rectangle([d_x0, d_y0, d_x0 + (d_x1 - d_x0) * 0.45, d_y1], fill=255)
    d_o_draw.rounded_rectangle([d_x0, d_y0, d_x1, d_y1], radius=int((d_y1 - d_y0) * 0.48), fill=255)

    # D 内镂空
    d_inner = Image.new("L", (size, size), 0)
    d_i_draw = ImageDraw.Draw(d_inner)
    in_x0 = d_x0 + stem_w
    in_y0 = d_y0 + bar_h
    in_x1 = d_x1 - int(size * 0.13)
    in_y1 = d_y1 - bar_h
    d_i_draw.rectangle([in_x0, in_y0, in_x0 + (in_x1 - in_x0) * 0.4, in_y1], fill=255)
    d_i_draw.rounded_rectangle([in_x0, in_y0, in_x1, in_y1], radius=int((in_y1 - in_y0) * 0.45), fill=255)

    d_mask = Image.new("L", (size, size), 0)
    for y in range(size):
        for x in range(size):
            if d_outer.getpixel((x, y)) > 0 and d_inner.getpixel((x, y)) == 0:
                d_mask.putpixel((x, y), 255)

    white_layer = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    fg.paste(white_layer, (0, 0), d_mask)

    # (B) 娃娃蝴蝶结 (别在 D 的右上方)
    bx = int(size * 0.75)
    by = int(size * 0.24)
    bw = int(size * 0.16)
    bh = int(size * 0.12)

    bow_mask = Image.new("L", (size, size), 0)
    bow_draw = ImageDraw.Draw(bow_mask)

    # 左翼 (可爱圆润三角形)
    left_wing = [
        (bx - int(bw * 0.15), by),
        (bx - bw, by - int(bh * 0.75)),
        (bx - int(bw * 0.9), by + int(bh * 0.75)),
    ]
    bow_draw.polygon(left_wing, fill=255)

    # 右翼
    right_wing = [
        (bx + int(bw * 0.15), by),
        (bx + bw, by - int(bh * 0.75)),
        (bx + int(bw * 0.9), by + int(bh * 0.75)),
    ]
    bow_draw.polygon(right_wing, fill=255)

    # 飘带
    rib_l = [
        (bx - int(bw * 0.1), by + int(bh * 0.2)),
        (bx - int(bw * 0.65), by + int(bh * 1.35)),
        (bx - int(bw * 0.4), by + int(bh * 1.45)),
        (bx, by + int(bh * 0.4)),
    ]
    bow_draw.polygon(rib_l, fill=255)

    rib_r = [
        (bx + int(bw * 0.1), by + int(bh * 0.2)),
        (bx + int(bw * 0.65), by + int(bh * 1.35)),
        (bx + int(bw * 0.4), by + int(bh * 1.45)),
        (bx, by + int(bh * 0.4)),
    ]
    bow_draw.polygon(rib_r, fill=255)

    # 中心圆结
    kr = int(bw * 0.28)
    bow_draw.ellipse([bx - kr, by - kr, bx + kr, by + kr], fill=255)

    bow_color = Image.new("RGBA", (size, size), (240, 249, 255, 255))
    fg.paste(bow_color, (0, 0), bow_mask)

    # (C) 闪烁四角星芒 (右下方)
    sx = int(size * 0.82)
    sy = int(size * 0.70)
    sr = int(size * 0.07)
    star_mask = Image.new("L", (size, size), 0)
    star_draw = ImageDraw.Draw(star_mask)
    star_pts = [
        (sx, sy - sr),
        (sx + int(sr * 0.28), sy - int(sr * 0.28)),
        (sx + sr, sy),
        (sx + int(sr * 0.28), sy + int(sr * 0.28)),
        (sx, sy + sr),
        (sx - int(sr * 0.28), sy + int(sr * 0.28)),
        (sx - sr, sy),
        (sx - int(sr * 0.28), sy - int(sr * 0.28)),
    ]
    star_draw.polygon(star_pts, fill=255)
    star_color = Image.new("RGBA", (size, size), (224, 242, 254, 255))
    fg.paste(star_color, (0, 0), star_mask)

    # 5. 前景柔和投影
    fg_alpha = fg.split()[3]
    shadow_mask = fg_alpha.filter(ImageFilter.GaussianBlur(radius=int(size * 0.02)))
    shadow = Image.new("RGBA", (size, size), (15, 23, 42, 110))
    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_layer.paste(shadow, (0, int(size * 0.02)), shadow_mask)

    # 最终合成
    final = Image.alpha_composite(img, shadow_layer)
    final = Image.alpha_composite(final, fg)
    return final

def main():
    static_dir = os.path.join("web", "static")
    os.makedirs(static_dir, exist_ok=True)

    print("Generating 512x512 master icon...")
    master = draw_dollyteria_badge(512)

    # 1. 保存 512x512 PNG
    master.save(os.path.join(static_dir, "icon-512.png"), "PNG")

    # 2. 保存 192x192 PNG (PWA / WebApp)
    icon_192 = master.resize((192, 192), Image.Resampling.LANCZOS)
    icon_192.save(os.path.join(static_dir, "icon-192.png"), "PNG")

    # 3. 保存 180x180 PNG (Apple Touch Icon)
    apple_icon = master.resize((180, 180), Image.Resampling.LANCZOS)
    apple_icon.save(os.path.join(static_dir, "apple-touch-icon.png"), "PNG")

    # 4. 保存 32x32 PNG (Favicon 32x32)
    icon_32 = master.resize((32, 32), Image.Resampling.LANCZOS)
    icon_32.save(os.path.join(static_dir, "favicon-32x32.png"), "PNG")

    # 5. 保存 16x16 PNG (Favicon 16x16)
    icon_16 = master.resize((16, 16), Image.Resampling.LANCZOS)
    icon_16.save(os.path.join(static_dir, "favicon-16x16.png"), "PNG")

    # 6. 打包保存多尺寸 favicon.ico (包含 16x16, 32x32, 48x48)
    icon_48 = master.resize((48, 48), Image.Resampling.LANCZOS)
    ico_path = os.path.join(static_dir, "favicon.ico")
    icon_48.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[icon_32, icon_16]
    )
    print(f"favicon.ico successfully generated at {ico_path}")

    # 7. 生成纯矢量 SVG 图标
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="100%" height="100%">
  <defs>
    <!-- 经典深蓝到天蓝科技感渐变 (高辨识度) -->
    <linearGradient id="dollyBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="42%" stop-color="#2563eb" />
      <stop offset="100%" stop-color="#1d4ed8" />
    </linearGradient>

    <!-- 顶部轻微玻璃感高光 -->
    <linearGradient id="dollyGloss" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.36" />
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
    </linearGradient>

    <!-- 柔和微投影 -->
    <filter id="dollyDropShadow" x="-15%" y="-15%" width="130%" height="130%">
      <feDropShadow dx="0" dy="2.5" stdDeviation="2.5" flood-color="#0f172a" flood-opacity="0.4" />
    </filter>
  </defs>

  <!-- 外层圆角矩形高质感蓝色徽章 -->
  <rect x="6" y="6" width="116" height="116" rx="30" ry="30" fill="url(#dollyBg)" stroke="rgba(255,255,255,0.45)" stroke-width="2.5" />

  <!-- 顶部轻微玻璃弧光 -->
  <path d="M 6,36 C 6,19 19,6 36,6 L 92,6 C 109,6 122,19 122,36 C 122,52 86,64 6,52 Z" fill="url(#dollyGloss)" />

  <!-- 核心主体内容 (带微阴影) -->
  <g filter="url(#dollyDropShadow)">
    <!-- Dollyteria 品牌大写字母 D -->
    <path fill-rule="evenodd" fill="#ffffff" d="M 28,26 L 66,26 C 89,26 103,42 103,64 C 103,86 89,102 66,102 L 28,102 Z M 48,44 L 62,44 C 74,44 82,53 82,64 C 82,75 74,84 62,84 L 48,84 Z" />

    <!-- Dolly 娃娃特征：右上角可爱白色蝴蝶结 (Bowknot) -->
    <!-- 左蝶翼 -->
    <path d="M 91,29 C 78,19 71,22 69,28 C 67,35 77,39 89,32 Z" fill="#f0f9ff" />
    <!-- 右蝶翼 -->
    <path d="M 96,29 C 109,19 116,22 118,28 C 120,35 110,39 98,32 Z" fill="#f0f9ff" />
    <!-- 飘带 -->
    <path d="M 90,32 C 85,39 79,46 73,50 C 78,46 86,40 92,33 Z" fill="#f0f9ff" />
    <path d="M 97,32 C 102,39 108,46 114,50 C 109,46 101,40 95,33 Z" fill="#f0f9ff" />
    <!-- 中心结扣 -->
    <circle cx="93.5" cy="30.5" r="4.8" fill="#ffffff" stroke="#bae6fd" stroke-width="0.8" />

    <!-- 右下角闪烁四角星芒 (上新好物闪光) -->
    <path d="M 104,82 Q 104,90 112,90 Q 104,90 104,98 Q 104,90 96,90 Q 104,90 104,82 Z" fill="#ffffff" />
    <circle cx="104" cy="90" r="1.5" fill="#38bdf8" />
  </g>
</svg>
'''
    svg_path = os.path.join(static_dir, "favicon.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content.strip())
    print(f"favicon.svg successfully generated at {svg_path}")

if __name__ == "__main__":
    main()
