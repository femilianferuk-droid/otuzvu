import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import re

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8397723969:AAGV-qBJ8GWLYaeY_QCdRlJGZbGJhsGNLJU"
ADMIN_CHAT_ID = 7973988177
BOT_CARD = "2204120132703386"
DATABASE_NAME = "money_for_reviews.db"

# Инициализация бота
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния FSM
class Form(StatesGroup):
    waiting_for_amount = State()
    waiting_for_receipt = State()
    waiting_for_withdraw_amount = State()
    waiting_for_withdraw_details = State()
    waiting_for_review_reward = State()
    waiting_for_review_count = State()
    waiting_for_keywords = State()
    waiting_for_banned_words = State()
    waiting_for_group_id = State()
    waiting_for_review_text = State()
    admin_change_balance = State()
    admin_mailing = State()

# Класс для работы с базой данных
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Пользователи
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0.0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Заказы на отзывы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                reward REAL,
                count_total INTEGER,
                count_done INTEGER DEFAULT 0,
                keywords TEXT,
                banned_words TEXT,
                group_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT 1,
                FOREIGN KEY (creator_id) REFERENCES users (user_id)
            )
        ''')
        
        # Выполненные отзывы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                user_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'pending',
                sent_to_group BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Транзакции (пополнения/выводы)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount REAL,
                status TEXT DEFAULT 'pending',
                details TEXT,
                admin_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # История выполнения заказов (для ограничения 1 раз в сутки)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_completions (
                user_id INTEGER,
                order_id INTEGER,
                last_completed TIMESTAMP,
                PRIMARY KEY (user_id, order_id)
            )
        ''')
        
        self.conn.commit()
    
    def get_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        if user:
            return {
                'user_id': user[0],
                'username': user[1],
                'balance': user[2],
                'registered_at': user[3]
            }
        return None
    
    def create_user(self, user_id: int, username: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
            (user_id, username)
        )
        self.conn.commit()
    
    def update_balance(self, user_id: int, amount: float):
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE users SET balance = balance + ? WHERE user_id = ?',
            (amount, user_id)
        )
        self.conn.commit()
    
    def create_order(self, creator_id: int, reward: float, count: int, 
                    keywords: str, banned_words: str, group_id: str):
        cursor = self.conn.cursor()
        # Списываем сумму с учётом комиссии 20%
        total_cost = reward * count * 1.2
        cursor.execute(
            'UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?',
            (total_cost, creator_id, total_cost)
        )
        if cursor.rowcount == 0:
            return None
        
        cursor.execute(
            '''INSERT INTO orders 
            (creator_id, reward, count_total, keywords, banned_words, group_id) 
            VALUES (?, ?, ?, ?, ?, ?)''',
            (creator_id, reward, count, keywords, banned_words, group_id)
        )
        order_id = cursor.lastrowid
        self.conn.commit()
        return order_id
    
    def get_available_orders(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT o.*, u.username 
            FROM orders o 
            JOIN users u ON o.creator_id = u.user_id 
            WHERE o.active = 1 
            AND o.count_done < o.count_total
            AND o.creator_id != ?
            AND NOT EXISTS (
                SELECT 1 FROM order_completions oc 
                WHERE oc.user_id = ? 
                AND oc.order_id = o.id 
                AND oc.last_completed > datetime('now', '-1 day')
            )
        ''', (user_id, user_id))
        return cursor.fetchall()
    
    def can_complete_order(self, user_id: int, order_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 1 FROM order_completions 
            WHERE user_id = ? AND order_id = ? 
            AND last_completed > datetime('now', '-1 day')
        ''', (user_id, order_id))
        return cursor.fetchone() is None
    
    def complete_order(self, user_id: int, order_id: int, review_text: str):
        cursor = self.conn.cursor()
        
        # Проверяем, можно ли выполнить заказ
        cursor.execute('''
            SELECT reward, count_total, count_done FROM orders 
            WHERE id = ? AND active = 1 AND count_done < count_total
        ''', (order_id,))
        order = cursor.fetchone()
        
        if not order:
            return None
        
        reward = order[0]
        
        # Создаём запись об отзыве
        cursor.execute(
            'INSERT INTO reviews (order_id, user_id, text) VALUES (?, ?, ?)',
            (order_id, user_id, review_text)
        )
        review_id = cursor.lastrowid
        
        # Увеличиваем счётчик выполненных
        cursor.execute(
            'UPDATE orders SET count_done = count_done + 1 WHERE id = ?',
            (order_id,)
        )
        
        # Начисляем вознаграждение исполнителю
        cursor.execute(
            'UPDATE users SET balance = balance + ? WHERE user_id = ?',
            (reward, user_id)
        )
        
        # Добавляем запись о выполнении
        cursor.execute('''
            INSERT OR REPLACE INTO order_completions (user_id, order_id, last_completed)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, order_id))
        
        self.conn.commit()
        return review_id
    
    def create_transaction(self, user_id: int, trans_type: str, amount: float, details: str = ""):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO transactions (user_id, type, amount, details) VALUES (?, ?, ?, ?)',
            (user_id, trans_type, amount, details)
        )
        trans_id = cursor.lastrowid
        self.conn.commit()
        return trans_id
    
    def get_pending_transactions(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT t.*, u.username 
            FROM transactions t 
            JOIN users u ON t.user_id = u.user_id 
            WHERE t.status = 'pending'
        ''')
        return cursor.fetchall()
    
    def update_transaction_status(self, trans_id: int, status: str, admin_message_id: int = None):
        cursor = self.conn.cursor()
        if admin_message_id:
            cursor.execute(
                'UPDATE transactions SET status = ?, admin_message_id = ? WHERE id = ?',
                (status, admin_message_id, trans_id)
            )
        else:
            cursor.execute(
                'UPDATE transactions SET status = ? WHERE id = ?',
                (status, trans_id)
            )
        self.conn.commit()
    
    def get_statistics(self):
        cursor = self.conn.cursor()
        stats = {}
        
        cursor.execute('SELECT COUNT(*) FROM users')
        stats['total_users'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM orders')
        stats['total_orders'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reviews WHERE status = "approved"')
        stats['completed_reviews'] = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(balance) FROM users')
        stats['total_balance'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT SUM(amount) FROM transactions WHERE status = "completed" AND type = "deposit"')
        stats['total_deposits'] = cursor.fetchone()[0] or 0
        
        return stats

db = Database()

# Главное меню
def main_menu():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="💬 Оставить отзыв", callback_data="leave_review"))
    keyboard.add(InlineKeyboardButton(text="💰 Купить отзыв", callback_data="buy_review"))
    keyboard.add(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"))
    keyboard.add(InlineKeyboardButton(text="🆘 Поддержка", callback_data="support"))
    keyboard.adjust(2)
    return keyboard.as_markup()

# Админ панель
def admin_menu():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    keyboard.add(InlineKeyboardButton(text="⚖️ Изменить баланс", callback_data="admin_change_balance"))
    keyboard.add(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing"))
    keyboard.add(InlineKeyboardButton(text="📋 Заявки на пополнение", callback_data="admin_deposits"))
    keyboard.add(InlineKeyboardButton(text="📋 Заявки на вывод", callback_data="admin_withdrawals"))
    keyboard.adjust(2)
    return keyboard.as_markup()

# Меню вывода средств
def withdraw_menu():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🤖 Криптобот (от 100₽)", callback_data="withdraw_crypto"))
    keyboard.add(InlineKeyboardButton(text="💳 СБП (от 20₽)", callback_data="withdraw_sbp"))
    keyboard.add(InlineKeyboardButton(text="💳 Карта (от 50₽)", callback_data="withdraw_card"))
    keyboard.add(InlineKeyboardButton(text="👛 ЮMoney (от 1₽)", callback_data="withdraw_yoomoney"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="profile"))
    keyboard.adjust(2)
    return keyboard.as_markup()

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "👋 Добро пожаловать в <b>Money For Reviews</b>!\n\n"
        "💰 Зарабатывайте деньги за оставление отзывов\n"
        "📝 Или покупайте отзывы для своего бизнеса\n\n"
        "Выберите действие:",
        reply_markup=main_menu()
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_CHAT_ID:
        await message.answer("Админ панель:", reply_markup=admin_menu())
    else:
        await message.answer("У вас нет доступа к админ панели")

# Обработчики кнопок
@dp.callback_query(lambda c: c.data == "support")
async def support_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "🆘 <b>Поддержка</b>\n\n"
        "По всем вопросам обращайтесь к администратору:\n"
        f"👤 @starfizovoi\n\n"
        "Для возврата в меню нажмите /start",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]])
    )

