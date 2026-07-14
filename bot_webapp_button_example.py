from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

MINI_APP_URL = "https://ВАШ-ДОМЕН"

def mini_app_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🍽 Открыть приложение", web_app=WebAppInfo(url=MINI_APP_URL))]])
