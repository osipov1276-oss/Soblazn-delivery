import hashlib
import hmac
import json
import os
import threading
import time
import secrets
from functools import wraps
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import requests
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash

BASE_DIR = Path(__file__).resolve().parent

ADMIN_API_SECRET = os.getenv('ADMIN_API_SECRET', '').strip()


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE settings without an extra dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(BASE_DIR / "bot_config.env")
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-in-render")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "soblazn2026")
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
COURIER_CHAT_ID = os.getenv("COURIER_CHAT_ID", "-1004342107012").strip()
DELIVERY_FEE = int(os.getenv("DELIVERY_FEE", "1000"))
BOT_USERNAME = ""
CLUB_BOT_USERNAME = os.getenv("CLUB_BOT_USERNAME", "SOBLAZN_Club_Bot").strip().lstrip("@")
INTEGRATION_SECRET = os.getenv("INTEGRATION_SECRET", "").strip()

ORDERS_FILE = BASE_DIR / "mini_app_orders.json"
PROFILES_FILE = BASE_DIR / "guest_profiles.json"
BONUS_QUEUE_FILE = BASE_DIR / "bonus_sync_queue.json"
DATA_LOCK = threading.RLock()


def normalize_guest_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def load_profiles() -> dict:
    if not PROFILES_FILE.exists():
        return {}
    try:
        return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_profiles(profiles: dict) -> None:
    with DATA_LOCK:
        PROFILES_FILE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")


def load_bonus_queue() -> dict:
    with DATA_LOCK:
        if not BONUS_QUEUE_FILE.exists():
            return {}
        try:
            return json.loads(BONUS_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_bonus_queue(queue: dict) -> None:
    with DATA_LOCK:
        BONUS_QUEUE_FILE.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def integration_authorized() -> bool:
    supplied = request.headers.get("X-Integration-Secret", "")
    return bool(INTEGRATION_SECRET and hmac.compare_digest(supplied, INTEGRATION_SECRET))


def load_orders() -> dict:
    if not ORDERS_FILE.exists():
        return {}
    try:
        return json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_orders(orders: dict) -> None:
    ORDERS_FILE.write_text(
        json.dumps(orders, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def status_keyboard(order_number: str, status: str):
    if status == "new":
        rows = [[{"text": "👨‍💼 Администратор принял", "callback_data": f"admin_accept:{order_number}"}]]
    elif status == "admin_accepted":
        rows = [[{"text": "🛵 Курьер принял", "callback_data": f"courier_accept:{order_number}"}]]
    elif status == "courier_accepted":
        rows = [[{"text": "🚗 Курьер в пути", "callback_data": f"on_way:{order_number}"}]]
    elif status == "on_way":
        rows = [[{"text": "✅ Заказ доставлен", "callback_data": f"delivered:{order_number}"}]]
    else:
        rows = []
    return {"inline_keyboard": rows}


def build_status_text(order: dict) -> str:
    lines = [order["base_text"], "", "📋 СТАТУС ЗАКАЗА:"]
    lines.append(f"👨‍💼 Администратор: {order.get('admin') or 'не принял'}")
    lines.append(f"🛵 Курьер: {order.get('courier') or 'не принял'}")
    labels = {
        "new": "ожидает администратора",
        "admin_accepted": "принят администратором",
        "courier_accepted": "принят курьером",
        "on_way": "курьер в пути",
        "delivered": "доставлен",
    }
    lines.append(f"📦 Статус: {labels.get(order.get('status'), order.get('status'))}")
    return "\n".join(lines)


def telegram_call(method: str, payload: dict, timeout: int = 20):
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=timeout,
    )


def answer_callback(callback_id: str, text: str = "", alert: bool = False):
    try:
        telegram_call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": alert,
            },
            10,
        )
    except Exception:
        pass


STATUS_LABELS = {
    "new": "🟡 Ожидает подтверждения",
    "admin_accepted": "👨‍🍳 Заказ принят и готовится",
    "courier_accepted": "🛵 Курьер принял заказ",
    "on_way": "🚗 Курьер едет к вам",
    "delivered": "✅ Заказ доставлен",
}


def notify_customer(order: dict) -> None:
    user_id = order.get("telegram_user_id")
    if not user_id:
        return

    status = order.get("status", "new")
    text = (
        f"🍽 Заказ №{order.get('order_number')}\n\n"
        f"{STATUS_LABELS.get(status, status)}"
    )

    if status == "courier_accepted" and order.get("courier"):
        text += f"\nКурьер: {order['courier']}"
    if status == "on_way":
        text += "\nПожалуйста, будьте на связи."

    try:
        telegram_call("sendMessage", {"chat_id": user_id, "text": text}, 10)
    except Exception:
        pass


