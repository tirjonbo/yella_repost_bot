import os
import json
import re
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ── Config ──────────────────────────────────────────────────────────────────
YELLA_API_URL   = "https://yella.uz/yellaapi.php"
YELLA_API_SECRET = os.environ["YELLA_API_SECRET"]
BOT_TOKEN       = os.environ["BOT_TOKEN"]
TARGET_CHANNEL  = "@yellauz"
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
STATE_FILE      = "promo_state.json"

ASSETS_DIR  = "assets"
TEMPLATE    = f"{ASSETS_DIR}/card_template.png"
FONTS_DIR   = f"{ASSETS_DIR}/fonts"

# ── State ────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"posted_today": [], "last_reset_date": ""}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def reset_state_if_new_day(state):
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("last_reset_date") != today:
        state["posted_today"] = []
        state["last_reset_date"] = today
    return state

# ── Fetch promo products ──────────────────────────────────────────────────────
def fetch_promo_products(limit=20):
    try:
        resp = requests.post(
            YELLA_API_URL,
            data={"secret": YELLA_API_SECRET, "action": "get_promo_products", "limit": limit},
            timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("products", [])
    except Exception as e:
        print(f"API fetch error: {e}")
        return []

# ── Download product photo ───────────────────────────────────────────────────
def download_photo(url: str):
    try:
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            return Image.open(BytesIO(resp.content)).convert('RGBA')
        print(f"Photo download failed: {resp.status_code}")
    except Exception as e:
        print(f"Photo download error: {e}")
    return None

# ── Generate card image ───────────────────────────────────────────────────────
def format_price(p):
    return f"{int(p):,}".replace(',', ' ') + " сум"

def wrap_text(text, font, max_width, draw_obj, max_lines=2):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw_obj.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:max_lines]

def make_card(product_photo, product_name, price_old, price_new, discount_percent, output_path):
    W, H = 1080, 1080
    img = Image.open(TEMPLATE).convert('RGB')
    draw = ImageDraw.Draw(img, 'RGBA')

    f_name = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Medium.ttf', 38)
    f_price_new = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Bold.ttf', 56)
    f_price_old = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Medium.ttf', 34)
    f_badge = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-SemiBold.ttf', 28)
    f_footer = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Medium.ttf', 30)
    f_tag = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-SemiBold.ttf', 26)

    if product_photo is None:
        product_photo = Image.new('RGBA', (400, 400), (230, 230, 230, 255))

    max_size = 460
    product_photo = product_photo.copy()
    product_photo.thumbnail((max_size, max_size), Image.LANCZOS)
    photo_x = (W - product_photo.width) // 2
    photo_y = 110 + (560 - product_photo.height) // 2
    img.paste(product_photo, (photo_x, photo_y), product_photo)

    tag_top = 640
    tag_bottom = 700
    draw.rectangle([90, tag_top, W - 90, tag_bottom], fill=(26, 26, 26, 255))
    tag_text = "ЦЕНА ПО АКЦИИ · ТОЛЬКО СЕГОДНЯ"
    bbox = draw.textbbox((0, 0), tag_text, font=f_tag)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, tag_top + 12), tag_text, font=f_tag, fill=(204, 255, 0, 255))

    name_lines = wrap_text(product_name, f_name, 880, draw)
    name_y = 750
    for line in name_lines:
        draw.text((90, name_y), line, font=f_name, fill=(26, 26, 26, 255))
        name_y += 48

    price_y = name_y + 20

    new_price_text = format_price(price_new)
    old_price_text = format_price(price_old)

    bbox_new = draw.textbbox((0, 0), new_price_text, font=f_price_new)
    new_w = bbox_new[2] - bbox_new[0]
    badge_pad_x = 28
    badge_pad_y = 14
    badge_w = new_w + badge_pad_x * 2
    badge_h = (bbox_new[3] - bbox_new[1]) + badge_pad_y * 2
    draw.rounded_rectangle(
        [90, price_y, 90 + badge_w, price_y + badge_h],
        radius=14, fill=(204, 255, 0, 255)
    )
    draw.text((90 + badge_pad_x, price_y + badge_pad_y - bbox_new[1]), new_price_text, font=f_price_new, fill=(26, 26, 26, 255))

    old_x = 90 + badge_w + 24
    old_y = price_y + badge_h / 2 - 17
    draw.text((old_x, old_y), old_price_text, font=f_price_old, fill=(170, 170, 170, 255))
    bbox_old = draw.textbbox((old_x, old_y), old_price_text, font=f_price_old)
    strike_y = (bbox_old[1] + bbox_old[3]) / 2
    draw.line([bbox_old[0] - 2, strike_y, bbox_old[2] + 2, strike_y], fill=(170, 170, 170, 255), width=3)

    discount_text = f"-{discount_percent}%"
    bbox_disc = draw.textbbox((0, 0), discount_text, font=f_badge)
    disc_w = bbox_disc[2] - bbox_disc[0]
    draw.rounded_rectangle(
        [W - 90 - disc_w - 36, price_y + 4, W - 90, price_y + badge_h - 4],
        radius=12, fill=(255, 59, 48, 255)
    )
    draw.text((W - 90 - disc_w - 18, price_y + badge_h/2 - 18), discount_text, font=f_badge, fill=(255, 255, 255, 255))

    footer_y = 990
    draw.line([90, footer_y - 20, W - 90, footer_y - 20], fill=(230, 230, 230, 255), width=2)
    logo_text = "yella.uz"
    bbox_logo = draw.textbbox((0, 0), logo_text, font=f_footer)
    logo_w = bbox_logo[2] - bbox_logo[0]
    draw.text((W - 90 - logo_w, footer_y), logo_text, font=f_footer, fill=(26, 26, 26, 255))

    img.save(output_path, quality=95)
    return output_path