@dp.callback_query(lambda c: c.data == "profile")
async def profile_handler(callback: CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user:
        await callback.message.edit_text(
            f"👤 <b>Профиль</b>\n\n"
            f"👤 Имя: @{user['username'] or 'Не указано'}\n"
            f"💰 Баланс: {user['balance']:.2f}₽\n\n"
            "Выберите действие:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="deposit"),
                    InlineKeyboardButton(text="💸 Вывести баланс", callback_data="withdraw")
                ],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ])
        )

@dp.callback_query(lambda c: c.data == "leave_review")
async def leave_review_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    orders = db.get_available_orders(user_id)
    
    if not orders:
        await callback.message.edit_text(
            "📝 <b>Оставить отзыв</b>\n\n"
            "На данный момент нет доступных заказов для выполнения.\n"
            "Попробуйте позже!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )
        return
    
    keyboard = InlineKeyboardBuilder()
    for order in orders:
        order_id, creator_id, reward, count_total, count_done, keywords, banned_words, group_id, created_at, active, username = order
        keyboard.add(InlineKeyboardButton(
            text=f"Заказ #{order_id} - {reward}₽ (осталось: {count_total - count_done})",
            callback_data=f"select_order_{order_id}"
        ))
    
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    keyboard.adjust(1)
    
    await callback.message.edit_text(
        "📝 <b>Доступные заказы:</b>\n\n"
        "Выберите заказ для выполнения:",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(lambda c: c.data.startswith("select_order_"))
async def select_order_handler(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[-1])
    
    if not db.can_complete_order(callback.from_user.id, order_id):
        await callback.answer(
            "❌ Вы уже выполняли этот заказ сегодня. Попробуйте завтра!",
            show_alert=True
        )
        return
    
    await state.set_state(Form.waiting_for_review_text)
    await state.update_data(order_id=order_id)
    
    await callback.message.edit_text(
        "✍️ <b>Напишите ваш отзыв:</b>\n\n"
        "Отзыв будет проверен на наличие ключевых слов.\n"
        "Отправьте текст отзыва:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Отмена", callback_data="leave_review")
        ]])
    )

