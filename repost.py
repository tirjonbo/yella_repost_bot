import os
import json
import re
import requests
import xml.etree.ElementTree as ET

# ── Config ──────────────────────────────────────────────────────────────────
SOURCE_CHANNELS = [
    "biggeekru",   # BigGeek — техника, гаджеты, игры
    "rbtshki",     # Роботишки — техника, путешествия, жизнь
]
BOT_TOKEN      = os.environ["BOT_TOKEN"]
TARGET_CHANNEL = "@yellauz"
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
STATE_FILE     = "state.json"

# YouTube/VK ссылки самих каналов — пропускаем их собственный контент
OWN_CONTENT_URLS = [
    "youtube.com/biggeek", "youtu.be/biggeek",
    "@biggeek", "biggeek.ru/video",
    "youtube.com/@rbtshki", "youtu.be/rbtshki",
    "vk.com/rbtshki", "@rbtshki",
]

# ── State ────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_ids": {"biggeekru": 0, "rbtshki": 0},
        "posted_ids": [],
        "last_channel_index": 0
    }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ── HTML processing ──────────────────────────────────────────────────────────
def clean_html_for_telegram(text):
    if not text:
        return ""
    # Абзацы сохраняем как двойной перенос
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text)
    text = re.sub(r'<p[^>]*>', '', text)
    text = re.sub(r'</p>', '\n\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    # Форматирование
    text = re.sub(r'<strong([^>]*)>', '<b>', text)
    text = re.sub(r'</strong>', '</b>', text)
    text = re.sub(r'<em([^>]*)>', '<i>', text)
    text = re.sub(r'</em>', '</i>', text)
    # Ссылки — оставляем только href
    text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>', r'<a href="\1">', text)
    # Убираем всё остальное кроме разрешённых тегов Telegram
    text = re.sub(r'<(?!/?(b|i|a)(\s|>|/))[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def html_to_plain(text):
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text.strip()

# ── Fetch posts from one channel ─────────────────────────────────────────────
def fetch_posts(channel: str) -> list:
    posts = []

    # Метод 1: RSSHub
    rss_urls = [
        f"https://rsshub.app/telegram/channel/{channel}",
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
                    print(f"Fetched {len(posts)} posts via RSS from @{channel}")
                    return posts
        except Exception as e:
            print(f"RSS error ({rss_url}): {e}")

    # Метод 2: t.me/s/ по блокам
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        resp = requests.get(f"https://t.me/s/{channel}", headers=headers, timeout=20)
        if resp.status_code == 200:
            html = resp.text
            message_blocks = re.split(r'(?=<div class="tgme_widget_message_wrap)', html)
            for block in message_blocks:
                m = re.search(rf'data-post="{channel}/(\d+)"', block)
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
                print(f"Fetched {len(posts)} posts via t.me/s/ from @{channel}")
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

def get_video_via_telegram(channel: str, post_id: int) -> bytes | None:
    """Скачиваем видео через Telegram Bot API — форвардим пост и берём file_id."""
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    try:
        me_resp = requests.get(f"{base}/getMe", timeout=10)
        if me_resp.status_code != 200:
            return None
        bot_id = me_resp.json()["result"]["id"]

        fwd = requests.post(f"{base}/forwardMessage", json={
            "chat_id": bot_id,
            "from_chat_id": f"@{channel}",
            "message_id": post_id
        }, timeout=15)

        if fwd.status_code != 200:
            print(f"Forward failed: {fwd.text[:200]}")
            return None

        msg = fwd.json().get("result", {})
        video = msg.get("video") or msg.get("animation")
        if not video:
            print("No video in forwarded message")
            return None

        file_id = video["file_id"]
        print(f"Got file_id from Telegram: {file_id[:30]}...")

        file_resp = requests.get(f"{base}/getFile", params={"file_id": file_id}, timeout=10)
        if file_resp.status_code != 200:
            return None

        file_path = file_resp.json()["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        dl = requests.get(file_url, timeout=60)
        if dl.status_code == 200:
            print(f"Video downloaded via Telegram: {len(dl.content)/1024:.0f} KB")
            return dl.content
    except Exception as e:
        print(f"get_video_via_telegram error: {e}")
    return None

# ── Filter ───────────────────────────────────────────────────────────────────
SKIP_KEYWORDS = [
    "ркн", "госуслуг", "роскомнадзор",
    "#партнерский", "конкурс", "розыгрыш", "участвовать", "победитель",
    "промокод", "купить рекламу",
    "сбербанк", "тинькофф", "gosuslugi",
    "dns-shop", "citilink", "mvideo", "eldorado",
]

def should_skip(text: str, html_text: str) -> tuple[bool, str]:
    t = text.lower()
    h = html_text.lower()

    # Фильтр по ключевым словам
    for kw in SKIP_KEYWORDS:
        if kw in t:
            return True, f"keyword: {kw}"

    # Фильтр собственного контента каналов (YouTube/VK ролики самих авторов)
    for url in OWN_CONTENT_URLS:
        if url.lower() in h:
            return True, f"own content: {url}"

    return False, ""

# ── Rewrite via Groq ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — редактор Telegram-канала @yellauz — живого канала про гаджеты, технологии, игры, путешествия и всё интересное для аудитории Узбекистана.

Задача: адаптировать пост под нашу аудиторию. Пиши живо, как рассказываешь другу.

Тебе передаётся текст поста в HTML формате (теги <b>, <i>, <a href="...">).

Правила:
1. Убирай: ссылки на российские магазины (biggeek.ru, DNS, Citilink и др.), госуслуги, рублёвые цены
2. Фразы "в России", "для россиян" → убирай или нейтрализуй
3. СТИЛЬ — живой, разговорный, с характером:
   - Можно добавить короткий комментарий редакции в конце (1 фраза)
   - Вопрос аудитории в конце если уместно — хорошо
4. СТРУКТУРА — копируй структуру оригинала точно:
   - Абзацы разделяй пустой строкой (\n\n)
   - Списки через — (тире) если в оригинале список
   - Финальный призыв если есть — сохраняй
5. ФОРМАТИРОВАНИЕ — обязательно сохраняй HTML теги:
   - <b>жирный</b> — для названий устройств, игр, брендов, ключевых фактов
   - <i>курсив</i> — для цитат или акцентов если были
   - <a href="URL">текст</a> — ВСЕ ссылки из оригинала сохраняй
6. ХЭШТЕГИ — максимум 3 самых важных, отдельным абзацем в конце
7. НЕ добавляй подписи, ссылки на yella.uz или призывы к покупке

Формат ответа — строго JSON без лишнего текста:
{
  "ru": "Готовый текст поста с HTML тегами и абзацами.",
  "skip": false
}

Скипать {"skip": true} только если пост про российскую политику, госуслуги или прямую рекламу."""

YELLA_SIGNATURE = "\n\n⚡️ <a href=\"https://t.me/yellauz\">@yellauz</a>"

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
            "temperature": 0.7,
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
    return ru + YELLA_SIGNATURE

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
    posted_ids = set(state.get("posted_ids", []))

    # Определяем какой канал обрабатываем сейчас — чередуем по очереди
    last_idx = state.get("last_channel_index", 0)
    channel_idx = (last_idx + 1) % len(SOURCE_CHANNELS)
    channel = SOURCE_CHANNELS[channel_idx]
    last_id = state.get("last_ids", {}).get(channel, 0)

    print(f"Processing channel: @{channel} (last_id: {last_id})")

    posts = fetch_posts(channel)
    print(f"Total fetched: {len(posts)}")

    new_posts = [(pid, ht, pt, murl, mtype) for pid, ht, pt, murl, mtype in posts
                 if pid > last_id and pid not in posted_ids]
    print(f"New posts: {len(new_posts)}")

    if not new_posts:
        print("Nothing new. Done.")
        # Всё равно сохраняем смену канала
        state["last_channel_index"] = channel_idx
        save_state(state)
        return

    # Берём самый новый пост
    msg_id, html_text, plain_text, media_url, media_type = new_posts[-1]

    skip, reason = should_skip(plain_text, html_text)
    if skip:
        print(f"Skipping post {msg_id} ({reason})")
        state["last_ids"] = state.get("last_ids", {})
        state["last_ids"][channel] = max(last_id, msg_id)
        state["last_channel_index"] = channel_idx
        save_state(state)
        return

    print(f"Processing post {msg_id}... media: {media_type or 'none'} {(media_url or '')[:80]}")
    print(f"Original: {plain_text[:150]}...")

    # Скачиваем медиа
    media_bytes = None
    if media_url:
        print(f"Downloading {media_type}...")
        media_bytes = download_media(media_url)
        if media_bytes:
            print(f"Downloaded: {len(media_bytes) / 1024:.1f} KB")
        else:
            print("Direct download failed")
            if media_type == 'video':
                print("Trying via Telegram forward...")
                media_bytes = get_video_via_telegram(channel, msg_id)
            if not media_bytes:
                print("Media unavailable, posting text-only")

    # Переписываем через Groq
    print("Rewriting via Groq...")
    result = rewrite_post(html_text)

    if not result:
        print("Rewrite failed, skipping")
        return

    if result.get("skip"):
        print(f"Groq skipped post {msg_id}")
        state["last_ids"] = state.get("last_ids", {})
        state["last_ids"][channel] = max(last_id, msg_id)
        state["last_channel_index"] = channel_idx
        save_state(state)
        return

    full_text = format_post(result)
    print(f"Rewritten: {result.get('ru', '')[:100]}...")
    print(f"Posting to {TARGET_CHANNEL}...")

    if post_to_telegram(full_text, media_bytes, media_type):
        posted_ids.add(msg_id)
        state["last_ids"] = state.get("last_ids", {})
        state["last_ids"][channel] = max(last_id, msg_id)
        state["last_channel_index"] = channel_idx
        state["posted_ids"] = list(posted_ids)[-200:]
        save_state(state)
    else:
        print("❌ Failed to post")

if __name__ == "__main__":
    main()
