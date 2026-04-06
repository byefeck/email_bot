import os
import asyncio
import json
import base64
import logging
import re
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("errors.log"),
        logging.StreamHandler()
    ]
)

CHAT_ID = os.getenv("CHAT_ID")
THREAD_ID = os.getenv("THREAD_ID")
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
STATE_FILE = 'state.json'

def load_state():
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding='utf-8') as f:
        json.dump(state, f, indent=2)

def get_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('gmail', 'v1', credentials=creds)

def history_and_msg_id(service, history_id):
    query = service.users().history().list(
        userId="me",
        startHistoryId=history_id,
        historyTypes=["messageAdded"]
    ).execute()

    history = query.get('history', [])
    new_history_id = query.get('historyId', history_id)

    msg_ids = []

    for i in history:
        messages = i.get('messagesAdded', [])
        for m in messages:
            msg_ids.append(m['message']['id'])
    
    return msg_ids, new_history_id

def decode(data):
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")

#разобрать
def decode_html(data):
    html_data = decode(data)
    soup = BeautifulSoup(html_data, "html.parser")

    # 1. удалить мусорные теги
    for tag in soup(["script", "style", "noscript", "img"]):
        tag.decompose()

    # 2. убрать tracking / hidden элементы
    for tag in soup.find_all(attrs={"style": True}):
        if "display:none" in tag.get("style", ""):
            tag.decompose()

    # 3. <br> → перенос строки
    for br in soup.find_all("br"):
        br.replace_with("\n")

    # 4. достать текст
    text = soup.get_text(separator="\n")

    # 5. чистка строк
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    # 6. убрать мусорные слова (опционально)
    cleaned = []
    for line in lines:
        if "unsubscribe" in line.lower():
            continue
        if "let us know" in line.lower():
            continue
        cleaned.append(line)

    return "\n".join(cleaned)

#разобрать
def safe_cut(text, limit=3500):
    return text[:limit] if text else ""

#разобрать
def extract_text_parts(parts):
    text_plain = None
    text_html = None

    for part in parts:
        mime = part.get("mimeType")
        data = part.get("body", {}).get("data")

        # если вложенные parts
        if "parts" in part:
            p, h = extract_text_parts(part["parts"])
            text_plain = text_plain or p
            text_html = text_html or h
            continue

        if not data:
            continue

        if mime == "text/plain":
            text_plain = decode(data)

        elif mime == "text/html":
            text_html = decode_html(data)

    return text_plain, text_html

def has_attachments(msg):
    payload = msg["payload"]

    if "parts" not in payload:
        return False

    for part in payload["parts"]:
        if part.get("filename"):  # если имя файла есть
            return True

    return False

#разобравть
def parse_sender(sender):
    email = re.search(r"<(.+?)>", sender)
    email = email.group(1) if email else ""

    name = re.sub(r"<.+?>", "", sender).replace('"', "").strip()

    return name, email

#разобрать
def get_text(msg):
    payload = msg['payload']

    if 'parts' in payload:
        plain, html = extract_text_parts(payload['parts'])

        if plain:
            return plain
        if html:
            return html

    data = payload.get('body', {}).get('data')
    if data:
        return decode(data)

    return ""
        

def parse_message(service, msg_id):
    msg = service.users().messages().get(
        userId="me",
        id=msg_id
    ).execute()
    
    headers = msg["payload"]["headers"]

    subject = next((i['value'] for i in headers if i['name'] == 'Subject'), 'Без темы')
    sender = next((i['value'] for i in headers if i['name'] == 'From'), 'Неизвестно')
    name, email = parse_sender(sender)
    text = get_text(msg)
    has_att = has_attachments(msg)
    
    return {
        'sender_name': name,
        'sender_email': email,
        'subject': subject,
        'text': text,
        'has_att': has_att
    }

def format_msg(msg_dict):
    sender_name = msg_dict['sender_name']
    sender_email = msg_dict['sender_email']
    subject = msg_dict['subject']
    text = msg_dict['text']
    has_att = msg_dict['has_att']
    message = (
        f'📨 Новое письмо!\n'
        f'👻 Имя отправителя: {sender_name}\n'
        f'📧 Почта отправителя: {sender_email}\n'
        f'📎 Прикрепленные файлы: {"есть" if has_att else "нет"}\n\n'
        f'🙉 Тема: {subject}\n'
        f'💬 Текст:\n\n'
        f'{safe_cut(text)}\n\n'
    )
    return safe_cut(message, 4000)


bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

async def mail_loop():
    service = get_service()

    state = load_state()
    history_id = state.get('history_id')

    if not history_id:
        profile = service.users().getProfile(userId='me').execute()
        history_id = profile['historyId']
        state["history_id"] = history_id
        save_state(state)

    while True:
        try:
            msg_ids, new_history_id = history_and_msg_id(service, history_id)
            for msg_id in msg_ids:
                msg_dict = parse_message(service, msg_id)
                send = format_msg(msg_dict)
                await bot.send_message(
                    CHAT_ID,
                    send,
                    message_thread_id=THREAD_ID
                )
                await asyncio.sleep(5)
            history_id = new_history_id
            state['history_id'] = history_id
            save_state(state)
        except Exception as e:
            logging.error(f"Ошибка: {e}")

        await asyncio.sleep(5)

async def main():
    asyncio.create_task(mail_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())