@dp.message(Form.waiting_for_review_text)
async def process_review_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data['order_id']
    review_text = message.text
    
    # Получаем информацию о заказе
    cursor = db.conn.cursor()
    cursor.execute('SELECT keywords, banned_words, group_id FROM orders WHERE id = ?', (order_id,))
    order_info = cursor.fetchone()
    
    if not order_info:
        await message.answer("❌ Заказ не найден!")
        await state.clear()
        return
    
    keywords = order_info[0].split(',') if order_info[0] else []
    banned_words = order_info[1].split(',') if order_info[1] else []
    group_id = order_info[2]
    
    # Проверка ключевых слов
    has_keywords = any(keyword.lower().strip() in review_text.lower() for keyword in keywords if keyword.strip())
    has_banned_words = any(banned_word.lower().strip() in review_text.lower() for banned_word in banned_words if banned_word.strip())
    
    if not has_keywords:
        await message.answer(
            "❌ <b>Отзыв отклонён!</b>\n\n"
            "В отзыве не найдены ключевые слова.\n"
            "Попробуйте выполнить другой заказ.",
            reply_markup=main_menu()
        )
        await state.clear()
        return
    
    if has_banned_words:
        await message.answer(
            "❌ <b>Отзыв отклонён!</b>\n\n"
            "В отзыве найдены запрещённые слова.\n"
            "Попробуйте выполнить другой заказ.",
            reply_markup=main_menu()
        )
        await state.clear()
        return
    
    # Сохраняем отзыв
    review_id = db.complete_order(message.from_user.id, order_id, review_text)
    
    if review_id:
        # Отправляем отзыв в группу
        try:
            if group_id:
                await bot.send_message(
                    chat_id=group_id,
                    text=f"📝 <b>Новый отзыв</b>\n\n"
                         f"От пользователя: @{message.from_user.username or 'Аноним'}\n"
                         f"Текст отзыва:\n{review_text}\n\n"
                         f"ID отзыва: #{review_id}",
                    parse_mode=ParseMode.HTML
                )
                
                # Обновляем статус отзыва
                cursor.execute('UPDATE reviews SET sent_to_group = 1 WHERE id = ?', (review_id,))
                db.conn.commit()
        except Exception as e:
            logger.error(f"Error sending to group: {e}")
        
        await message.answer(
            "✅ <b>Отзыв успешно отправлен!</b>\n\n"
            "Ваш отзыв был проверен и отправлен заказчику.\n"
            "Вознаграждение зачислено на ваш баланс.\n\n"
            "Вы можете выполнить новый заказ через 24 часа.",
            reply_markup=main_menu()
        )
    else:
        await message.answer(
            "❌ <b>Ошибка!</b>\n\n"
            "Не удалось сохранить отзыв. Попробуйте позже.",
            reply_markup=main_menu()
        )
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "buy_review")
async def buy_review_handler(callback: CallbackQuery):
    user = db.get_user(callback.from_user.id)
    
    if user['balance'] < 10:
        await callback.message.edit_text(
            "💰 <b>Купить отзыв</b>\n\n"
            "❌ Для создания заказа необходим минимальный баланс: 10₽\n"
            f"💳 Ваш баланс: {user['balance']:.2f}₽\n\n"
            "Пополните баланс в профиле.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )
        return
    
    await callback.message.edit_text(
        "💰 <b>Создание заказа на отзыв</b>\n\n"
        "Введите вознаграждение за один отзыв (от 1 до 50₽):\n"
        "<i>Пример: 10</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]])
    )
    await callback.answer()
    await Form.waiting_for_review_reward.set()

@dp.message(Form.waiting_for_review_reward)
async def process_reward(message: types.Message, state: FSMContext):
    try:
        reward = float(message.text.replace(',', '.'))
        if reward < 1 or reward > 50:
            raise ValueError
    except:
        await message.answer(
            "❌ Неверная сумма!\n"
            "Введите число от 1 до 50₽:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Отмена", callback_data="buy_review")
            ]])
        )
        return
    
    await state.update_data(reward=reward)
    await Form.waiting_for_review_count.set()
    await message.answer(
        "🔢 <b>Количество выполнений</b>\n\n"
        "Сколько раз можно выполнить этот заказ?\n"
        "Введите число:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
        ]])
    )

