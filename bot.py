import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Токен бота
BOT_TOKEN = "8397723969:AAGV-qBJ8GWLYaeY_QCdRlJGZbGJhsGNLJU"

# Состояния для разговора
(
    WAITING_USERNAME,
    WAITING_GIFTS_COUNT,
    WAITING_NFT_GIFTS_COUNT,
    WAITING_REVIEW,
    WAITING_WITHDRAW_AMOUNT,
    WAITING_WITHDRAW_DETAILS
) = range(6)

# Хранилище данных пользователей
user_data = {}

# ID группы для отправки отзывов и заявок на вывод
GROUP_ID = -1003478857463

# Комиссия на вывод
WITHDRAW_FEE = 0.02  # 2%

# Корректные тематические изображения (работающие URL)
THEME_IMAGES = {
    "welcome": "https://raw.githubusercontent.com/telegram-bots/assets/main/images/start_image.jpg",
    "review": "https://raw.githubusercontent.com/telegram-bots/assets/main/images/review_image.jpg",
    "withdraw": "https://raw.githubusercontent.com/telegram-bots/assets/main/images/money_image.jpg",
    "success": "https://raw.githubusercontent.com/telegram-bots/assets/main/images/success_image.jpg",
    "support": "https://raw.githubusercontent.com/telegram-bots/assets/main/images/support_image.jpg",
    "balance": "https://raw.githubusercontent.com/telegram-bots/assets/main/images/balance_image.jpg",
}

# Стили для красивого оформления
class Styles:
    BLUE_TITLE = "🔷 *{text}* 🔷"
    BLUE_SUBTITLE = "🔹 **{text}**"
    BLUE_TEXT = "💠 {text}"
    SUCCESS = "✅ {text}"
    ERROR = "❌ {text}"
    WARNING = "⚠️ {text}"
    MONEY = "💰 {text}"
    REVIEW = "📝 {text}"
    USER = "👤 {text}"
    GIFT = "🎁 {text}"
    NFT = "🖼️ {text}"