def handle_callback(callback: dict) -> None:
    data = callback.get("data", "")
    if ":" not in data:
        return

    action, order_number = data.split(":", 1)
    orders = load_orders()
    order = orders.get(order_number)

    if not order:
        answer_callback(callback.get("id", ""), "Заказ не найден", True)
        return

    user = callback.get("from", {})
    employee = (
        " ".join(
            x
            for x in [user.get("first_name"), user.get("last_name")]
            if x
        ).strip()
        or user.get("username")
        or str(user.get("id"))
    )
    status = order.get("status", "new")

    if action == "admin_accept":
        if status != "new":
            answer_callback(callback["id"], "Заказ уже обработан", True)
            return
        order["admin"] = employee
        order["status"] = "admin_accepted"
    elif action == "courier_accept":
        if status != "admin_accepted":
            answer_callback(
                callback["id"],
                "Сначала заказ должен принять администратор",
                True,
            )
            return
        order["courier"] = employee
        order["status"] = "courier_accepted"
    elif action == "on_way":
        if status != "courier_accepted":
            answer_callback(
                callback["id"],
                "Сначала заказ должен принять курьер",
                True,
            )
            return
        order["status"] = "on_way"
    elif action == "delivered":
        if status != "on_way":
            answer_callback(
                callback["id"],
                "Сначала отметьте «Курьер в пути»",
                True,
            )
            return
        order["status"] = "delivered"
    else:
        return

    orders[order_number] = order
    save_orders(orders)
    notify_customer(order)

    msg = callback.get("message", {})
    payload = {
        "chat_id": msg.get("chat", {}).get("id"),
        "message_id": msg.get("message_id"),
        "text": build_status_text(order),
        "reply_markup": status_keyboard(order_number, order["status"]),
    }

    response = telegram_call("editMessageText", payload)
    if response.ok:
        answer_callback(callback["id"], "Статус обновлён")
    else:
        answer_callback(
            callback["id"],
            "Не удалось обновить сообщение",
            True,
        )


def get_bot_username() -> str:
    global BOT_USERNAME

    if BOT_USERNAME:
        return BOT_USERNAME
    if not BOT_TOKEN:
        return ""

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
            timeout=10,
        )
        data = response.json() if response.ok else {}
        BOT_USERNAME = str(
            data.get("result", {}).get("username", "")
        ).strip()
    except Exception:
        BOT_USERNAME = ""
    return BOT_USERNAME


def handle_message(message: dict) -> None:
    text = str(message.get("text", "")).strip()
    chat_id = message.get("chat", {}).get("id")
    user = message.get("from", {})

    if not chat_id or not text.startswith("/start"):
        return

    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if not payload.startswith("order_"):
        telegram_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "👋 Добро пожаловать в SOBLAZN Delivery!\n\n"
                    "Оформите заказ в приложении, а затем нажмите "
                    "«Открыть Telegram и отслеживать заказ»."
                ),
            },
            10,
        )
        return

    raw = payload[len("order_"):]
    if "_" not in raw:
        telegram_call(
            "sendMessage",
            {"chat_id": chat_id, "text": "Не удалось определить заказ."},
            10,
        )
        return

    order_number, tracking_token = raw.split("_", 1)
    orders = load_orders()
    order = orders.get(order_number)

    if (
        not order
        or not hmac.compare_digest(
            str(order.get("tracking_token", "")),
            tracking_token,
        )
    ):
        telegram_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Заказ не найден или ссылка устарела.",
            },
            10,
        )
        return

    order["telegram_user_id"] = user.get("id") or chat_id
    orders[order_number] = order
    save_orders(orders)

    status = order.get("status", "new")
    text_out = (
        f"✅ Заказ №{order_number} привязан к вашему Telegram.\n\n"
        f"{STATUS_LABELS.get(status, status)}\n"
        f"💰 Сумма: {order.get('total', 0)} ₸\n\n"
        "Теперь я буду присылать вам сообщения при каждом изменении статуса."
    )

    if order.get("courier"):
        text_out += f"\n🛵 Курьер: {order['courier']}"

    telegram_call(
        "sendMessage",
        {"chat_id": chat_id, "text": text_out},
        10,
    )


def polling_loop() -> None:
    if not BOT_TOKEN:
        return

    offset = None
    while True:
        try:
            params = {
                "timeout": 25,
                "allowed_updates": ["callback_query", "message"],
            }
            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params=params,
                timeout=35,
            )
            data = response.json() if response.ok else {}

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                if update.get("callback_query"):
                    handle_callback(update["callback_query"])
                elif update.get("message"):
                    handle_message(update["message"])
        except Exception as exc:
            print(f"Telegram polling error: {exc}", flush=True)
            time.sleep(3)


PACKAGING_PRICES = {
    "Коробки для пиццы": 230,
    "Бургер-боксы": 165,
    "Контейнеры для супа": 165,
    "Контейнеры для салатов": 165,
    "Контейнеры для горячей еды": 165,
    "Большие контейнеры для шашлыка": 200,
    "Коробки для чебуреков": 230,
    "Контейнеры для фри и закусок": 165,
    "Контейнеры для десертов": 200,
    "Соусники": 0,
    "Одноразовые приборы": 0,
}

with open(BASE_DIR / "menu.json", encoding="utf-8") as menu_file:
    MENU = json.load(menu_file)

# Правильная привязка фотографий к блюдам по названию.
# Номера соответствуют проверенным пользователем фотографиям.
import re
from difflib import SequenceMatcher