@dp.message(Form.waiting_for_review_count)
async def process_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text)
        if count < 1:
            raise ValueError
    except:
        await message.answer(
            "❌ Неверное количество!\n"
            "Введите целое число больше 0:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
            ]])
        )
        return
    
    data = await state.get_data()
    reward = data['reward']
    user = db.get_user(message.from_user.id)
    total_cost = reward * count * 1.2
    
    if user['balance'] < total_cost:
        await message.answer(
            f"❌ <b>Недостаточно средств!</b>\n\n"
            f"💰 Необходимо: {total_cost:.2f}₽\n"
            f"💳 Ваш баланс: {user['balance']:.2f}₽\n\n"
            f"Пополните баланс или уменьшите количество выполнений.",
            reply_markup=main_menu()
        )
        await state.clear()
        return
    
    await state.update_data(count=count)
    await Form.waiting_for_keywords.set()
    await message.answer(
        "🔑 <b>Ключевые слова</b>\n\n"
        "Введите ключевые слова через запятую.\n"
        "Хотя бы одно из них должно быть в отзыве.\n"
        "<i>Пример: отличный, качество, быстро</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
        ]])
    )

@dp.message(Form.waiting_for_keywords)
async def process_keywords(message: types.Message, state: FSMContext):
    await state.update_data(keywords=message.text)
    await Form.waiting_for_banned_words.set()
    await message.answer(
        "🚫 <b>Запрещённые слова</b>\n\n"
        "Введите запрещённые слова через запятую.\n"
        "Если они есть в отзыве - он будет отклонён.\n"
        "<i>Пример: плохо, ужасно, обман</i>\n\n"
        "Если запрещённых слов нет, отправьте 0:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
        ]])
    )

@dp.message(Form.waiting_for_banned_words)
async def process_banned_words(message: types.Message, state: FSMContext):
    banned_words = message.text if message.text != "0" else ""
    await state.update_data(banned_words=banned_words)
    await Form.waiting_for_group_id.set()
    await message.answer(
        "👥 <b>ID группы</b>\n\n"
        "Для получения отзывов вам нужно:\n"
        "1. Создать группу в Telegram\n"
        "2. Добавить бота @{(await bot.get_me()).username} в группу\n"
        "3. Дать боту права администратора\n"
        "4. Отправить ID группы (например: -1001234567890)\n\n"
        "Как получить ID группы:\n"
        "• Добавьте @RawDataBot в группу\n"
        "• Отправьте /id\n"
        "• Скопируйте 'chat_id'",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
        ]])
    )

