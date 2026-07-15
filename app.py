import hashlib
import hmac
import json
import os
import threading
import time
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

import requests
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent


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
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
COURIER_CHAT_ID = os.getenv("COURIER_CHAT_ID", "-1004342107012").strip()
DELIVERY_FEE = int(os.getenv("DELIVERY_FEE", "1000"))
BOT_USERNAME = ""

ORDERS_FILE = BASE_DIR / "mini_app_orders.json"


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

        if not product or qty < 1:
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


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/menu")
def api_menu():
    return jsonify(
        {
            "items": MENU,
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
    total = subtotal + packaging_fee + DELIVERY_FEE

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
        f"💰 ИТОГО: {total} ₸"
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
        "total": total,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    orders = load_orders()
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
