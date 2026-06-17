import os
import json
import re
import requests
import xml.etree.ElementTree as ET

# ── Config ──────────────────────────────────────────────────────────────────
SOURCE_CHANNEL  = "biggeekru"
BOT_TOKEN       = os.environ["BOT_TOKEN"]
TARGET_CHANNEL  = "@yellauz"
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
STATE_FILE      = "state.json"

# ── State ────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_post_id": 0, "posted_ids": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ── Fetch posts ──────────────────────────────────────────────────────────────
def strip_html(text):
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text.strip()

def fetch_posts():
    """
    Возвращает список (message_id, text, photo_url|None).
    Метод 1: RSSHub.
    Метод 2: t.me/s/ парсинг по блокам как fallback.
    """
    posts = []

    # Метод 1: RSSHub
    rss_urls = [
        f"https://rsshub.app/telegram/channel/{SOURCE_CHANNEL}",
        f"https://rss.rssforever.com/telegram/channel/{SOURCE_CHANNEL}",
    ]
    for rss_url in rss_urls:
        try:
            resp = requests.get(rss_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and '<item>' in resp.text:
                root = ET.fromstring(resp.text)
                items = root.findall('.//item')
                for item in items[-10:]:
                    link = item.findtext('link', '')
                    desc = item.findtext('description', '')
                    m = re.search(r'/(\d+)$', link)
                    if not m or not desc:
                        continue
                    pid = int(m.group(1))
                    text = strip_html(desc)
                    if not text or len(text) < 30:
                        continue
                    photo_url = None
                    enclosure = item.find('enclosure')
                    if enclosure is not None:
                        photo_url = enclosure.get('url')
                    if not photo_url:
                        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
                        if img_m:
                            photo_url = img_m.group(1)
                    posts.append((pid, text, photo_url))
                if posts:
                    print(f"Fetched {len(posts)} posts via RSS: {rss_url}")
                    return posts
        except Exception as e:
            print(f"RSS error ({rss_url}): {e}")

    # Метод 2: t.me/s/ — парсим каждый пост как ЕДИНЫЙ блок
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        resp = requests.get(f"https://t.me/s/{SOURCE_CHANNEL}",
                            headers=headers, timeout=20)
        if resp.status_code == 200:
            html = resp.text
            # Разбиваем на блоки по каждому сообщению
            message_blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', html)
            for block in message_blocks:
                # ID поста
                m = re.search(rf'data-post="{SOURCE_CHANNEL}/(\d+)"', block)
                if not m:
                    continue
                pid = int(m.group(1))

                # Текст
                text_m = re.search(
                    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                    block, re.DOTALL
                )
                text = strip_html(text_m.group(1)) if text_m else ""
                if not text or len(text) < 30:
                    continue

                # Фото — ищем background-image в том же блоке
                photo_url = None
                photo_m = re.search(
                    r'background-image:url\([\'"]?([^\'")\s]+)[\'"]?\)',
                    block
                )
                if photo_m:
                    photo_url = photo_m.group(1)

                posts.append((pid, text, photo_url))

            if posts:
                print(f"Fetched {len(posts)} posts via t.me/s/")
                return posts[-10:]
    except Exception as e:
        print(f"t.me/s/ error: {e}")

    return posts

# ── Filter ───────────────────────────────────────────────────────────────────
SKIP_KEYWORDS = [
    "конкурс", "розыгрыш", "участвовать", "победитель",
    "gosuslugi", "госуслуг", "промокод для россиян",
    "biggeek.ru", "mews.biggeek", "купить рекламу",
    "подписаться на наш telegram"
]

def should_skip(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SKIP_KEYWORDS)

# ── Rewrite via Groq ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — редактор Telegram-канала @yellauz, интернет-магазина электроники в Узбекистане.

Задача: адаптировать технологический пост из российского канала Big Geek под аудиторию Узбекистана.

Правила:
1. Сохраняй стиль оригинала: эмодзи в начале, живой разговорный язык, хэштеги если были
2. Убирай: ссылки на российские магазины (biggeek.ru, DNS, Citilink и др.), госуслуги, рублёвые цены
3. Фразы "в России", "российские пользователи", "для россиян" → убирай или заменяй на "в Узбекистане" / "у нас" где уместно
4. НЕ добавляй никакие подписи, ссылки на yella.uz или призывы к покупке — это делается отдельно
5. Отвечай ТОЛЬКО готовым текстом поста, без пояснений и кавычек"""

YELLA_SIGNATURE = "\n\n🛒 yella.uz — интернет-магазин электроники"

def rewrite_post(text: str) -> str | None:
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            "temperature": 0.7,
            "max_tokens": 600
        },
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()

# ── Post to Telegram ─────────────────────────────────────────────────────────
def post_to_telegram(text: str, photo_url: str | None = None) -> bool:
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    full_text = text + YELLA_SIGNATURE

    if photo_url:
        # Скачиваем фото сами — Telegram не может достучаться до cdn.telesco.pe
        try:
            img_resp = requests.get(photo_url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if img_resp.status_code == 200:
                files = {"photo": ("photo.jpg", img_resp.content, "image/jpeg")}
                data = {"chat_id": TARGET_CHANNEL, "caption": full_text}
                resp = requests.post(f"{base}/sendPhoto", data=data, files=files, timeout=30)
                if resp.status_code == 200:
                    print("✅ Posted with photo (upload)")
                    return True
                print(f"sendPhoto upload error: {resp.status_code} {resp.text[:200]}")
            else:
                print(f"Failed to download photo: {img_resp.status_code}")
        except Exception as e:
            print(f"Photo download/upload error: {e}")

        print("Photo failed, falling back to text-only")

    # sendMessage (text only)
    for parse_mode in ["HTML", None]:
        payload = {"chat_id": TARGET_CHANNEL, "text": full_text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(f"{base}/sendMessage", json=payload, timeout=15)
        if resp.status_code == 200:
            print(f"✅ Posted text-only (parse_mode={parse_mode})")
            return True
        print(f"sendMessage error ({parse_mode}): {resp.status_code} {resp.text[:200]}")
    return False

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    state = load_state()
    last_id = state["last_post_id"]
    posted_ids = set(state.get("posted_ids", []))

    print(f"Last posted ID: {last_id}")

    posts = fetch_posts()
    print(f"Total fetched: {len(posts)}")

    # Только новые, ещё не постившиеся
    new_posts = [(pid, txt, photo) for pid, txt, photo in posts
                 if pid > last_id and pid not in posted_ids]
    print(f"New posts: {len(new_posts)}")

    if not new_posts:
        print("Nothing new. Done.")
        return

    # Берём самый свежий 1 пост за запуск
    msg_id, text, photo_url = new_posts[-1]

    if should_skip(text):
        print(f"Skipping post {msg_id} (filtered)")
        state["last_post_id"] = max(last_id, msg_id)
        save_state(state)
        return

    print(f"Rewriting post {msg_id}... photo: {photo_url or 'none'}")
    print(f"Original: {text[:150]}...")

    rewritten = rewrite_post(text)
    if not rewritten:
        print("Empty rewrite, skipping")
        return

    print(f"Rewritten: {rewritten[:150]}...")
    print(f"Posting to {TARGET_CHANNEL}...")

    if post_to_telegram(rewritten, photo_url):
        posted_ids.add(msg_id)
        state["last_post_id"] = max(last_id, msg_id)
        state["posted_ids"] = list(posted_ids)[-100:]
        save_state(state)
    else:
        print("❌ Failed to post")

if __name__ == "__main__":
    main()