# ── Generate sales copy via Groq ─────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — копирайтер интернет-магазина электроники @yellauz в Узбекистане.

Тебе дают название товара, его описание и характеристики. Напиши короткий продающий текст для Telegram-поста.

Правила:
1. Структура: цепляющий триггер-фраза в начале (1 строка) + 2-3 ключевых преимущества списком через — (тире) + короткий призыв в конце
2. Стиль — живой, не канцелярский, без воды
3. Эмодзи — максимум 1-2 на весь текст, не больше
4. НЕ упоминай цену — она будет на картинке отдельно
5. НЕ пиши хэштеги
6. Используй HTML тег <b> для названия товара при первом упоминании

Формат ответа — строго JSON:
{
  "text": "Готовый текст поста с HTML тегами"
}"""

def generate_sales_copy(product_name, description):
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Товар: {product_name}\n\nОписание: {description}"}
                ],
                "temperature": 0.7,
                "max_tokens": 500
            },
            timeout=30
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        return result.get("text", "")
    except Exception as e:
        print(f"Groq error: {e}")
        return None

# ── Post to Telegram ─────────────────────────────────────────────────────────
def post_to_telegram(card_path, caption):
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    with open(card_path, 'rb') as f:
        files = {"photo": ("card.jpg", f, "image/jpeg")}
        data = {"chat_id": TARGET_CHANNEL, "caption": caption, "parse_mode": "HTML"}
        resp = requests.post(f"{base}/sendPhoto", data=data, files=files, timeout=30)
    if resp.status_code == 200:
        print("✅ Posted promo card")
        return True
    print(f"sendPhoto error: {resp.status_code} {resp.text[:300]}")
    return False

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    state = load_state()
    state = reset_state_if_new_day(state)
    posted_today = set(state.get("posted_today", []))

    print(f"Posted today: {len(posted_today)}")

    products = fetch_promo_products(limit=20)
    print(f"Fetched {len(products)} promo products")

    if not products:
        print("No promo products found. Done.")
        return

    # Берём первый ещё не запощенный сегодня товар (отсортированы по скидке убыв.)
    candidate = None
    for p in products:
        if str(p["product_id"]) not in posted_today:
            candidate = p
            break

    if not candidate:
        print("All promo products already posted today. Done.")
        return

    product_id = str(candidate["product_id"])
    name = candidate["name"]
    description = candidate.get("description", "")
    price_old = candidate["price"]
    price_new = candidate["special_price"]
    discount = candidate["discount_percent"]
    image_url = candidate["image_url"]
    product_url = candidate["product_url"]

    print(f"Selected product {product_id}: {name} (-{discount}%)")

    # Скачиваем фото
    photo = download_photo(image_url)
    if photo is None:
        print("Could not download photo, using placeholder")

    # Генерируем карточку
    card_path = "/tmp/promo_card.jpg"
    make_card(photo, name, price_old, price_new, discount, card_path)
    print(f"Card generated: {card_path}")

    # Генерируем продающий текст
    sales_text = generate_sales_copy(name, description)
    if not sales_text:
        sales_text = f"<b>{name}</b>\n\nАкция ограничена по времени — успейте купить по выгодной цене."

    caption = f"{sales_text}\n\n🔗 <a href=\"{product_url}\">Смотреть товар на сайте</a>\n\n⚡️ <a href=\"https://t.me/yellauz\">@yellauz</a>"

    if post_to_telegram(card_path, caption):
        posted_today.add(product_id)
        state["posted_today"] = list(posted_today)
        save_state(state)
    else:
        print("❌ Failed to post promo card")

if __name__ == "__main__":
    main()