PHOTO_BY_DISH = {
    "паста с куриным филе и грибами": 1,
    "десерт от шефа": 2,
    "куриный шницель с картофельным пюре": 4,
    "картофельные дольки": 5,
    "кеспе с кониной": 6,
    "салат с хрустящим цыпленком и ореховым соусом": 7,
    "стейк из семги терияки с израильским кускусом": 8,
    "райс боул с семгой": 9,
    "кефаль на мангале": 10,
    "пивное ассорти": 11,
    "колбаски из курицы": 12,
    "кефаль с пастой птитим": 13,
    "говяжьи ребра с картофельным пюре и тыквенным кремом": 14,
    "кефаль под сливочным соусом терияки и картофельным пюре": 15,
    "не шакшука": 16,
    "сырники с яблочным джемом и ванильным кремом": 17,
    "норвежский завтрак": 18,
    "блины с курицей": 19,
    "мидии запеченные": 20,
    "пицца с охотничьими колбасками и халапеньо": 21,
    "блинчики с курицей": 23,
    "меренговый рулет": 24,
    "салат руккола с креветками": 25,
    "соленья домашние": 27,
    "мясные деликатесы": 28,
    "гравлакс из семги": 29,
    "баварский завтрак": 31,
    "бургер классная цыпа": 34,
    "шашлык из баранины": 35,
    "шашлык из шампиньонов": 36,
    "люля кебаб из курицы": 37,
    "креветки к пиву": 38,
    "фарфалле с семгой и брокколи": 39,
    "мозговые косточки с чесночным хлебом": 40,
    "блинчики со сметаной": 43,
    "пирожное тирамису": 45,
    "тирамису": 45,
    "удон с креветками": 46,
    "тигровые креветки в сливочном соусе": 48,
    "бургер подружка мясника": 49,
    "пицца пепперони": 50,
    "рыбная палитра": 51,
    "соус тартар к рыбе": 53,
    "соус тар тар к рыбе": 53,
    "соус томатный к мясу": 54,
    "томатный соус к мясу": 54,
    "салат с телятиной": 55,
    "сырные палочки с соусом тартар": 56,
    "пельмени говяжьи с бульоном": 57,
    "гренки чесночные": 58,
    "картофельное пюре": 59,
    "овощная нарезка с брынзой": 61,
    "печень кебаб": 62,
    "бургер с тигровыми креветками": 63,
    "чечил жареный": 64,
    "пицца куриная bbq": 65,
    "спагетти карбонара": 66,
    "каша рисовая с курагой": 67,
    "рисовая каша с курагой": 67,
    "стрипсы куриные": 68,
    "куриные стрипсы": 68,
    "тальятелле с креветками": 69,
    "тальятелли с креветками": 69,
    "мороженое": 70,
    "рис": 71,
    "шашлык из курицы": 72,
    "запеченные на мангале овощи": 73,
    "овощи на мангале": 73,
    "чизкейк с нутеллой": 75,
    "колбаски из говядины": 76,
}


def normalize_dish_name(value: str) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = text.replace("&", " ").replace("bbq", "bbq")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return " ".join(text.split())


NORMALIZED_PHOTOS = {
    normalize_dish_name(name): number for name, number in PHOTO_BY_DISH.items()
}


def find_photo_number(product_name: str):
    normalized = normalize_dish_name(product_name)
    if normalized in NORMALIZED_PHOTOS:
        return NORMALIZED_PHOTOS[normalized]

    # Небольшие различия в написании: «семга/сёмга», дефисы, порядок слов.
    product_words = set(normalized.split())
    best_number = None
    best_score = 0.0
    for known_name, number in NORMALIZED_PHOTOS.items():
        known_words = set(known_name.split())
        overlap = len(product_words & known_words) / max(1, len(product_words | known_words))
        similarity = SequenceMatcher(None, normalized, known_name).ratio()
        score = similarity * 0.7 + overlap * 0.3
        if score > best_score:
            best_score = score
            best_number = number
    return best_number if best_score >= 0.84 else None


for product in MENU:
    # Сохраняем фотографию, которая уже правильно прописана в menu.json,
    # если соответствующий файл действительно существует.
    existing_image = next(
        (product.get(key) for key in ("image", "photo", "image_url", "photo_url") if product.get(key)),
        None,
    )
    existing_is_valid = False
    if isinstance(existing_image, str) and existing_image.startswith("/static/"):
        existing_path = BASE_DIR / existing_image.lstrip("/")
        existing_is_valid = existing_path.is_file()

    # Убираем дублирующие поля, но не уничтожаем рабочую ссылку.
    for key in ("image", "photo", "image_url", "photo_url"):
        product.pop(key, None)

    if existing_is_valid:
        product["image"] = existing_image
        continue

    # Для остальных блюд используем проверенное сопоставление названия и фото.
    photo_number = find_photo_number(product.get("name", ""))
    if photo_number:
        photo_path = BASE_DIR / "static" / "menu_photos" / f"photo_{photo_number:03d}.webp"
        if photo_path.is_file():
            product["image"] = f"/static/menu_photos/photo_{photo_number:03d}.webp"

PRODUCTS = {int(product["id"]): product for product in MENU}