# Создаем красивую клавиатуру с синим дизайном
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📝 Оставить отзыв", callback_data="leave_review")],
        [InlineKeyboardButton("💰 Вывод средств", callback_data="withdraw")],
        [InlineKeyboardButton("🛟 Поддержка", callback_data="support")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_withdraw_methods_keyboard():
    keyboard = [
        [InlineKeyboardButton("💳 СБП", callback_data="withdraw_sbp")],
        [InlineKeyboardButton("💳 Банковская карта", callback_data="withdraw_card")],
        [InlineKeyboardButton("₿ Crypto Bot", callback_data="withdraw_crypto")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Функция для отправки сообщения с изображением
async def send_theme_message(update, context, text, image_type, reply_markup=None, parse_mode='Markdown'):
    try:
        # Для обычных сообщений
        if hasattr(update, 'message'):
            await update.message.reply_photo(
                photo=THEME_IMAGES[image_type],
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        # Для callback query
        else:
            from telegram import InputMediaPhoto
            await update.edit_message_media(
                media=InputMediaPhoto(
                    media=THEME_IMAGES[image_type],
                    caption=text,
                    parse_mode=parse_mode
                ),
                reply_markup=reply_markup
            )
    except Exception as e:
        logging.error(f"Ошибка отправки изображения {image_type}: {e}")
        # Резервный вариант - отправка без изображения
        if hasattr(update, 'message'):
            await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            await update.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )

# Команда /start с красивым оформлением
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
🎉 *Добро пожаловать в бот оплаты за отзывы!* 🎉

💎 *Наша миссия:* Вместе создавать честное комьюнити
🔷 *Ваша выгода:* Получайте деньги за ваши мнения

✨ *Преимущества:*
• 🎁 Бонусы за подарки
• 🖼️ Доплата за NFT
• 💰 Быстрые выплаты
• 🔒 Безопасно и надежно

👇 *Выберите действие:*
    """
    
    await send_theme_message(
        update, context, welcome_text, "welcome", 
        get_main_keyboard(), 'Markdown'
    )

# Обработка нажатий на кнопки
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "leave_review":
        user_data[user_id] = {
            'state': WAITING_USERNAME,
            'total_amount': 10,  # Начальная оплата
            'username': '',
            'gifts_bonus': 0,
            'nft_bonus': 0
        }
        
        review_info = Styles.BLUE_TITLE.format(text="СОЗДАНИЕ ОТЗЫВА") + """

📊 *Тарифы:*
• 🎁 Обычные подарки: +3₽ за каждый
• 🖼️ NFT подарки: +8₽ за каждый
• 💰 Базовая ставка: 10₽

📋 *Требования к отзыву:*
• ✅ Обязательно укажите: @v3estnikov
• ❌ Запрещено слово: скам

👇 *Напишите ваш юзернейм в Telegram:*
        """
        
        await send_theme_message(
            query, context, review_info, "review",
            get_back_keyboard(), 'Markdown'
        )
    
    elif query.data == "withdraw":
        balance = user_data.get(user_id, {}).get('balance', 0)
        
        withdraw_text = Styles.BLUE_TITLE.format(text="ВЫВОД СРЕДСТВ") + f"""

💰 *Ваш баланс:* {balance}₽
🎯 *Минимальная сумма вывода:* 10₽

*Если хотите вывести деньги с бота то выберите способ вывода ниже.*

💎 *Доступные способы вывода:*
• 💳 СБП - комиссия 2%
• 💳 Банковская карта - комиссия 2%
• ₿ Crypto Bot - комиссия 2%

📉 *Пример расчета:*
Заявка на 100₽ = 98₽ к получению

👇 *Выберите способ вывода:*
        """
        
        await send_theme_message(
            query, context, withdraw_text, "withdraw",
            get_withdraw_methods_keyboard(), 'Markdown'
        )
    
    elif query.data in ["withdraw_sbp", "withdraw_card", "withdraw_crypto"]:
        balance = user_data.get(user_id, {}).get('balance', 0)
        
        if balance < 10:
            error_text = Styles.ERROR.format(text="Недостаточно средств для вывода!\n\nМинимальная сумма: 10₽")
            await query.edit_message_text(
                text=error_text,
                reply_markup=get_main_keyboard()
            )
            return
        
        # Сохраняем выбранный способ вывода
        method_map = {
            "withdraw_sbp": "СБП",
            "withdraw_card": "Банковская карта", 
            "withdraw_crypto": "Crypto Bot"
        }
        
        user_data[user_id]['withdraw_method'] = method_map[query.data]
        user_data[user_id]['state'] = WAITING_WITHDRAW_AMOUNT
        
        withdraw_amount_text = Styles.BLUE_SUBTITLE.format(text="ЗАЯВКА НА ВЫВОД") + f"""

💎 *Способ вывода:* {method_map[query.data]}
💰 *Доступный баланс:* {balance}₽
🎯 *Минимальная сумма:* 10₽
📉 *Комиссия:* 2%

👇 *Напишите сумму для вывода в рублях:*
        """
        
        await send_theme_message(
            query, context, withdraw_amount_text, "withdraw",
            get_back_keyboard(), 'Markdown'
        )
    
    elif query.data == "support":
        support_text = Styles.BLUE_TITLE.format(text="ПОДДЕРЖКА") + """

🛟 *Мы всегда готовы помочь!*

💎 *По всем вопросам:*
• 💰 Вывод средств
• 📝 Проблемы с отзывами
• 🎁 Вопросы по бонусам
• 🔧 Технические неполадки

📞 *Контакт для связи:*
@support_username

⏰ *Время ответа:* до 24 часов
        """
        
        await send_theme_message(
            query, context, support_text, "support",
            get_main_keyboard(), 'Markdown'
        )
    
    elif query.data == "back_to_main":
        main_menu_text = "🔷 *Главное меню* 🔷\n\nВыберите действие:"
        await send_theme_message(
            query, context, main_menu_text, "welcome",
            get_main_keyboard(), 'Markdown'
        )

# Обработка сообщений пользователя
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text
    
    if user_id not in user_data:
        await update.message.reply_text(
            "👋 Для начала работы нажмите /start",
            reply_markup=get_main_keyboard()
        )
        return
    
    current_state = user_data[user_id].get('state')
    
    if current_state == WAITING_USERNAME:
        # Сохраняем username и переходим к следующему шагу
        user_data[user_id]['username'] = message_text
        user_data[user_id]['state'] = WAITING_GIFTS_COUNT
        
        gifts_text = Styles.BLUE_SUBTITLE.format(text="ШАГ 2: ПОДАРКИ") + f"""

👤 *Ваш юзернейм:* {message_text}

🎁 *Сколько у вас обычных подарков?*
• 0 подарков = +0₽
• 1+ подарков = +3₽

👇 *Напишите количество:*
        """
        
        await send_theme_message(
            update, context, gifts_text, "review",
            get_back_keyboard(), 'Markdown'
        )
    
    elif current_state == WAITING_GIFTS_COUNT:
        try:
            gifts_count = int(message_text)
            if gifts_count < 0:
                raise ValueError
                
            gifts_bonus = 3 if gifts_count > 0 else 0
            user_data[user_id]['gifts_bonus'] = gifts_bonus
            user_data[user_id]['gifts_count'] = gifts_count
            user_data[user_id]['state'] = WAITING_NFT_GIFTS_COUNT
            
            nft_text = Styles.BLUE_SUBTITLE.format(text="ШАГ 3: NFT ПОДАРКИ") + f"""

🎁 *Обычные подарки:* {gifts_count} = +{gifts_bonus}₽

🖼️ *Сколько у вас NFT подарков?*
• 0 NFT = +0₽
• 1+ NFT = +8₽

👇 *Напишите количество:*
            """
            
            await send_theme_message(
                update, context, nft_text, "review",
                get_back_keyboard(), 'Markdown'
            )
            
        except ValueError:
            await update.message.reply_text(
                Styles.ERROR.format(text="Пожалуйста, введите корректное число подарков!"),
                reply_markup=get_back_keyboard()
            )
    
    elif current_state == WAITING_NFT_GIFTS_COUNT:
        try:
            nft_count = int(message_text)
            if nft_count < 0:
                raise ValueError
                
            nft_bonus = 8 if nft_count > 0 else 0
            user_data[user_id]['nft_bonus'] = nft_bonus
            user_data[user_id]['nft_count'] = nft_count
            
            # Рассчитываем итоговую сумму
            total_amount = 10 + user_data[user_id]['gifts_bonus'] + nft_bonus
            
            review_text = Styles.BLUE_SUBTITLE.format(text="ФИНАЛЬНЫЙ ШАГ: ОТЗЫВ") + f"""

💎 *Итоговая сумма:* {total_amount}₽
• 🎁 Базовая ставка: 10₽
• 🎁 Подарки: +{user_data[user_id]['gifts_bonus']}₽
• 🖼️ NFT: +{nft_bonus}₽

📋 *Требования к отзыву:*
• ✅ Обязательно: @v3estnikov
• ❌ Запрещено: скам

✍️ *Напишите ваш отзыв:*
            """
            
            user_data[user_id]['state'] = WAITING_REVIEW
            user_data[user_id]['total_amount'] = total_amount
            
            await send_theme_message(
                update, context, review_text, "review",
                get_back_keyboard(), 'Markdown'
            )
            
        except ValueError:
            await update.message.reply_text(
                Styles.ERROR.format(text="Пожалуйста, введите корректное число NFT подарков!"),
                reply_markup=get_back_keyboard()
            )
    
    elif current_state == WAITING_REVIEW:
        review = message_text
        
        # Проверка обязательных и запрещенных слов
        if "@v3estnikov" not in review:
            await update.message.reply_text(
                Styles.ERROR.format(text="Отзыв отклонен! ❌\n\nВ отзыве обязательно должно быть упоминание @v3estnikov"),
                reply_markup=get_main_keyboard()
            )
            user_data[user_id]['state'] = None
            return
        
        if "скам" in review.lower():
            await update.message.reply_text(
                Styles.ERROR.format(text="Отзыв отклонен! ❌\n\nЗапрещено использовать слово 'скам'"),
                reply_markup=get_main_keyboard()
            )
            user_data[user_id]['state'] = None
            return
        
        # Отзыв прошел проверку
        user_data[user_id]['review'] = review
        user_data[user_id]['state'] = None
        
        # Добавляем сумму к балансу
        if 'balance' not in user_data[user_id]:
            user_data[user_id]['balance'] = 0
        user_data[user_id]['balance'] += user_data[user_id]['total_amount']
        
        # Отправляем отзыв в группу
        try:
            # Пересылаем сообщение от пользователя
            await context.bot.forward_message(
                chat_id=GROUP_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            sent_success = True
        except Exception as e:
            logging.error(f"Ошибка отправки в группу {GROUP_ID}: {e}")
            sent_success = False
        
        success_text = Styles.BLUE_TITLE.format(text="ОТЗЫВ ПРИНЯТ! ✅") + f"""

💎 *Ваш баланс пополнен на:* {user_data[user_id]['total_amount']}₽
💰 *Текущий баланс:* {user_data[user_id]['balance']}₽

📊 *Детали:*
• 🎁 Базовая ставка: 10₽
• 🎁 Подарки: +{user_data[user_id]['gifts_bonus']}₽
• 🖼️ NFT: +{user_data[user_id]['nft_bonus']}₽

{'✅ *Отзыв успешно отправлен в группу!*' if sent_success else '⚠️ *Ошибка отправки в группу, но оплата начислена*'}

✨ *Спасибо за ваш отзыв!*
        """
        
        await send_theme_message(
            update, context, success_text, "success",
            get_main_keyboard(), 'Markdown'
        )
    
    elif current_state == WAITING_WITHDRAW_AMOUNT:
        try:
            withdraw_amount = int(message_text)
            balance = user_data[user_id].get('balance', 0)
            
            if withdraw_amount < 10:
                await update.message.reply_text(
                    Styles.ERROR.format(text="Минимальная сумма вывода 10₽!"),
                    reply_markup=get_back_keyboard()
                )
                return
            
            if withdraw_amount > balance:
                await update.message.reply_text(
                    Styles.ERROR.format(text=f"Недостаточно средств!\nДоступно: {balance}₽"),
                    reply_markup=get_back_keyboard()
                )
                return
            
            # Рассчитываем сумму с учетом комиссии
            fee_amount = int(withdraw_amount * WITHDRAW_FEE)
            final_amount = withdraw_amount - fee_amount
            
            # Сохраняем данные вывода
            user_data[user_id]['withdraw_amount'] = withdraw_amount
            user_data[user_id]['fee_amount'] = fee_amount
            user_data[user_id]['final_amount'] = final_amount
            user_data[user_id]['state'] = WAITING_WITHDRAW_DETAILS
            
            # Запрашиваем реквизиты
            method = user_data[user_id]['withdraw_method']
            
            details_text = Styles.BLUE_SUBTITLE.format(text="РЕКВИЗИТЫ ДЛЯ ВЫВОДА") + f"""

💎 *Детали вывода:*
• Сумма: {withdraw_amount}₽
• Способ: {method}
• Комиссия: {fee_amount}₽ (2%)
• К получению: {final_amount}₽

👇 *Укажите реквизиты для получения оплаты:*

"""
            
            if method == "СБП":
                details_text += "💳 *Для СБП укажите:*\n• Номер телефона или карты\n• Банк (если известно)"
            elif method == "Банковская карта":
                details_text += "💳 *Для карты укажите:*\n• Номер карты\n• Банк"
            elif method == "Crypto Bot":
                details_text += "₿ *Для Crypto Bot укажите:*\n• Ваш username в Crypto Bot\n• Или номер кошелька"
            
            details_text += "\n\n📝 *Напишите реквизиты одним сообщением:*"
            
            await send_theme_message(
                update, context, details_text, "withdraw",
                get_back_keyboard(), 'Markdown'
            )
            
        except ValueError:
            await update.message.reply_text(
                Styles.ERROR.format(text="Пожалуйста, введите корректную сумму в рублях!"),
                reply_markup=get_back_keyboard()
            )
    
    elif current_state == WAITING_WITHDRAW_DETAILS:
        # Сохраняем реквизиты
        user_data[user_id]['withdraw_details'] = message_text
        user_data[user_id]['state'] = None
        
        # Обновляем баланс пользователя
        balance = user_data[user_id].get('balance', 0)
        withdraw_amount = user_data[user_id]['withdraw_amount']
        user_data[user_id]['balance'] = balance - withdraw_amount
        
        # Формируем сообщение для группы
        user_info = update.message.from_user
        withdraw_method = user_data[user_id]['withdraw_method']
        final_amount = user_data[user_id]['final_amount']
        fee_amount = user_data[user_id]['fee_amount']
        
        withdraw_request_text = f"""
🚨 *НОВАЯ ЗАЯВКА НА ВЫВОД* 🚨

👤 *Информация о пользователе:*
• ID: `{user_id}`
• Username: @{user_info.username if user_info.username else 'Нет username'}
• Имя: {user_info.first_name or ''} {user_info.last_name or ''}

💰 *Детали вывода:*
• Сумма: {withdraw_amount}₽
• Способ: {withdraw_method}
• Комиссия: {fee_amount}₽ (2%)
• К выплате: {final_amount}₽
• Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}

📋 *Реквизиты:*
{message_text}

📊 *Баланс:*
• До: {balance}₽
• После: {balance - withdraw_amount}₽

🎯 *Статус:* ⏳ Ожидает обработки
        """
        
        # Отправляем заявку в группу
        try:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=withdraw_request_text,
                parse_mode='Markdown'
            )
            sent_success = True
        except Exception as e:
            logging.error(f"Ошибка отправки заявки в группу: {e}")
            sent_success = False
        
        # Сообщение пользователю
        success_text = Styles.BLUE_TITLE.format(text="ЗАЯВКА ОТПРАВЛЕНА В ОБРАБОТКУ! ✅") + f"""

💎 *Детали заявки:*
• Сумма: {withdraw_amount}₽
• Способ: {withdraw_method}
• Комиссия: {fee_amount}₽
• К получению: {final_amount}₽
• Новый баланс: {balance - withdraw_amount}₽

✅ *Заявка отправлена в обработку!*

⏰ *Срок обработки:* до 24 часов
📞 *По вопросам:* @support_username

✨ *Спасибо за использование нашего сервиса!*
        """
        
        await send_theme_message(
            update, context, success_text, "success",
            get_main_keyboard(), 'Markdown'
        )

# Обработка команды /balance
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    balance = user_data.get(user_id, {}).get('balance', 0)
    
    balance_text = Styles.BLUE_TITLE.format(text="ВАШ БАЛАНС") + f"""

💰 *Текущий баланс:* {balance}₽
🎯 *Минимальный вывод:* 10₽
📉 *Комиссия на вывод:* 2%

{'✅ *Доступен вывод средств*' if balance >= 10 else '⚠️ *Недостаточно для вывода*'}

💎 *Как увеличить баланс:*
• 📝 Оставляйте качественные отзывы
• 🎁 Указывайте подарки
• 🖼️ Не забывайте про NFT
    """
    
    await send_theme_message(
        update, context, balance_text, "balance",
        get_main_keyboard(), 'Markdown'
    )

# Основная функция
def main():
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота
    print("🔷 Бот запущен с тематическими изображениями!")
    print(f"📤 Отзывы и заявки будут отправляться в группу: {GROUP_ID}")
    print("💳 Комиссия на вывод: 2%")
    print("🖼️ Используются корректные тематические изображения")
    application.run_polling()

if __name__ == "__main__":
    main()
