import os
import json
import re
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ── Config ──────────────────────────────────────────────────────────────────
YELLA_API_URL    = "https://yella.uz/yellaapi.php"
YELLA_API_SECRET = os.environ["YELLA_API_SECRET"]
BOT_TOKEN        = os.environ["BOT_TOKEN"]
TARGET_CHANNEL   = "@yellauz"
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
STATE_FILE       = "promo_state.json"

ASSETS_DIR = "assets"
FONTS_DIR  = f"{ASSETS_DIR}/fonts"

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
        return resp.json().get("products", [])
    except Exception as e:
        print(f"API fetch error: {e}")
        return []

# ── Download product photo ────────────────────────────────────────────────────
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

# ── Card generator ────────────────────────────────────────────────────────────
def format_price(p):
    return f"{int(p):,}".replace(',', ' ') + " сум"

def wrap_text(text, font, max_width, draw_obj, max_lines=2):
    words = text.split()
    lines, current = [], ""
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

def make_card(product_photo, product_name, product_sub, price_old, price_new, discount_percent, output_path):
    W, H = 1080, 1080

    img = Image.new('RGB', (W, H), '#FFFFFF')
    draw = ImageDraw.Draw(img, 'RGBA')

    # Светло-серая зона под фото
    draw.rectangle([0, 0, W, 670], fill=(246, 246, 250, 255))

    fb  = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Bold.ttf', 54)
    fsb = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-SemiBold.ttf', 36)
    fm  = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Medium.ttf', 30)
    fs  = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Regular.ttf', 24)
    fd  = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Bold.ttf', 46)
    fl  = ImageFont.truetype(f'{FONTS_DIR}/Montserrat-Bold.ttf', 30)

    # Фото товара
    if product_photo is None:
        product_photo = Image.new('RGBA', (500, 400), (220, 220, 225, 255))
    ph = product_photo.copy()
    ph.thumbnail((740, 610), Image.LANCZOS)
    img.paste(ph, ((W - ph.width) // 2, 30 + (630 - ph.height) // 2), ph)

    # Бейдж скидки — красный, сверху слева
    bx, by, bw, bh = 44, 44, 186, 106
    draw.rounded_rectangle([bx, by, bx+bw, by+bh], radius=20, fill=(255, 45, 45, 255))
    dt = f"-{discount_percent}%"
    bb = draw.textbbox((0, 0), dt, font=fd)
    draw.text(
        (bx + (bw - (bb[2]-bb[0])) // 2, by + (bh - (bb[3]-bb[1])) // 2 - bb[1]),
        dt, font=fd, fill='#FFFFFF'
    )

    # Лайм-разделитель
    draw.rectangle([0, 670, W, 686], fill=(204, 255, 0, 255))

    # Название и подзаголовок
    name_lines = wrap_text(product_name, fsb, 960, draw, max_lines=2)
    ny = 704
    for line in name_lines:
        draw.text((50, ny), line, font=fsb, fill='#1A1A1A')
        ny += 50
    draw.text((50, ny + 2), product_sub, font=fs, fill='#AAAAAA')

    # Новая цена — лайм плашка
    nt = format_price(price_new)
    bn = draw.textbbox((0, 0), nt, font=fb)
    nw, nh = bn[2]-bn[0], bn[3]-bn[1]
    pad = 22
    py_price = ny + 48
    draw.rounded_rectangle(
        [50, py_price, 50+nw+pad*2, py_price+nh+pad*2],
        radius=16, fill=(204, 255, 0, 255)
    )
    draw.text((50+pad, py_price+pad-bn[1]), nt, font=fb, fill='#1A1A1A')

    # Старая цена зачёркнутая
    ot = format_price(price_old)
    ox = 50 + nw + pad*2 + 22
    oy = py_price + (nh+pad*2)//2 - 15
    draw.text((ox, oy), ot, font=fm, fill='#BBBBBB')
    bo = draw.textbbox((ox, oy), ot, font=fm)
    sy = (bo[1]+bo[3])//2
    draw.line([bo[0], sy, bo[2], sy], fill='#BBBBBB', width=3)

    # Логотип внизу
    footer_y = 980
    draw.line([50, footer_y - 16, W-50, footer_y - 16], fill=(228, 228, 228, 255), width=2)
    draw.text((50, footer_y), "yella.uz", font=fl, fill='#1A1A1A')

    img.save(output_path, quality=95)
    return output_path

# ── Groq: очистка названия ────────────────────────────────────────────────────
def clean_product_name(raw_name: str) -> tuple[str, str]:
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": (
                        "Тебе дают техническое название товара. "
                        "Верни красивое короткое название (бренд + модель/серия, без артикулов и кодов) "
                        "и короткий подзаголовок (тип товара, 2-4 слова). "
                        "Формат — строго JSON: {\"name\": \"...\", \"sub\": \"...\"}"
                    )},
                    {"role": "user", "content": raw_name}
                ],
                "temperature": 0.3,
                "max_tokens": 100
            },
            timeout=15
        )
        resp.raise_for_status()
        raw = re.sub(r'^```json\s*', '', resp.json()["choices"][0]["message"]["content"].strip())
        raw = re.sub(r'\s*```$', '', raw)
        data = json.loads(raw)
        return data.get("name", raw_name), data.get("sub", "В наличии")
    except Exception as e:
        print(f"Name clean error: {e}")
        return raw_name, "В наличии"