def packaging_type(category: str, name: str) -> str | None:
    text = f"{category} {name}".lower()

    if (
        "напит" in text
        or "вода" in text
        or "лимонад" in text
        or "pepsi" in text
        or "компот" in text
        or "морс" in text
    ):
        return None
    if "пицц" in text:
        return "Коробки для пиццы"
    if "бургер" in text:
        return "Бургер-боксы"
    if any(x in text for x in ["суп", "борщ", "уха", "солянк", "том ям", "бульон"]):
        return "Контейнеры для супа"
    if "салат" in text or "боул" in text:
        return "Контейнеры для салатов"
    if "шашлык" in text or "люля" in text:
        return "Большие контейнеры для шашлыка"
    if "чебурек" in text:
        return "Коробки для чебуреков"
    if any(x in text for x in ["фри", "закуск", "наггет", "крыл", "кольца", "гренк"]):
        return "Контейнеры для фри и закусок"
    if any(x in text for x in ["десерт", "торт", "чизкейк", "тирамису", "медовик", "брауни"]):
        return "Контейнеры для десертов"

    return "Контейнеры для горячей еды"


def calculate(cart):
    lines = []
    subtotal = 0
    counts = {}

    for row in cart:
        product = PRODUCTS.get(int(row.get("id", 0)))
        qty = max(0, int(row.get("qty", 0)))

        if not product or product.get("hidden", False) or qty < 1:
            continue

        total = product["price"] * qty
        subtotal += total
        lines.append({**product, "qty": qty, "total": total})

        package = packaging_type(
            product["category"],
            product["name"],
        )
        if package:
            counts[package] = counts.get(package, 0) + qty

    utensils = sum(item["qty"] for item in lines)
    if utensils:
        counts["Одноразовые приборы"] = utensils

    packaging = []
    fee = 0

    for name, qty in counts.items():
        unit = PACKAGING_PRICES[name]
        total = unit * qty
        fee += total
        packaging.append(
            {
                "name": name,
                "qty": qty,
                "unit": unit,
                "total": total,
            }
        )

    return lines, subtotal, packaging, fee


