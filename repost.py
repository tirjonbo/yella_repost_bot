import os
import json
import re
import requests
import xml.etree.ElementTree as ET

# ── Config ──────────────────────────────────────────────────────────────────
SOURCE_CHANNEL  = "exploitex"
BOT_TOKEN       = os.environ["VERTEX_BOT_TOKEN"]
TARGET_CHANNEL  = "@vertex_ii"
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
STATE_FILE      = "vertex_state.json"

# ── State ────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_post_id": 0, "posted_ids": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ── HTML processing ──────────────────────────────────────────────────────────
def clean_html_for_telegram(text):
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<strong([^>]*)>', '<b>', text)
    text = re.sub(r'</strong>', '</b>', text)
    text = re.sub(r'<em([^>]*)>', '<i>', text)
    text = re.sub(r'</em>', '</i>', text)
    text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>', r'<a href="\1">', text)
    text = re.sub(r'<(?!/?(b|i|a)(\s|>|/))[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text.strip()

def html_to_plain(text):
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text.strip()

# ── Fetch posts ──────────────────────────────────────────────────────────────
def fetch_posts():
    posts = []

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
                for item in items[-15:]:
                    link = item.findtext('link', '')
                    desc = item.findtext('description', '')
                    m = re.search(r'/(\d+)$', link)
                    if not m or not desc:
                        continue
                    pid = int(m.group(1))
                    html_text = clean_html_for_telegram(desc)
                    plain_text = html_to_plain(desc)
                    if not plain_text or len(plain_text) < 30:
                        continue
                    media_url = None
                    media_type = None
                    enclosure = item.find('enclosure')
                    if enclosure is not None:
                        url = enclosure.get('url', '')
                        etype = enclosure.get('type', '')
                        if 'video' in etype or url.endswith('.mp4'):
                            media_url = url
                            media_type = 'video'
                        else:
                            media_url = url
                            media_type = 'photo'
                    if not media_url:
                        vm = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', desc)
                        if vm:
                            media_url = vm.group(1)
                            media_type = 'video'
                    if not media_url:
                        im = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
                        if im:
                            media_url = im.group(1)
                            media_type = 'photo'
                    posts.append((pid, html_text, plain_text, media_url, media_type))
                if posts:
                    print(f"Fetched {len(posts)} posts via RSS: {rss_url}")
                    return posts
        except Exception as e:
            print(f"RSS error ({rss_url}): {e}")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        resp = requests.get(f"https://t.me/s/{SOURCE_CHANNEL}", headers=headers, timeout=20)
        if resp.status_code == 200:
            html = resp.text
            message_blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', html)
            for block in message_blocks:
                m = re.search(rf'data-post="{SOURCE_CHANNEL}/(\d+)"', block)
                if not m:
                    continue
                pid = int(m.group(1))
                text_m = re.search(
                    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                    block, re.DOTALL
                )
                raw_html = text_m.group(1) if text_m else ""
                html_text = clean_html_for_telegram(raw_html)
                plain_text = html_to_plain(raw_html)
                if not plain_text or len(plain_text) < 30:
                    continue
                media_url = None
                media_type = None
                video_m = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', block)
                if video_m:
                    media_url = video_m.group(1)
                    media_type = 'video'
                if not media_url:
                    photo_m = re.search(
                        r'background-image:url\([\'"]?([^\'")\s]+)[\'"]?\)', block)
                    if photo_m:
                        media_url = photo_m.group(1)
                        media_type = 'photo'
                posts.append((pid, html_text, plain_text, media_url, media_type))
            if posts:
                print(f"Fetched {len(posts)} posts via t.me/s/")
                return posts[-15:]
    except Exception as e:
        print(f"t.me/s/ error: {e}")

    return posts

# ── Download media ───────────────────────────────────────────────────────────
def download_media(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            return resp.content
        print(f"Media download failed: {resp.status_code}")
    except Exception as e:
        print(f"Media download error: {e}")
    return None

# ── Filter ───────────────────────────────────────────────────────────────────
SKIP_KEYWORDS = [
    "в россии", "для россиян", "российск", "росси ", "россия",
    "русских", "русские", "русский пользовател",
    "ркн", "госуслуг", "роскомнадзор", "заблокировал",
    "#партнерский", "конкурс", "розыгрыш", "участвовать", "победитель",
    "промокод", "купить рекламу",
    "вконтакте", "яндекс", "сбербанк", "тинькофф", "gosuslugi",
    "dns-shop", "citilink", "mvideo", "eldorado",
]

def should_skip(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SKIP_KEYWORDS)

# ── Rewrite via Groq ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — редактор Telegram-канала @vertex_ii для IT и бизнес-аудитории Узбекистана.

Задача: адаптировать технологический пост под узбекистанскую аудиторию, оформить в двуязычном формате.

Тебе передаётся текст поста в HTML формате (теги <b>, <a href="...">).

Правила:
1. Убирай всё про Россию, российские сервисы, рублёвые цены, РКН
2. СТРУКТУРА — копируй структуру оригинала максимально точно:
   - Абзацы, списки, порядок элементов — всё сохраняй
   - Если есть финальный призыв ("Забираем здесь", "Попробуйте") — сохраняй
3. ФОРМАТИРОВАНИЕ — сохраняй HTML теги:
   - <b>жирный</b> — для ключевых слов и названий как в оригинале
   - <a href="URL">текст ссылки</a> — ВСЕ ссылки из оригинала ОБЯЗАТЕЛЬНО сохраняй
4. ЭМОДЗИ — максимум 1 в начале, только если уместно. Внутри текста не использовать
5. НЕ добавляй свои ссылки, хэштеги, призывы купить

Формат ответа — строго JSON:
{
  "ru": "Русская версия с HTML тегами <b> и <a href=...>. Структура как в оригинале.",
  "uz": "O'zbek versiyasi. Xuddi shu HTML teglar va tuzilma. 30-40% qisqaroq.",
  "skip": false
}

Если пост про Россию/российское — {"skip": true}.
Узбекская версия: тот же смысл, та же структура, те же ссылки, короче на 30-40%."""

VERTEX_SIGNATURE = "\n\n⚡️ @vertex_ii — IT va texnologiyalar"

def rewrite_post(html_text: str) -> dict | None:
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": html_text}
            ],
            "temperature": 0.6,
            "max_tokens": 1500
        },
        timeout=30
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"JSON parse error: {e}\nRaw: {raw[:300]}")
        return None

# ── Format post ──────────────────────────────────────────────────────────────
def format_post(data: dict) -> str:
    ru = data.get("ru", "").strip()
    uz = data.get("uz", "").strip()
    divider = "\n\n— — —\n\n"
    return ru + divider + uz + VERTEX_SIGNATURE

# ── Post to Telegram ─────────────────────────────────────────────────────────
def post_to_telegram(text: str, media_bytes: bytes | None, media_type: str | None) -> bool:
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"

    if media_bytes and media_type == 'video':
        data = {"chat_id": TARGET_CHANNEL, "caption": text, "parse_mode": "HTML"}
        files = {"video": ("video.mp4", media_bytes, "video/mp4")}
        resp = requests.post(f"{base}/sendVideo", data=data, files=files, timeout=60)
        if resp.status_code == 200:
            print("✅ Posted with video")
            return True
        print(f"sendVideo error: {resp.status_code} {resp.text[:200]}")
        print("Video failed, falling back to text-only")

    elif media_bytes and media_type == 'photo':
        data = {"chat_id": TARGET_CHANNEL, "caption": text, "parse_mode": "HTML"}
        files = {"photo": ("photo.jpg", media_bytes, "image/jpeg")}
        resp = requests.post(f"{base}/sendPhoto", data=data, files=files, timeout=30)
        if resp.status_code == 200:
            print("✅ Posted with photo")
            return True
        print(f"sendPhoto error: {resp.status_code} {resp.text[:200]}")
        print("Photo failed, falling back to text-only")

    for parse_mode in ["HTML", None]:
        payload = {"chat_id": TARGET_CHANNEL, "text": text}
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

    new_posts = [(pid, ht, pt, murl, mtype) for pid, ht, pt, murl, mtype in posts
                 if pid > last_id and pid not in posted_ids]
    print(f"New posts: {len(new_posts)}")

    if not new_posts:
        print("Nothing new. Done.")
        return

    msg_id, html_text, plain_text, media_url, media_type = new_posts[-1]

    if should_skip(plain_text):
        print(f"Skipping post {msg_id} (keyword filter)")
        state["last_post_id"] = max(last_id, msg_id)
        save_state(state)
        return

    print(f"Processing post {msg_id}... media: {media_type or 'none'} {(media_url or '')[:80]}")
    print(f"Original: {plain_text[:150]}...")

    media_bytes = None
    if media_url:
        print(f"Downloading {media_type}...")
        media_bytes = download_media(media_url)
        if media_bytes:
            print(f"Downloaded: {len(media_bytes) / 1024:.1f} KB")
        else:
            print("Media download failed, will post text-only")

    print("Rewriting via Groq...")
    result = rewrite_post(html_text)

    if not result:
        print("Rewrite failed, skipping")
        return

    if result.get("skip"):
        print(f"Groq decided to skip post {msg_id} (Russia-related)")
        state["last_post_id"] = max(last_id, msg_id)
        save_state(state)
        return

    full_text = format_post(result)
    print(f"RU: {result.get('ru', '')[:100]}...")
    print(f"UZ: {result.get('uz', '')[:100]}...")
    print(f"Posting to {TARGET_CHANNEL}...")

    if post_to_telegram(full_text, media_bytes, media_type):
        posted_ids.add(msg_id)
        state["last_post_id"] = max(last_id, msg_id)
        state["posted_ids"] = list(posted_ids)[-100:]
        save_state(state)
    else:
        print("❌ Failed to post")

if __name__ == "__main__":
    main()