# ── Groq: продающий текст ─────────────────────────────────────────────────────
SALES_PROMPT = """Ты — копирайтер интернет-магазина электроники @yellauz в Узбекистане.

Тебе дают название товара и описание. Напиши короткий продающий текст для Telegram-поста.

Правила:
1. Структура:
   — Цепляющая фраза-крючок в начале (1 строка, без банальщины типа "Хотите...")
   — 2-3 ключевых преимущества списком через — (тире), каждое на отдельной строке
   — Короткий финал (1 строка)
2. Стиль — живой, как рассказываешь другу, без воды и канцелярщины
3. Эмодзи — максимум 1 в начале если уместно, не больше
4. НЕ упоминай цену — она на картинке
5. НЕ пиши хэштеги и подписи
6. Используй <b>название товара</b> при первом упоминании

Формат ответа — строго JSON: {"text": "Готовый текст с HTML тегами"}"""

def generate_sales_copy(product_name, description):
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": SALES_PROMPT},
                    {"role": "user", "content": f"Товар: {product_name}\n\nОписание: {description}"}
                ],
                "temperature": 0.7,
                "max_tokens": 500
            },
            timeout=30
        )
        resp.raise_for_status()
        raw = re.sub(r'^```json\s*', '', resp.json()["choices"][0]["message"]["content"].strip())
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw).get("text", "")
    except Exception as e:
        print(f"Groq error: {e}")
        return None

# ── Post to Telegram ──────────────────────────────────────────────────────────
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

# ── Main ──────────────────────────────────────────────────────────────────────
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

    candidate = None
    for p in products:
        if str(p["product_id"]) not in posted_today:
            candidate = p
            break

    if not candidate:
        print("All promo products already posted today. Done.")
        return

    product_id  = str(candidate["product_id"])
    raw_name    = candidate["name"]
    description = candidate.get("description", "")
    price_old   = candidate["price"]
    price_new   = candidate["special_price"]
    discount    = candidate["discount_percent"]
    image_url   = candidate["image_url"]
    product_url = candidate["product_url"]

    print(f"Selected: {raw_name} (-{discount}%)")

    # Очищаем название
    clean_name, sub_name = clean_product_name(raw_name)
    print(f"Clean name: {clean_name} | Sub: {sub_name}")

    # Скачиваем фото
    photo = download_photo(image_url)
    if photo is None:
        print("Could not download photo, using placeholder")

    # Генерируем карточку
    card_path = "/tmp/promo_card.jpg"
    make_card(photo, clean_name, sub_name, price_old, price_new, discount, card_path)
    print(f"Card generated: {card_path}")

    # Генерируем текст
    sales_text = generate_sales_copy(clean_name, description)
    if not sales_text:
        sales_text = (
            f"<b>{clean_name}</b> сейчас доступен по акционной цене.\n\n"
            f"Успейте заказать — предложение ограничено."
        )

    caption = (
        f"{sales_text}\n\n"
        f"🔗 <a href=\"{product_url}\">Смотреть на yella.uz</a>\n\n"
        f"⚡️ <a href=\"https://t.me/yellauz\">@yellauz</a>"
    )

    if post_to_telegram(card_path, caption):
        posted_today.add(product_id)
        state["posted_today"] = list(posted_today)
        save_state(state)
    else:
        print("❌ Failed to post promo card")

if __name__ == "__main__":
    main()