def validate_init_data(init_data: str) -> bool:
    if not BOT_TOKEN or not init_data:
        return False

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)

    if not received:
        return False

    data_check = "\n".join(
        f"{key}={value}"
        for key, value in sorted(pairs.items())
    )
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    calculated = hmac.new(
        secret,
        data_check.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(calculated, received)


def get_telegram_user_id(init_data: str):
    if not init_data:
        return None

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        user_raw = parsed.get("user")

        if not user_raw:
            return None

        user = json.loads(user_raw)
        return user.get("id")
    except Exception:
        return None


def normalize_phone(phone: str) -> str:
    return "".join(
        char
        for char in str(phone or "")
        if char.isdigit()
    )


def customer_key(init_data: str, phone: str) -> str:
    telegram_user_id = get_telegram_user_id(init_data)

    if telegram_user_id:
        return f"telegram:{telegram_user_id}"

    normalized_phone = normalize_phone(phone)
    if normalized_phone:
        return f"phone:{normalized_phone}"

    return ""



def save_menu() -> None:
    (BASE_DIR / "menu.json").write_text(
        json.dumps(MENU, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    global PRODUCTS
    PRODUCTS = {int(product["id"]): product for product in MENU}


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


@app.get("/")
def index():
    return render_template("index.html")



@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        flash("Неверный пароль", "error")
    return render_template("admin_login.html")


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_panel():
    categories = sorted({str(item.get("category", "Без категории")) for item in MENU})
    return render_template("admin.html", items=MENU, categories=categories)


@app.post("/admin/item/<int:item_id>/save")
@admin_required
def admin_save_item(item_id: int):
    item = next((row for row in MENU if int(row.get("id", 0)) == item_id), None)
    if not item:
        flash("Блюдо не найдено", "error")
        return redirect(url_for("admin_panel"))

    item["name"] = request.form.get("name", item.get("name", "")).strip()
    item["category"] = request.form.get("category", item.get("category", "")).strip()
    item["description"] = request.form.get("description", "").strip()
    try:
        item["price"] = max(0, int(request.form.get("price", item.get("price", 0))))
    except (TypeError, ValueError):
        flash("Цена должна быть числом", "error")
        return redirect(url_for("admin_panel", edit=item_id))

    item["hidden"] = request.form.get("hidden") == "on"
    image = request.files.get("image")
    if image and image.filename:
        if not allowed_image(image.filename):
            flash("Разрешены PNG, JPG, JPEG и WEBP", "error")
            return redirect(url_for("admin_panel", edit=item_id))
        ext = secure_filename(image.filename).rsplit(".", 1)[1].lower()
        upload_dir = BASE_DIR / "static" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"dish_{item_id}_{int(time.time())}.{ext}"
        image.save(upload_dir / filename)
        item["image"] = f"/static/uploads/{filename}"

    save_menu()
    flash(f"Сохранено: {item['name']}", "success")
    return redirect(url_for("admin_panel", edit=item_id))


@app.post("/admin/item/add")
@admin_required
def admin_add_item():
    next_id = max((int(row.get("id", 0)) for row in MENU), default=0) + 1
    name = request.form.get("name", "Новое блюдо").strip() or "Новое блюдо"
    category = request.form.get("category", "Новинки").strip() or "Новинки"
    try:
        price = max(0, int(request.form.get("price", 0)))
    except (TypeError, ValueError):
        price = 0
    MENU.append({"id": next_id, "category": category, "name": name, "price": price, "description": ""})
    save_menu()
    flash("Новое блюдо добавлено", "success")
    return redirect(url_for("admin_panel", edit=next_id))


@app.post("/admin/item/<int:item_id>/delete")
@admin_required
def admin_delete_item(item_id: int):
    global MENU
    before = len(MENU)
    MENU = [row for row in MENU if int(row.get("id", 0)) != item_id]
    if len(MENU) != before:
        save_menu()
        flash("Блюдо удалено", "success")
    return redirect(url_for("admin_panel"))


@app.get("/api/profile")
def api_profile():
    phone = session.get("guest_phone")
    if not phone:
        return jsonify({"ok": True, "logged_in": False})
    profile = load_profiles().get(phone)
    if not profile:
        session.pop("guest_phone", None)
        return jsonify({"ok": True, "logged_in": False})
    return jsonify({
        "ok": True,
        "logged_in": True,
        "profile": {
            "name": profile.get("name", ""),
            "phone": profile.get("phone", phone),
            "addresses": profile.get("addresses", []),
            "created_at": profile.get("created_at", ""),
            "telegram_linked": bool(profile.get("telegram_id")),
            "telegram_id": profile.get("telegram_id"),
            "bonus_balance": int(profile.get("bonus_balance", 0)),
            "total_spent": int(profile.get("total_spent", 0)),
            "bonus_updated_at": profile.get("bonus_updated_at", ""),
        },
    })


@app.post("/api/profile/register")
def api_profile_register():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    phone = normalize_guest_phone(data.get("phone", ""))
    pin = str(data.get("pin", "")).strip()
    if not name or len(phone) != 11 or not pin.isdigit() or len(pin) != 4:
        return jsonify({"ok": False, "error": "Введите имя, номер телефона и PIN из 4 цифр"}), 400
    profiles = load_profiles()
    if phone in profiles:
        return jsonify({"ok": False, "error": "Профиль с этим номером уже существует"}), 409
    profiles[phone] = {
        "name": name,
        "phone": phone,
        "pin_hash": generate_password_hash(pin),
        "addresses": [],
        "bonus_balance": 0,
        "total_spent": 0,
        "telegram_id": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_profiles(profiles)
    session["guest_phone"] = phone
    return jsonify({"ok": True})


@app.post("/api/profile/login")
def api_profile_login():
    data = request.get_json(silent=True) or {}
    phone = normalize_guest_phone(data.get("phone", ""))
    pin = str(data.get("pin", "")).strip()
    profile = load_profiles().get(phone)
    if not profile or not check_password_hash(profile.get("pin_hash", ""), pin):
        return jsonify({"ok": False, "error": "Неверный номер телефона или PIN"}), 401
    session["guest_phone"] = phone
    return jsonify({"ok": True})


@app.post("/api/profile/logout")
def api_profile_logout():
    session.pop("guest_phone", None)
    return jsonify({"ok": True})


@app.post("/api/profile/address")
def api_profile_address():
    phone = session.get("guest_phone")
    if not phone:
        return jsonify({"ok": False, "error": "Сначала войдите в профиль"}), 401
    data = request.get_json(silent=True) or {}
    address = str(data.get("address", "")).strip()
    if len(address) < 5:
        return jsonify({"ok": False, "error": "Введите полный адрес"}), 400
    profiles = load_profiles()
    profile = profiles.get(phone)
    if not profile:
        return jsonify({"ok": False, "error": "Профиль не найден"}), 404
    addresses = profile.setdefault("addresses", [])
    if address not in addresses:
        addresses.insert(0, address)
        profile["addresses"] = addresses[:5]
        save_profiles(profiles)
    return jsonify({"ok": True, "addresses": profile.get("addresses", [])})


@app.post("/api/profile/telegram-link")
def api_profile_telegram_link():
    phone = session.get("guest_phone")
    if not phone:
        return jsonify({"ok": False, "error": "Сначала войдите в профиль"}), 401
    profiles = load_profiles()
    profile = profiles.get(phone)
    if not profile:
        return jsonify({"ok": False, "error": "Профиль не найден"}), 404
    token = secrets.token_urlsafe(24)
    profile["telegram_link_token"] = token
    profile["telegram_link_expires"] = int(time.time()) + 15 * 60
    save_profiles(profiles)
    return jsonify({
        "ok": True,
        "bot_link": f"https://t.me/{CLUB_BOT_USERNAME}?start=club_{token}",
        "expires_in": 900,
    })


@app.post("/api/integration/link")
def api_integration_link():
    if not integration_authorized():
        return jsonify({"ok": False, "error": "Нет доступа"}), 403
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    telegram_id = str(data.get("telegram_id", "")).strip()
    phone = normalize_guest_phone(data.get("phone", ""))
    profiles = load_profiles()
    matched_phone = None
    for profile_phone, profile in profiles.items():
        if (
            profile.get("telegram_link_token") == token
            and int(profile.get("telegram_link_expires", 0)) >= int(time.time())
        ):
            matched_phone = profile_phone
            break
    if not matched_phone:
        return jsonify({"ok": False, "error": "Код привязки истёк или неверен"}), 404
    # Одноразовый секретный токен уже подтверждает, что привязку начал
    # владелец текущей сессии сайта. Телефон бота сохраняем для справки,
    # но не блокируем привязку из-за другого формата или старого номера.
    profile = profiles[matched_phone]
    profile.update({
        "telegram_id": telegram_id,
        "telegram_username": str(data.get("telegram_username", "")),
        "telegram_phone": phone,
        "bonus_balance": max(0, int(data.get("bonus_balance", 0))),
        "total_spent": max(0, int(data.get("total_spent", 0))),
        "bonus_updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    profile.pop("telegram_link_token", None)
    profile.pop("telegram_link_expires", None)
    save_profiles(profiles)
    return jsonify({"ok": True, "phone": matched_phone})


@app.post("/api/integration/sync")
def api_integration_sync():
    if not integration_authorized():
        return jsonify({"ok": False, "error": "Нет доступа"}), 403
    data = request.get_json(silent=True) or {}
    telegram_id = str(data.get("telegram_id", "")).strip()
    phone = normalize_guest_phone(data.get("phone", ""))
    profiles = load_profiles()
    matched_phone = None
    # Сначала ищем уже привязанный Telegram ID.
    for profile_phone, profile in profiles.items():
        if str(profile.get("telegram_id", "")) == telegram_id:
            matched_phone = profile_phone
            break

    # Если Telegram ещё не привязан, автоматически связываем аккаунты
    # по нормализованному номеру телефона. Проверяем и ключ словаря,
    # и поле phone внутри профиля, чтобы старые форматы номеров не мешали.
    if not matched_phone and phone:
        for profile_phone, profile in profiles.items():
            key_phone = normalize_guest_phone(profile_phone)
            saved_phone = normalize_guest_phone(profile.get("phone", profile_phone))
            if phone in {key_phone, saved_phone}:
                matched_phone = profile_phone
                break

    if not matched_phone:
        return jsonify({
            "ok": False,
            "error": "Профиль сайта с таким номером не найден. Войдите на сайте под тем же номером, который указан в SOBLAZN CLUB.",
        }), 404
    profile = profiles[matched_phone]
    profile["telegram_id"] = telegram_id
    profile["bonus_balance"] = max(0, int(data.get("bonus_balance", 0)))
    profile["total_spent"] = max(0, int(data.get("total_spent", 0)))
    profile["bonus_updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_profiles(profiles)
    return jsonify({"ok": True})


@app.get("/api/integration/pending-spends")
def api_integration_pending_spends():
    if not integration_authorized():
        return jsonify({"ok": False, "error": "Нет доступа"}), 403
    queue = load_bonus_queue()
    now = int(time.time())
    result = []
    changed = False
    for transaction_id, item in queue.items():
        status = item.get("status")
        if status == "processing" and now - int(item.get("processing_at", 0)) > 90:
            item["status"] = "pending"
            status = "pending"
            changed = True
        if status == "pending" and len(result) < 20:
            item["status"] = "processing"
            item["processing_at"] = now
            changed = True
            result.append({
                "transaction_id": transaction_id,
                "telegram_id": item.get("telegram_id"),
                "amount": int(item.get("amount", 0)),
                "order_number": item.get("order_number"),
            })
    if changed:
        save_bonus_queue(queue)
    return jsonify({"ok": True, "items": result})


@app.post("/api/integration/spend-result")
def api_integration_spend_result():
    if not integration_authorized():
        return jsonify({"ok": False, "error": "Нет доступа"}), 403
    data = request.get_json(silent=True) or {}
    transaction_id = str(data.get("transaction_id", ""))
    success = bool(data.get("success"))
    queue = load_bonus_queue()
    item = queue.get(transaction_id)
    if not item:
        return jsonify({"ok": False, "error": "Операция не найдена"}), 404
    if item.get("status") in {"completed", "failed"}:
        return jsonify({"ok": True, "duplicate": True})
    profiles = load_profiles()
    phone = item.get("phone")
    profile = profiles.get(phone)
    if success:
        item["status"] = "completed"
        item["completed_at"] = datetime.now().isoformat(timespec="seconds")
        if profile:
            profile["bonus_balance"] = max(0, int(data.get("balance", profile.get("bonus_balance", 0))))
            profile["total_spent"] = max(0, int(data.get("total_spent", profile.get("total_spent", 0))))
            profile["bonus_updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_profiles(profiles)
    else:
        item["status"] = "failed"
        item["error"] = str(data.get("error", "Не удалось списать бонусы"))
        if profile and not item.get("restored"):
            profile["bonus_balance"] = int(profile.get("bonus_balance", 0)) + int(item.get("amount", 0))
            profile["bonus_updated_at"] = datetime.now().isoformat(timespec="seconds")
            item["restored"] = True
            save_profiles(profiles)
    save_bonus_queue(queue)
    return jsonify({"ok": True})


@app.get("/api/menu")
def api_menu():
    return jsonify(
        {
            "items": [item for item in MENU if not item.get("hidden", False)],
            "delivery_fee": DELIVERY_FEE,
        }
    )


@app.post("/api/calculate")
def api_calculate():
    lines, subtotal, packaging, fee = calculate(
        (request.get_json(silent=True) or {}).get("cart", [])
    )
    return jsonify(
        {
            "items": lines,
            "subtotal": subtotal,
            "packaging": packaging,
            "packaging_fee": fee,
            "delivery_fee": DELIVERY_FEE,
            "total": subtotal + fee + DELIVERY_FEE,
        }
    )


@app.post("/api/order")
def api_order():
    data = request.get_json(silent=True) or {}

    if (
        os.getenv("REQUIRE_TELEGRAM_AUTH", "0") == "1"
        and not validate_init_data(data.get("initData", ""))
    ):
        return jsonify(
            {
                "ok": False,
                "error": "Не удалось проверить пользователя Telegram",
            }
        ), 403

    items, subtotal, packaging, packaging_fee = calculate(
        data.get("cart", [])
    )

    if not items:
        return jsonify(
            {
                "ok": False,
                "error": "Корзина пуста",
            }
        ), 400

    customer = data.get("customer", {})
    guest_phone = session.get("guest_phone")
    if guest_phone:
        profiles = load_profiles()
        profile = profiles.get(guest_phone)
        if profile:
            customer["name"] = profile.get("name") or customer.get("name")
            customer["phone"] = profile.get("phone") or customer.get("phone")
            address = str(customer.get("address", "")).strip()
            if address:
                addresses = profile.setdefault("addresses", [])
                if address not in addresses:
                    addresses.insert(0, address)
                    profile["addresses"] = addresses[:5]
                    save_profiles(profiles)
    base_total = subtotal + packaging_fee + DELIVERY_FEE
    bonus_used = 0
    bonus_transaction_id = ""
    profile_for_bonus = None
    profiles_for_bonus = None
    requested_bonus = data.get("bonus_used", 0)
    try:
        requested_bonus = int(requested_bonus or 0)
    except (TypeError, ValueError):
        requested_bonus = -1
    if requested_bonus < 0:
        return jsonify({"ok": False, "error": "Некорректное количество бонусов"}), 400
    if requested_bonus:
        if not guest_phone:
            return jsonify({"ok": False, "error": "Для списания бонусов войдите в профиль"}), 401
        profiles_for_bonus = load_profiles()
        profile_for_bonus = profiles_for_bonus.get(guest_phone)
        if not profile_for_bonus or not profile_for_bonus.get("telegram_id"):
            return jsonify({"ok": False, "error": "Сначала подключите SOBLAZN CLUB в личном кабинете"}), 409
        available = int(profile_for_bonus.get("bonus_balance", 0))
print(
    "BONUS DEBUG:",
    "phone=", guest_phone,
    "available=", available,
    "base_total=", base_total,
    "requested=", requested_bonus,
    "profile=", profile_for_bonus
)
        max_allowed = min(available, base_total)
        if requested_bonus > max_allowed:
            return jsonify({"ok": False, "error": f"Можно использовать не более {max_allowed} бонусов"}), 409
        bonus_used = requested_bonus
    total = base_total - bonus_used

    item_text = "\n".join(
        f"• {item['name']} × {item['qty']} = {item['total']} ₸"
        for item in items
    )
    package_text = "\n".join(
        f"• {item['name']}: {item['qty']} × {item['unit']} ₸ = {item['total']} ₸"
        for item in packaging
    )

    order_number = datetime.now().strftime("%d%m%H%M%S")
    tracking_token = secrets.token_urlsafe(18)

    init_data = data.get("initData", "")
    telegram_user_id = get_telegram_user_id(init_data)
    order_customer_key = customer_key(
        init_data,
        customer.get("phone", ""),
    )

    text = (
        f"🚨 НОВЫЙ ЗАКАЗ №{order_number} ИЗ MINI APP\n\n"
        f"👤 {customer.get('name', '—')}\n"
        f"📱 {customer.get('phone', '—')}\n"
        f"🏠 {customer.get('address', '—')}\n"
        f"💬 {customer.get('comment', 'Нет') or 'Нет'}\n"
        f"💳 {customer.get('payment', '—')}\n\n"
        f"🍽 ЗАКАЗ:\n{item_text}\n\n"
        f"📦 УПАКОВКА:\n{package_text}\n\n"
        f"🍽 Блюда: {subtotal} ₸\n"
        f"📦 Упаковка: {packaging_fee} ₸\n"
        f"🚚 Доставка: {DELIVERY_FEE} ₸\n"
        + (f"🎁 Бонусами: −{bonus_used} ₸\n" if bonus_used else "")
        + f"💰 ИТОГО К ОПЛАТЕ: {total} ₸"
    )

    order = {
        "order_number": order_number,
        "base_text": text,
        "status": "new",
        "admin": None,
        "courier": None,
        "tracking_token": tracking_token,
        "telegram_user_id": telegram_user_id,
        "customer_key": order_customer_key,
        "customer_name": customer.get("name", "—"),
        "customer_phone": customer.get("phone", "—"),
        "customer_address": customer.get("address", "—"),
        "customer_comment": customer.get("comment", ""),
        "customer_payment": customer.get("payment", "—"),
        "cart": [
            {
                "id": item["id"],
                "qty": item["qty"],
            }
            for item in items
        ],
        "items": [
            {
                "id": item["id"],
                "name": item["name"],
                "price": item["price"],
                "qty": item["qty"],
                "total": item["total"],
            }
            for item in items
        ],
        "subtotal": subtotal,
        "packaging_fee": packaging_fee,
        "delivery_fee": DELIVERY_FEE,
        "bonus_used": bonus_used,
        "base_total": base_total,
        "total": total,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    orders = load_orders()
    orders[order_number] = order
    save_orders(orders)

    if bonus_used and profile_for_bonus and profiles_for_bonus is not None:
        bonus_transaction_id = secrets.token_urlsafe(18)
        profile_for_bonus["bonus_balance"] = int(profile_for_bonus.get("bonus_balance", 0)) - bonus_used
        profile_for_bonus["bonus_updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_profiles(profiles_for_bonus)
        queue = load_bonus_queue()
        queue[bonus_transaction_id] = {
            "status": "pending",
            "phone": guest_phone,
            "telegram_id": str(profile_for_bonus.get("telegram_id")),
            "amount": bonus_used,
            "order_number": order_number,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_bonus_queue(queue)
        order["bonus_transaction_id"] = bonus_transaction_id
        orders[order_number] = order
        save_orders(orders)

    if BOT_TOKEN and COURIER_CHAT_ID:
        response = telegram_call(
            "sendMessage",
            {
                "chat_id": COURIER_CHAT_ID,
                "text": build_status_text(order),
                "reply_markup": status_keyboard(
                    order_number,
                    "new",
                ),
            },
            15,
        )

        if not response.ok:
            orders.pop(order_number, None)
            save_orders(orders)
            if bonus_used and profile_for_bonus and profiles_for_bonus is not None:
                profile_for_bonus["bonus_balance"] = int(profile_for_bonus.get("bonus_balance", 0)) + bonus_used
                save_profiles(profiles_for_bonus)
                queue = load_bonus_queue()
                queue.pop(bonus_transaction_id, None)
                save_bonus_queue(queue)
            return jsonify(
                {
                    "ok": False,
                    "error": "Telegram не принял заказ",
                    "details": response.text,
                }
            ), 502
    else:
        print(text, flush=True)

    username = get_bot_username()
    bot_link = (
        f"tg://resolve?domain={username}"
        f"&start=order_{order_number}_{tracking_token}"
        if username
        else ""
    )

    return jsonify(
        {
            "ok": True,
            "total": total,
            "bonus_used": bonus_used,
            "message": "Заказ отправлен",
            "order_number": order_number,
            "tracking_token": tracking_token,
            "status": "new",
            "bot_link": bot_link,
        }
    )


@app.get("/api/order_status/<order_number>")
def api_order_status(order_number: str):
    token = request.args.get("token", "")
    order = load_orders().get(order_number)

    if (
        not order
        or not token
        or not hmac.compare_digest(
            str(order.get("tracking_token", "")),
            token,
        )
    ):
        return jsonify(
            {
                "ok": False,
                "error": "Заказ не найден",
            }
        ), 404

    status = order.get("status", "new")

    return jsonify(
        {
            "ok": True,
            "order_number": order_number,
            "status": status,
            "status_text": STATUS_LABELS.get(status, status),
            "admin": order.get("admin"),
            "courier": order.get("courier"),
            "total": order.get("total"),
            "created_at": order.get("created_at"),
        }
    )


@app.post("/api/my_orders")
def api_my_orders():
    data = request.get_json(silent=True) or {}
    init_data = str(data.get("initData", ""))
    phone = str(data.get("phone", ""))

    if (
        os.getenv("REQUIRE_TELEGRAM_AUTH", "0") == "1"
        and not validate_init_data(init_data)
    ):
        return jsonify(
            {
                "ok": False,
                "error": "Не удалось проверить пользователя Telegram",
            }
        ), 403

    key = customer_key(init_data, phone)

    if not key:
        return jsonify(
            {
                "ok": False,
                "error": "Введите номер телефона",
            }
        ), 400

    result = []

    for order in load_orders().values():
        if order.get("customer_key") != key:
            continue

        result.append(
            {
                "order_number": order.get("order_number"),
                "status": order.get("status", "new"),
                "status_text": STATUS_LABELS.get(
                    order.get("status", "new"),
                    order.get("status", "new"),
                ),
                "total": order.get("total", 0),
                "created_at": order.get("created_at"),
                "items": order.get("items", []),
                "cart": order.get("cart", []),
                "tracking_token": order.get("tracking_token", ""),
                "courier": order.get("courier"),
            }
        )

    result.sort(
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )

    return jsonify(
        {
            "ok": True,
            "orders": result[:30],
        }
    )


@app.get("/health")
def health():
    return {"ok": True}


if os.getenv("RUN_TELEGRAM_POLLING") == "1":
    threading.Thread(
        target=polling_loop,
        daemon=True,
    ).start()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=False,
        use_reloader=False,
    )