@dp.message(Form.waiting_for_group_id)
async def process_group_id(message: types.Message, state: FSMContext):
    group_id = message.text.strip()
    
    # Проверяем, что бот является администратором группы
    try:
        chat_member = await bot.get_chat_member(group_id, (await bot.get_me()).id)
        if chat_member.status not in ['administrator', 'creator']:
            await message.answer(
                "❌ Бот не является администратором группы!\n"
                "Пожалуйста, дайте боту права администратора и попробуйте снова:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
                ]])
            )
            return
    except Exception as e:
        await message.answer(
            f"❌ Ошибка проверки группы: {e}\n"
            "Проверьте ID группы и права бота:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="buy_review")
            ]])
        )
        return
    
    data = await state.get_data()
    
    # Создаём заказ
    order_id = db.create_order(
        creator_id=message.from_user.id,
        reward=data['reward'],
        count=data['count'],
        keywords=data['keywords'],
        banned_words=data['banned_words'],
        group_id=group_id
    )
    
    if order_id:
        total_cost = data['reward'] * data['count'] * 1.2
        user = db.get_user(message.from_user.id)
        
        await message.answer(
            f"✅ <b>Заказ успешно создан!</b>\n\n"
            f"📝 ID заказа: #{order_id}\n"
            f"💰 Вознаграждение: {data['reward']}₽ за отзыв\n"
            f"🔢 Количество выполнений: {data['count']}\n"
            f"💸 Списано: {total_cost:.2f}₽\n"
            f"💳 Остаток баланса: {user['balance']:.2f}₽\n\n"
            f"📋 Ключевые слова: {data['keywords']}\n"
            f"🚫 Запрещённые слова: {data['banned_words'] or 'нет'}\n\n"
            f"Отзывы будут приходить в группу: {group_id}",
            reply_markup=main_menu()
        )
    else:
        await message.answer(
            "❌ <b>Не удалось создать заказ!</b>\n\n"
            "Проверьте, достаточно ли средств на балансе.\n"
            "Не забудьте про комиссию 20%.",
            reply_markup=main_menu()
        )
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_handler(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        f"Для пополнения баланса переведите сумму от 10 до 1000₽ на карту:\n"
        f"<code>{BOT_CARD}</code>\n\n"
        "После перевода отправьте сюда скриншот чека.\n\n"
        "Введите сумму пополнения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="profile")
        ]])
    )
    await Form.waiting_for_amount.set()

@dp.message(Form.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(',', '.'))
        if amount < 10 or amount > 1000:
            raise ValueError
    except:
        await message.answer(
            "❌ Неверная сумма!\n"
            "Введите число от 10 до 1000:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Отмена", callback_data="profile")
            ]])
        )
        return
    
    await state.update_data(amount=amount)
    await Form.waiting_for_receipt.set()
    await message.answer(
        f"📸 <b>Отправьте чек</b>\n\n"
        f"Сумма: {amount}₽\n"
        f"Карта: {BOT_CARD}\n\n"
        "Отправьте сюда скриншот или фото чека:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Отмена", callback_data="profile")
        ]])
    )

@dp.message(Form.waiting_for_receipt)
async def process_receipt(message: types.Message, state: FSMContext):
    if not (message.photo or message.document):
        await message.answer(
            "❌ Пожалуйста, отправьте скриншот или фото чека!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Отмена", callback_data="profile")
            ]])
        )
        return
    
    data = await state.get_data()
    amount = data['amount']
    
    # Создаём транзакцию
    trans_id = db.create_transaction(
        user_id=message.from_user.id,
        trans_type="deposit",
        amount=amount,
        details=f"Карта: {BOT_CARD}"
    )
    
    # Отправляем админу на проверку
    user = db.get_user(message.from_user.id)
    admin_message = await bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=message.photo[-1].file_id if message.photo else message.document.file_id,
        caption=f"📥 <b>Новая заявка на пополнение</b>\n\n"
               f"ID транзакции: #{trans_id}\n"
               f"👤 Пользователь: @{user['username']} (ID: {user['user_id']})\n"
               f"💰 Сумма: {amount}₽\n"
               f"💳 Карта: {BOT_CARD}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{trans_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{trans_id}")
            ]
        ])
    )
    
    # Сохраняем ID сообщения админа
    db.update_transaction_status(trans_id, "pending", admin_message.message_id)
    
    await message.answer(
        "✅ <b>Заявка отправлена!</b>\n\n"
        f"ID заявки: #{trans_id}\n"
        f"Сумма: {amount}₽\n\n"
        "Администратор проверит чек и зачислит средства.\n"
        "Обычно это занимает до 24 часов.",
        reply_markup=main_menu()
    )
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "💸 <b>Вывод средств</b>\n\n"
        "Выберите способ вывода:",
        reply_markup=withdraw_menu()
    )

