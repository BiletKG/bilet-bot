import os
import sqlite3
import random
import string
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import logging
import asyncio
from io import BytesIO

# Для работы с PDF
import PyPDF2
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logging.basicConfig(level=logging.INFO)

# ========== ТВОИ ДАННЫЕ (ВСТАВЬ СЮДА) ==========
BOT_TOKEN = "8699646464:AAFpi4VqeGI6JTa24Wz0-QPSrWjiMUhkoLI"  # <- ВСТАВЬ СВОЙ ТОКЕН
ADMIN_ID = 8081555684  # <- ВСТАВЬ СВОЙ ID (ТОЛЬКО ЦИФРЫ)
# =============================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========== ДАННЫЕ СОБЫТИЙ ==========
EVENTS = {
    "BASE": {
        "name": "Основной билет",
        "template": "template.pdf",
        "prefix": ""
    },
    "ANDRO": {
        "name": "Andro",
        "template": "template2.pdf",
        "prefix": "AN"
    },
    "WOMENFEST": {
        "name": "Women Fest",
        "template": "template4.pdf",
        "prefix": "WF"
    },
    "MOT": {
        "name": "MOT",
        "template": "template3.pdf",
        "prefix": "MOT"
    },
}

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('tickets.db', check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицы
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
               (id INTEGER PRIMARY KEY, user_id TEXT UNIQUE, fio TEXT, 
                ticket_number TEXT, secret_key TEXT, date TEXT)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS active_keys 
               (id INTEGER PRIMARY KEY, key TEXT UNIQUE, used BOOLEAN DEFAULT 0, 
                created_at TEXT, used_by TEXT)''')

# Миграции: добавляем поле события, если его ещё нет
try:
    cursor.execute("ALTER TABLE active_keys ADD COLUMN event_code TEXT")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE users ADD COLUMN event_code TEXT")
except sqlite3.OperationalError:
    pass
conn.commit()

# ========== СОСТОЯНИЯ ==========
class States(StatesGroup):
    wait_key = State()
    wait_fio = State()
    wait_new_key_count = State()
    wait_event_choice = State()
    wait_delete_key = State()

# ========== ФУНКЦИИ ДЛЯ КЛЮЧЕЙ ==========
def generate_key(total_length=8, prefix=""):
    """Генерирует случайный ключ c префиксом артиста"""
    characters = string.ascii_uppercase + string.digits
    random_length = max(1, total_length - len(prefix))
    random_part = ''.join(random.choice(characters) for _ in range(random_length))
    return prefix + random_part

def generate_multiple_keys(count, event_code="BASE"):
    """Генерирует несколько уникальных ключей для конкретного события"""
    event = EVENTS.get(event_code, EVENTS["BASE"])
    prefix = event.get("prefix", "") or ""
    keys = []
    for _ in range(count):
        while True:
            key = generate_key(total_length=8, prefix=prefix)
            cursor.execute("SELECT id FROM active_keys WHERE key = ?", (key,))
            if not cursor.fetchone():
                keys.append(key)
                break
    return keys

def save_keys_to_db(keys, event_code="BASE"):
    """Сохраняет ключи в базу данных с привязкой к событию"""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    for key in keys:
        cursor.execute(
            "INSERT INTO active_keys (key, used, created_at, event_code) VALUES (?, ?, ?, ?)",
            (key, 0, now, event_code)
        )
    conn.commit()

def get_unused_keys_count():
    """Получает количество неиспользованных ключей"""
    cursor.execute("SELECT COUNT(*) FROM active_keys WHERE used = 0")
    return cursor.fetchone()[0]

def validate_key(key):
    """Проверяет валидность ключа и возвращает информацию о событии"""
    cursor.execute("SELECT id, event_code FROM active_keys WHERE key = ? AND used = 0", (key,))
    result = cursor.fetchone()
    return result  # (id, event_code) или None

def mark_key_as_used(key, user_id):
    """Отмечает ключ как использованный"""
    cursor.execute("UPDATE active_keys SET used = 1, used_by = ? WHERE key = ?", 
                  (user_id, key))
    conn.commit()

# ========== СЧЁТЧИК БИЛЕТОВ ==========
def get_next_ticket_number():
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    # Все номера начинаются с №18200983 и идут по порядку
    return f"№{18200983 + count}"

# ========== ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ КУРСИВА ==========
def draw_italic_text(c, font_name, font_size, x, y, text):
    """
    Рисует текст с псевдо-курсивом (наклон) даже если у шрифта нет отдельного italic-файла.
    """
    c.saveState()
    c.setFont(font_name, font_size)
    # Смещение в нужную точку
    c.translate(x, y)
    # Наклон по оси X (10 градусов)
    c.skew(0, 10)
    c.drawString(0, 0, text)
    c.restoreState()


# ========== ГЕНЕРАЦИЯ PDF БИЛЕТА (С ПОДДЕРЖКОЙ КИРИЛЛИЦЫ) ==========
async def create_ticket_pdf(fio, ticket_num, event_code: str | None = None):
    """
    Берет исходный PDF файл (template.pdf) и добавляет на него номер билета и ФИО
    """
    try:
        # Создаем временный PDF с текстом (номер и ФИО)
        packet = BytesIO()
        c = canvas.Canvas(packet, pagesize=A4)
        
        # ========== РЕГИСТРИРУЕМ ШРИФТ С КИРИЛЛИЦЕЙ (и курсивом, если возможно) ==========
        # Пробуем разные шрифты с поддержкой кириллицы
        font_name = 'Helvetica'  # По умолчанию

        # Список возможных шрифтов для проверки
        font_paths = [
            "arialmt.ttf",                 # шрифт из корневой папки бота
            "arial.ttf",
            "DejaVuSans.ttf",
            "times.ttf",
            "C:/Windows/Fonts/arial.ttf",  # Для Windows
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Для Linux
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('CustomFont', font_path))
                    font_name = 'CustomFont'
                    print(f"✅ Загружен шрифт: {font_path}")
                    break
                except Exception:
                    continue

        if font_name == 'Helvetica':
            print("⚠️ ВНИМАНИЕ: Не найден шрифт с кириллицей! Русские буквы могут не отобразиться.")
        # ====================================================
        
        # Настройки текста
        c.setFillColorRGB(0, 0, 0)  # Черный цвет
        
        # Определяем событие и шаблон
        event_code = event_code or "BASE"
        event = EVENTS.get(event_code, EVENTS["BASE"])
        template_path = event.get("template") or "template.pdf"
        
        # Координаты номера и ФИО (как у первого генератора)
        ticket_x = 300
        ticket_y = 500
        fio_x = 300
        fio_y = 450

        # Добавляем номер билета (псевдо-курсив, уменьшенный шрифт, чтобы вписываться в шаблон)
        ticket_text = ticket_num  # Просто используем номер как есть
        draw_italic_text(c, font_name, 16, ticket_x, ticket_y, ticket_text)
        
        # Добавляем ФИО (псевдо-курсив, с кириллицей, уменьшенный шрифт под шаблон)
        draw_italic_text(c, font_name, 12, fio_x, fio_y, f"Владелец: {fio}")
        
        c.save()
        packet.seek(0)
        
        if os.path.exists(template_path):
            # Читаем исходный PDF шаблон
            existing_pdf = PyPDF2.PdfReader(open(template_path, 'rb'))
            new_pdf = PyPDF2.PdfWriter()
            
            # Получаем страницу с текстом
            new_page = PyPDF2.PdfReader(packet).pages[0]
            
            # Объединяем с первой страницей шаблона
            page = existing_pdf.pages[0]
            page.merge_page(new_page)
            new_pdf.add_page(page)
            
            # Если в шаблоне больше страниц, добавляем их без изменений
            for i in range(1, len(existing_pdf.pages)):
                new_pdf.add_page(existing_pdf.pages[i])
            
            # Сохраняем результат в буфер (без сохранения на диск)
            output = BytesIO()
            new_pdf.write(output)
            output.seek(0)

            return output, None
        else:
            logging.warning("Файл template.pdf не найден!")
            return await create_simple_pdf(fio, ticket_num, font_name)
            
    except Exception as e:
        logging.error(f"Ошибка при создании PDF: {e}")
        return await create_simple_pdf(fio, ticket_num)

async def create_simple_pdf(fio, ticket_num, font_name='Helvetica', italic_font_name=None):
    """Создает простой PDF если нет шаблона"""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Простой фон
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, width, height, fill=1)
    
    # Рамка
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(2)
    c.rect(50, 50, width-100, height-100)
    
    # Заголовок
    c.setFont(font_name, 24)
    c.drawString(200, 700, "БИЛЕТ")
    
    # Номер (псевдо-курсив, уменьшенный шрифт, по центру)
    c.setFillColorRGB(0, 0, 0)
    text_width = c.stringWidth(ticket_num, font_name, 16)
    x_position = (width - text_width) / 2
    draw_italic_text(c, font_name, 16, x_position, 500, ticket_num)
    
    # ФИО (псевдо-курсив, уменьшенный шрифт)
    draw_italic_text(c, font_name, 14, 200, 400, f"Владелец: {fio}")
    
    # Дата
    date_text = f"Дата: {datetime.now().strftime('%d.%m.%Y')}"
    c.drawString(200, 350, date_text)
    
    c.save()
    buffer.seek(0)

    # Не сохраняем файл на диск, сразу возвращаем в память
    return buffer, None

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    """Основная клавиатура для пользователей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎫 Получить билет")],
            [KeyboardButton(text="ℹ️ Информация")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_admin_keyboard():
    """Клавиатура для админа"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📋 Список билетов")],
            [KeyboardButton(text="🔑 Сгенерировать ключи"), KeyboardButton(text="📈 Статус ключей")],
            [KeyboardButton(text="🗑 Удалить ключ")],
            [KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

# ========== КОМАНДЫ ==========
@dp.message(Command("start"))
async def start(msg: types.Message, state: FSMContext):
    await msg.answer(
        "Bilet KG — это удобный сервис для онлайн-покупок билетов на самые популярные концерты!\n"
        "Мы выбираем — скорость!\n\n"
        "Используй кнопки ниже для навигации.",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "🏠 Главное меню")
async def main_menu(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard() if msg.from_user.id != ADMIN_ID else get_admin_keyboard()
    )

@dp.message(F.text == "ℹ️ Информация")
async def info(msg: types.Message):
    unused_keys = get_unused_keys_count()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_tickets = cursor.fetchone()[0]
    
    await msg.answer(
        f"ℹ️ **Информация о боте**\n\n"
        f"📄 Этот бот выдает персональные билеты в формате PDF.\n"
        f"🔑 Для получения билета нужен специальный ключ.\n"
        f"🎫 Ключи можно получить у администратора.\n\n"
        f"📊 **Статистика:**\n"
        f"• Всего выдано билетов: {total_tickets}\n"
        f"• Доступно ключей: {unused_keys}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "🎫 Получить билет")
async def get_ticket_start(msg: types.Message, state: FSMContext):
    # Проверяем, не получал ли пользователь уже билет
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (str(msg.from_user.id),))
    if cursor.fetchone():
        await msg.answer(
            "❌ Ты уже получил билет! Каждый пользователь может получить только один билет.",
            reply_markup=get_main_keyboard()
        )
        return
    
    await state.set_state(States.wait_key)
    await msg.answer(
        "🔑 Введи ключ для получения билета в формате PDF:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(F.text == "❌ Отмена")
async def cancel(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "Действие отменено.",
        reply_markup=get_main_keyboard() if msg.from_user.id != ADMIN_ID else get_admin_keyboard()
    )

@dp.message(States.wait_key)
async def check_key(msg: types.Message, state: FSMContext):
    if msg.text == "❌ Отмена":
        await cancel(msg, state)
        return
    
    key_info = validate_key(msg.text)
    if key_info:
        _, event_code = key_info
        await state.update_data(key=msg.text, event_code=event_code or "BASE")
        await state.set_state(States.wait_fio)
        await msg.answer("✅ Ключ верный! Введи свои ФИО:")
    else:
        await msg.answer("❌ Неверный или уже использованный ключ. Попробуй ещё раз:")

@dp.message(States.wait_fio)
async def get_fio(msg: types.Message, state: FSMContext):
    if msg.text == "❌ Отмена":
        await cancel(msg, state)
        return
    
    fio_input = msg.text.strip()
    parts = fio_input.split()
    if len(parts) < 2:
        await msg.answer("❌ Пожалуйста, введи **Имя и Фамилию** через пробел.")
        return
    # Берём только Имя и Фамилию (первые два слова)
    fio = " ".join(parts[:2])
    
    # Двойная проверка (на всякий случай)
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (str(msg.from_user.id),))
    if cursor.fetchone():
        await msg.answer("❌ Ты уже получил билет!")
        await state.clear()
        await msg.answer("Главное меню:", reply_markup=get_main_keyboard())
        return
    
    data = await state.get_data()
    key = data.get('key')
    event_code = data.get('event_code', 'BASE')
    
    ticket_num = get_next_ticket_number()
    
    try:
        # Создаем PDF билет на основе исходного шаблона
        pdf_buffer, file_path = await create_ticket_pdf(fio, ticket_num, event_code=event_code)
        
        # Сохраняем пользователя
        cursor.execute("""INSERT INTO users 
                         (user_id, fio, ticket_number, secret_key, date, event_code) 
                         VALUES (?, ?, ?, ?, ?, ?)""",
                      (str(msg.from_user.id), fio, ticket_num, key, 
                       datetime.now().strftime("%d.%m.%Y %H:%M"), event_code))
        
        # Отмечаем ключ как использованный
        mark_key_as_used(key, str(msg.from_user.id))
        
        conn.commit()
        
        # Отправляем PDF
        await msg.answer_document(
            types.input_file.BufferedInputFile(
                pdf_buffer.getvalue(), 
                filename=f"Билет_{ticket_num}.pdf"
            ),
            caption=f"🎫 **ТВОЙ БИЛЕТ {ticket_num}**\n"
                   f"👤 {fio}\n\n"
                   f"📄 Файл в формате PDF сохранен!\n"
                   f"Сохрани этот билет!",
            parse_mode="Markdown"
        )
        
        # Уведомляем админа
        await bot.send_message(
            ADMIN_ID, 
            f"🎫 **Новый билет!**\n"
            f"👤 {fio}\n"
            f"🎟 {ticket_num}\n"
            f"🔑 Ключ: {key}\n"
            f"🆔 User ID: {msg.from_user.id}",
            parse_mode="Markdown"
        )
        
        await msg.answer(
            "✅ Билет успешно получен!",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        await msg.answer(f"❌ Ошибка при создании билета: {e}")
        logging.error(f"Error creating PDF ticket: {e}")
    
    await state.clear()

# ========== АДМИН ПАНЕЛЬ ==========
@dp.message(F.text == "📊 Статистика")
@dp.message(Command("admin"))
async def admin_panel(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer("❌ У вас нет прав администратора.")
        return
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT fio, ticket_number, date FROM users ORDER BY id DESC LIMIT 5")
    last = cursor.fetchall()
    
    unused_keys = get_unused_keys_count()
    
    text = f"📊 **СТАТИСТИКА**\n"
    text += f"━━━━━━━━━━━━━━━\n"
    text += f"🎫 Всего билетов (PDF): {total}\n"
    text += f"🔑 Неиспользованных ключей: {unused_keys}\n\n"
    
    if last:
        text += "**📋 Последние 5 билетов:**\n"
        for fio, num, date in last:
            text += f"• {num} - {fio}\n  📅 {date}\n"
    
    await msg.answer(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

@dp.message(F.text == "📋 Список билетов")
@dp.message(Command("list"))
async def list_tickets(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    
    cursor.execute("SELECT fio, ticket_number, date FROM users ORDER BY id")
    all_users = cursor.fetchall()
    
    if not all_users:
        await msg.answer("📭 Пока нет билетов", reply_markup=get_admin_keyboard())
        return
    
    # Разбиваем на части, если список большой
    text = "🎫 **ВСЕ БИЛЕТЫ (PDF):**\n━━━━━━━━━━━━━━━\n"
    for fio, num, date in all_users:
        entry = f"{num} - {fio}\n📅 {date}\n\n"
        if len(text + entry) > 4000:
            await msg.answer(text, parse_mode="Markdown")
            text = "📋 **Продолжение:**\n━━━━━━━━━━━━━━━\n"
        text += entry
    
    if text:
        await msg.answer(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

@dp.message(F.text == "🔑 Сгенерировать ключи")
async def generate_keys_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(States.wait_event_choice)
    await msg.answer(
        "🎤 Для какого артиста/события сгенерировать ключи?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🎤 Andro"), KeyboardButton(text="🎤 Women Fest")],
                [KeyboardButton(text="🎤 MOT"), KeyboardButton(text="🎟 Основной билет")],
                [KeyboardButton(text="❌ Отмена")],
            ],
            resize_keyboard=True
        )
    )

@dp.message(States.wait_event_choice)
async def generate_keys_choose_event(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    if msg.text == "❌ Отмена":
        await cancel(msg, state)
        return
    
    text = msg.text.strip()
    if text == "🎤 Andro":
        event_code = "ANDRO"
    elif text == "🎤 Women Fest":
        event_code = "WOMENFEST"
    elif text == "🎤 MOT":
        event_code = "MOT"
    elif text == "🎟 Основной билет":
        event_code = "BASE"
    else:
        await msg.answer(
            "❌ Не понял выбор. Пожалуйста, выбери один из вариантов на клавиатуре."
        )
        return
    
    await state.update_data(event_code=event_code)
    await state.set_state(States.wait_new_key_count)
    await msg.answer(
        "🔢 Сколько ключей сгенерировать? (1-50)\n"
        "Отправь число:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(States.wait_new_key_count)
async def generate_keys_process(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    if msg.text == "❌ Отмена":
        await cancel(msg, state)
        return
    
    try:
        count = int(msg.text)
        if count < 1 or count > 50:
            await msg.answer("❌ Число должно быть от 1 до 50. Попробуй снова:")
            return
        
        data = await state.get_data()
        event_code = data.get("event_code", "BASE")
        
        keys = generate_multiple_keys(count, event_code=event_code)
        save_keys_to_db(keys, event_code=event_code)
        
        # Формируем текст с ключами
        keys_text = "\n".join([f"`{key}`" for key in keys])
        
        await msg.answer(
            f"✅ Сгенерировано {count} ключей:\n\n{keys_text}\n\n"
            f"📊 Всего неиспользованных ключей: {get_unused_keys_count()}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
    except ValueError:
        await msg.answer("❌ Пожалуйста, отправь число.")
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
    
    await state.clear()

@dp.message(F.text == "📈 Статус ключей")
async def keys_status(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        return
    
    cursor.execute("SELECT COUNT(*) FROM active_keys WHERE used = 0")
    unused = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM active_keys WHERE used = 1")
    used = cursor.fetchone()[0]
    
    cursor.execute("SELECT key, created_at, used_by FROM active_keys WHERE used = 1 ORDER BY id DESC LIMIT 5")
    last_used = cursor.fetchall()
    
    text = f"🔑 **СТАТУС КЛЮЧЕЙ**\n"
    text += f"━━━━━━━━━━━━━━━\n"
    text += f"✅ Неиспользовано: {unused}\n"
    text += f"❌ Использовано: {used}\n"
    text += f"📊 Всего: {unused + used}\n\n"
    
    if last_used:
        text += "**📋 Последние использованные:**\n"
        for key, date, user_id in last_used:
            text += f"• `{key}`\n  📅 {date}\n  👤 {user_id}\n"
    
    await msg.answer(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

@dp.message(F.text == "🗑 Удалить ключ")
async def delete_key_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(States.wait_delete_key)
    await msg.answer(
        "🔑 Введи неиспользованный ключ, который нужно удалить:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(States.wait_delete_key)
async def delete_key_process(msg: types.Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    if msg.text == "❌ Отмена":
        await cancel(msg, state)
        return
    
    key = msg.text.strip().upper()
    
    cursor.execute("SELECT id, used FROM active_keys WHERE key = ?", (key,))
    row = cursor.fetchone()
    
    if not row:
        await msg.answer("❌ Такой ключ не найден. Введи другой ключ или нажми «❌ Отмена».")
        return
    
    key_id, used = row
    
    if used:
        await msg.answer("❌ Этот ключ уже использован и не может быть удалён. Введи другой ключ или нажми «❌ Отмена».")
        return
    
    cursor.execute("DELETE FROM active_keys WHERE id = ?", (key_id,))
    conn.commit()
    
    await state.clear()
    await msg.answer(
        f"✅ Ключ `{key}` удалён.",
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )

# ========== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ==========
@dp.message()
async def handle_text(msg: types.Message):
    if msg.from_user.id == ADMIN_ID:
        await msg.answer(
            "Используй кнопки для навигации",
            reply_markup=get_admin_keyboard()
        )
    else:
        await msg.answer(
            "Используй кнопки для навигации",
            reply_markup=get_main_keyboard()
        )

# ========== ЗАПУСК ==========
async def main():
    # Создаем несколько начальных ключей при первом запуске
    cursor.execute("SELECT COUNT(*) FROM active_keys")
    if cursor.fetchone()[0] == 0:
        initial_keys = generate_multiple_keys(5)
        save_keys_to_db(initial_keys)
        print(f"✅ Сгенерировано 5 начальных ключей")
    
    print("✅ Бот запущен с поддержкой PDF!")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print(f"📄 Билеты сохраняются в папку /tickets в формате PDF")
    print(f"📁 Положите ваш исходный PDF шаблон как 'template.pdf' в папку с ботом")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())