@dp.callback_query(lambda c: c.data.startswith("withdraw_"))
async def withdraw_method_handler(callback: CallbackQuery, state: FSMContext):
    method = callback.data.replace("withdraw_", "")
    min_amounts = {
        "crypto": 100,
        "sbp": 20,
        "card": 50,
        "yoomoney": 1
    }
    
    method_names = {
        "crypto": "Криптобот",
        "sbp": "СБП",
        "card": "Банковская карта",
        "yoomoney": "ЮMoney"
    }
    
    await state.update_data(withdraw_method=method)
    await Form.waiting_for_withdraw_amount.set()
    
    await callback.message.edit_text(
        f"💸 <b>Вывод средств</b>\n\n"
        f"Способ: {method_names[method]}\n"
        f"Минимальная сумма: {min_amounts[method]}₽\n\n"
        f"Введите сумму для вывода:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="withdraw")
        ]])
    )

@dp.message(Form.waiting_for_withdraw_amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(',', '.'))
    except:
        await message.answer(
            "❌ Неверная сумма!\n"
            "Введите число:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="withdraw")
            ]])
        )
        return
    
    data = await state.get_data()
    method = data['withdraw_method']
    
    min_amounts = {
        "crypto": 100,
        "sbp": 20,
        "card": 50,
        "yoomoney": 1
    }
    
    if amount < min_amounts[method]:
        await message.answer(
            f"❌ Минимальная сумма для вывода: {min_amounts[method]}₽\n"
            f"Введите сумму заново:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="withdraw")
            ]])
        )
        return
    
    user = db.get_user(message.from_user.id)
    if user['balance'] < amount:
        await message.answer(
            f"❌ Недостаточно средств!\n"
            f"Ваш баланс: {user['balance']:.2f}₽\n"
            f"Введите меньшую сумму:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="withdraw")
            ]])
        )
        return
    
    await state.update_data(withdraw_amount=amount)
    await Form.waiting_for_withdraw_details.set()
    
    method_instructions = {
        "crypto": "Введите адрес криптокошелька (BTC, USDT TRC20/ERC20):",
        "sbp": "Введите номер телефона или СБП ID:",
        "card": "Введите номер карты (XXXX XXXX XXXX XXXX):",
        "yoomoney": "Введите номер кошелька ЮMoney:"
    }
    
    await message.answer(
        f"💸 <b>Введите реквизиты</b>\n\n"
        f"Сумма: {amount}₽\n"
        f"{method_instructions[method]}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="withdraw")
        ]])
    )

@dp.message(Form.waiting_for_withdraw_details)
async def process_withdraw_details(message: types.Message, state: FSMContext):
    details = message.text
    data = await state.get_data()
    amount = data['withdraw_amount']
    method = data['withdraw_method']
    
    method_names = {
        "crypto": "Криптобот",
        "sbp": "СБП",
        "card": "Банковская карта",
        "yoomoney": "ЮMoney"
    }
    
    # Создаём транзакцию и списываем средства
    trans_id = db.create_transaction(
        user_id=message.from_user.id,
        trans_type="withdraw",
        amount=amount,
        details=f"{method_names[method]}: {details}"
    )
    
    # Списываем средства
    db.update_balance(message.from_user.id, -amount)
    
    # Отправляем админу
    user = db.get_user(message.from_user.id)
    admin_message = await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📤 <b>Новая заявка на вывод</b>\n\n"
             f"ID транзакции: #{trans_id}\n"
             f"👤 Пользователь: @{user['username']} (ID: {user['user_id']})\n"
             f"💰 Сумма: {amount}₽\n"
             f"💳 Способ: {method_names[method]}\n"
             f"📋 Реквизиты: {details}\n"
             f"💸 Баланс пользователя: {user['balance'] - amount:.2f}₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выплачено", callback_data=f"paid_{trans_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_w_{trans_id}")
            ]
        ])
    )
    
    # Сохраняем ID сообщения админа
    db.update_transaction_status(trans_id, "pending", admin_message.message_id)
    
    await message.answer(
        "✅ <b>Заявка на вывод создана!</b>\n\n"
        f"ID заявки: #{trans_id}\n"
        f"Сумма: {amount}₽\n"
        f"Способ: {method_names[method]}\n\n"
        "Администратор обработает заявку в течение 24 часов.\n"
        "Средства временно заморожены.",
        reply_markup=main_menu()
    )
    
    await state.clear()

# Админ обработчики
@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    stats = db.get_statistics()
    
    await callback.message.edit_text(
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"📝 Всего заказов: {stats['total_orders']}\n"
        f"✅ Выполнено отзывов: {stats['completed_reviews']}\n"
        f"💰 Общий баланс: {stats['total_balance']:.2f}₽\n"
        f"💳 Всего пополнено: {stats['total_deposits']:.2f}₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
        ]])
    )

@dp.callback_query(lambda c: c.data == "admin_change_balance")
async def admin_change_balance_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    await callback.message.edit_text(
        "⚖️ <b>Изменение баланса</b>\n\n"
        "Введите ID пользователя и сумму через пробел:\n"
        "<i>Пример: 123456789 100</i>\n\n"
        "Для уменьшения баланса укажите отрицательное число.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
        ]])
    )
    await Form.admin_change_balance.set()

@dp.message(Form.admin_change_balance)
async def process_admin_change_balance(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError
        
        user_id = int(parts[0])
        amount = float(parts[1])
        
        user = db.get_user(user_id)
        if not user:
            await message.answer(
                "❌ Пользователь не найден!\n"
                "Попробуйте снова:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔙 Отмена", callback_data="admin")
                ]])
            )
            return
        
        db.update_balance(user_id, amount)
        
        await message.answer(
            f"✅ <b>Баланс изменён!</b>\n\n"
            f"👤 Пользователь: @{user['username']}\n"
            f"🆔 ID: {user_id}\n"
            f"💰 Изменение: {amount:+.2f}₽\n"
            f"💳 Новый баланс: {user['balance'] + amount:.2f}₽",
            reply_markup=admin_menu()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат!\n"
            "Введите ID пользователя и сумму через пробел:\n"
            "<i>Пример: 123456789 100</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Отмена", callback_data="admin")
            ]])
        )
        return
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_mailing")
async def admin_mailing_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Введите сообщение для рассылки всем пользователям:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
        ]])
    )
    await Form.admin_mailing.set()

@dp.message(Form.admin_mailing)
async def process_admin_mailing(message: types.Message, state: FSMContext):
    cursor = db.conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await bot.send_message(user[0], message.text)
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)  # Задержка чтобы не превысить лимиты
    
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"✅ Успешно: {success}\n"
        f"❌ Не удалось: {failed}",
        reply_markup=admin_menu()
    )
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_deposits")
async def admin_deposits_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    transactions = db.get_pending_transactions()
    deposits = [t for t in transactions if t[2] == "deposit"]
    
    if not deposits:
        await callback.message.edit_text(
            "📋 <b>Заявки на пополнение</b>\n\n"
            "Нет pending заявок.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
            ]])
        )
        return
    
    text = "📋 <b>Заявки на пополнение:</b>\n\n"
    for trans in deposits[:10]:  # Показываем первые 10
        text += f"ID: #{trans[0]}\n👤 @{trans[7]}\n💰 {trans[3]}₽\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
        ]])
    )

@dp.callback_query(lambda c: c.data == "admin_withdrawals")
async def admin_withdrawals_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    transactions = db.get_pending_transactions()
    withdrawals = [t for t in transactions if t[2] == "withdraw"]
    
    if not withdrawals:
        await callback.message.edit_text(
            "📋 <b>Заявки на вывод</b>\n\n"
            "Нет pending заявок.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
            ]])
        )
        return
    
    text = "📋 <b>Заявки на вывод:</b>\n\n"
    for trans in withdrawals[:10]:  # Показываем первые 10
        text += f"ID: #{trans[0]}\n👤 @{trans[7]}\n💰 {trans[3]}₽\n📋 {trans[4]}\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
        ]])
    )

@dp.callback_query(lambda c: c.data.startswith("approve_"))
async def approve_deposit_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    trans_id = int(callback.data.split("_")[1])
    
    # Получаем информацию о транзакции
    cursor = db.conn.cursor()
    cursor.execute('SELECT user_id, amount FROM transactions WHERE id = ?', (trans_id,))
    trans = cursor.fetchone()
    
    if not trans:
        await callback.answer("Транзакция не найдена")
        return
    
    user_id, amount = trans
    
    # Зачисляем средства
    db.update_balance(user_id, amount)
    db.update_transaction_status(trans_id, "completed")
    
    # Уведомляем пользователя
    user = db.get_user(user_id)
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Пополнение одобрено!</b>\n\n"
            f"💰 Зачислено: {amount}₽\n"
            f"💳 Новый баланс: {user['balance'] + amount:.2f}₽"
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"✅ <b>Пополнение одобрено</b>\n\n"
        f"ID транзакции: #{trans_id}\n"
        f"👤 Пользователь: @{user['username']}\n"
        f"💰 Сумма: {amount}₽\n"
        f"💳 Новый баланс: {user['balance'] + amount:.2f}₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin_deposits")
        ]])
    )

@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_deposit_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    trans_id = int(callback.data.split("_")[1])
    
    # Получаем информацию о транзакции
    cursor = db.conn.cursor()
    cursor.execute('SELECT user_id, amount FROM transactions WHERE id = ?', (trans_id,))
    trans = cursor.fetchone()
    
    if not trans:
        await callback.answer("Транзакция не найдена")
        return
    
    user_id, amount = trans
    db.update_transaction_status(trans_id, "rejected")
    
    # Уведомляем пользователя
    user = db.get_user(user_id)
    try:
        await bot.send_message(
            user_id,
            f"❌ <b>Пополнение отклонено</b>\n\n"
            f"💰 Сумма: {amount}₽\n\n"
            f"Возможные причины:\n"
            f"• Чек нечитаем\n"
            f"• Сумма не совпадает\n"
            f"• Подозрительная активность\n\n"
            f"По вопросам обращайтесь в поддержку."
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"❌ <b>Пополнение отклонено</b>\n\n"
        f"ID транзакции: #{trans_id}\n"
        f"👤 Пользователь: @{user['username']}\n"
        f"💰 Сумма: {amount}₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin_deposits")
        ]])
    )

@dp.callback_query(lambda c: c.data.startswith("paid_"))
async def paid_withdrawal_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    trans_id = int(callback.data.split("_")[1])
    db.update_transaction_status(trans_id, "completed")
    
    # Уведомляем пользователя
    cursor = db.conn.cursor()
    cursor.execute('SELECT user_id, amount, details FROM transactions WHERE id = ?', (trans_id,))
    trans = cursor.fetchone()
    
    if trans:
        user_id, amount, details = trans
        user = db.get_user(user_id)
        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Вывод средств выполнен!</b>\n\n"
                f"💰 Выведено: {amount}₽\n"
                f"📋 Способ: {details}"
            )
        except:
            pass
    
    await callback.answer("✅ Выплата подтверждена")

@dp.callback_query(lambda c: c.data.startswith("reject_w_"))
async def reject_withdrawal_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        await callback.answer("Доступ запрещён")
        return
    
    trans_id = int(callback.data.split("_")[2])
    
    # Возвращаем средства пользователю
    cursor = db.conn.cursor()
    cursor.execute('SELECT user_id, amount FROM transactions WHERE id = ?', (trans_id,))
    trans = cursor.fetchone()
    
    if trans:
        user_id, amount = trans
        db.update_balance(user_id, amount)  # Возвращаем средства
        db.update_transaction_status(trans_id, "rejected")
        
        # Уведомляем пользователя
        user = db.get_user(user_id)
        try:
            await bot.send_message(
                user_id,
                f"❌ <b>Вывод средств отклонён</b>\n\n"
                f"💰 Сумма: {amount}₽ возвращена на баланс\n"
                f"💳 Новый баланс: {user['balance'] + amount:.2f}₽\n\n"
                f"Возможные причины:\n"
                f"• Неверные реквизиты\n"
                f"• Подозрительная активность\n\n"
                f"По вопросам обращайтесь в поддержку."
            )
        except:
            pass
    
    await callback.answer("❌ Вывод отклонён, средства возвращены")

# Общие обработчики
@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "👋 Добро пожаловать в <b>Money For Reviews</b>!\n\n"
        "💰 Зарабатывайте деньги за оставление отзывов\n"
        "📝 Или покупайте отзывы для своего бизнеса\n\n"
        "Выберите действие:",
        reply_markup=main_menu()
    )

@dp.callback_query(lambda c: c.data == "back_to_admin")
async def back_to_admin_handler(callback: CallbackQuery):
    if callback.from_user.id == ADMIN_CHAT_ID:
        await callback.message.edit_text("Админ панель:", reply_markup=admin_menu())
    else:
        await callback.answer("Доступ запрещён")

async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
