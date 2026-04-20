import asyncio
import json
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID, ZEPTO_PIN, ZEPTO_PHONE
from database import Database
from cart_manager import CartManager, parse_items
from address_manager import AddressManager
from zepto_automation import ZeptoAutomation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Suppress noisy third-party logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

# Conversation states
(
    IDLE,
    AWAITING_ITEM_CONFIRM,
    AWAITING_OTP,
    AWAITING_ADDRESS_CONFIRM,
    AWAITING_ORDER_CONFIRM,
    SAVE_ADDRESS_LABEL,
    SAVE_ADDRESS_FULL,
    SAVE_ADDRESS_PIN,
) = range(8)

# Global instances (single group bot)
db = Database()
address_mgr = AddressManager(db)
zepto = ZeptoAutomation()

# Pending OTP future (for login flow)
_otp_future: Optional[asyncio.Future] = None

# Queue of pending items to search (for multi-item sequential flow)
_pending_items: list = []
_active_chat_id: Optional[int] = None


def allowed(update: Update) -> bool:
    """Return True only if the message comes from the configured group."""
    if not TELEGRAM_GROUP_ID:
        return True  # No restriction configured — allow all (development mode)
    return update.effective_chat.id == TELEGRAM_GROUP_ID


async def reject(update: Update):
    """Silently ignore or optionally warn unknown callers."""
    logger.warning(f"Blocked message from chat_id={update.effective_chat.id}")
    # Intentionally silent — don't reveal the bot exists to strangers


async def cart_timeout_handler(context_ref: dict):
    """Called when cart has been idle for 60 seconds."""
    bot = context_ref["bot"]
    chat_id = context_ref["chat_id"]
    cart: CartManager = context_ref["cart"]
    bot_data = context_ref["bot_data"]

    if cart.is_empty():
        return

    address = bot_data.get("selected_address")
    if not address:
        await bot.send_message(chat_id, "⏰ No new items for 60 seconds!\n\nNo delivery address selected. Use /start to set one.")
        return

    keyboard = [
        [InlineKeyboardButton("✅ Yes, place order", callback_data="order_confirm")],
        [InlineKeyboardButton("➕ Keep adding items", callback_data="order_cancel")],
    ]
    await bot.send_message(
        chat_id,
        f"⏰ No new items for 60 seconds!\n\n{cart.format_cart()}\n\n"
        f"📍 Deliver to: *{address['label']}* — {address['pin_code']}\n\n"
        f"Place order now?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ─── Commands ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)
    chat_id = update.effective_chat.id
    user = update.effective_user

    db.add_user(user.id, user.full_name)

    # Initialise cart for this session (no timeout — user manually triggers checkout)
    if "cart" not in context.bot_data:
        context.bot_data["cart"] = CartManager(
            db,
            on_timeout=lambda: None,  # No auto-timeout behavior
        )

    # If not logged in, trigger Zepto login flow
    if not context.bot_data.get("zepto_logged_in"):
        if context.bot_data.get("login_in_progress"):
            await update.message.reply_text("⏳ Login already in progress, please wait...")
            return
        await update.message.reply_text(
            "👋 *Grocery Group Bot*\n\n"
            "Connecting to Zepto, please wait...",
            parse_mode="Markdown",
        )
        context.bot_data["login_in_progress"] = True
        await _trigger_zepto_login(chat_id, context)
        return

    # If logged in but no address selected, prompt selection
    if not context.bot_data.get("selected_address"):
        await _prompt_address_selection(chat_id, context)
        return

    addr = context.bot_data["selected_address"]
    cart: CartManager = context.bot_data.get("cart")

    # Show cart if it has items from previous session
    if cart and not cart.is_empty():
        await update.message.reply_text(
            f"👋 *Welcome back!*\n\n"
            f"📍 Delivering to: *{addr['label']}*\n\n"
            f"{cart.format_cart()}\n\n"
            "Continue adding items or type `/checkout` to place the order.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "👋 *Grocery Group Bot* is ready!\n\n"
            f"📍 Delivering to: *{addr['label']}*\n\n"
            "Just type items to add them to the cart:\n"
            "  • `add milk`\n"
            "  • `add bread, eggs, butter`\n\n"
            "*Commands:*\n"
            "/view_cart — see current cart\n"
            "/save_address — save a delivery address\n"
            "/cancel — clear cart and start over\n"
            "/history — view past orders",
            parse_mode="Markdown",
        )


async def _trigger_zepto_login(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Initiate Zepto OTP login — two phase: send OTP, then wait for reply."""
    loop = asyncio.get_event_loop()

    async def do_login():
        # Phase 1: check cookies / navigate to Zepto and trigger OTP
        await context.bot.send_message(chat_id, "⏳ Checking Zepto session...")
        success, msg = await loop.run_in_executor(None, lambda: zepto.initiate_login(ZEPTO_PHONE))
        logger.info(f"[Login] Phase1 result: {msg}")

        if not success:
            context.bot_data["login_in_progress"] = False
            await context.bot.send_message(chat_id, msg + "\nSend /start to try again.")
            return

        # Already logged in via saved cookies — skip OTP entirely
        if msg.startswith("✅ Already logged in"):
            context.bot_data["zepto_logged_in"] = True
            context.bot_data["login_in_progress"] = False
            await context.bot.send_message(chat_id, msg)
            await _prompt_address_selection(chat_id, context)
            return

        # Need OTP — inform user now that we know OTP was actually sent
        await context.bot.send_message(chat_id, f"📲 Sending OTP to {ZEPTO_PHONE}...")
        await context.bot.send_message(chat_id, msg)  # "📩 OTP sent... please reply"

        # Phase 2: wait for OTP reply in Telegram
        otp_future = loop.create_future()
        context.bot_data["otp_future"] = otp_future
        context.bot_data["awaiting_otp"] = True

        try:
            otp = await asyncio.wait_for(asyncio.wrap_future(otp_future), timeout=120)
        except asyncio.TimeoutError:
            context.bot_data["awaiting_otp"] = False
            context.bot_data.pop("otp_future", None)
            await context.bot.send_message(chat_id, "⏰ OTP timed out. Send /start to try again.")
            return

        context.bot_data["awaiting_otp"] = False
        context.bot_data.pop("otp_future", None)
        context.bot_data["login_in_progress"] = False
        await context.bot.send_message(chat_id, "⏳ Entering OTP...")

        success, msg = await loop.run_in_executor(None, lambda: zepto.complete_login(otp))
        logger.info(f"[Login] Phase2 result: {msg}")

        await context.bot.send_message(chat_id, msg)

        if success:
            context.bot_data["zepto_logged_in"] = True
            await _prompt_address_selection(chat_id, context)
        else:
            await context.bot.send_message(chat_id, "Send /start to try again.")

    asyncio.create_task(do_login())


async def _prompt_address_selection(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Fetch saved addresses from Zepto and show for selection."""
    await context.bot.send_message(chat_id, "📍 Fetching your saved addresses from Zepto...")

    loop = asyncio.get_event_loop()
    success, addresses = await loop.run_in_executor(None, zepto.get_saved_addresses)

    if not success or not addresses:
        await context.bot.send_message(
            chat_id,
            "⚠️ No saved addresses found on Zepto.\n\n"
            "Please open Zepto and select your delivery location, then type `start` again.\n\n"
            "Or if you've already selected a location on Zepto, type `start` to continue."
        )
        return

    context.bot_data["zepto_addresses"] = addresses

    keyboard = []
    for a in addresses:
        full = f"{a['label']} — {a['address']}" if a.get("address") else a["label"]
        keyboard.append([InlineKeyboardButton(f"📍 {full}", callback_data=f"setup_addr_{a['index']}")])

    await context.bot.send_message(
        chat_id,
        "✅ Zepto connected!\n\n*Select your delivery address:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


HELP_TEXT = (
    "🛒 *Grocery Bot Commands*\n\n"
    "*Adding items:*\n"
    "  `add milk` — search and add one item\n"
    "  `add bread, eggs, butter` — add multiple\n\n"
    "*Order:*\n"
    "  `checkout` / `place order` — place order now\n"
    "  `view cart` / `cart` — see current cart\n"
    "  `cancel` / `clear cart` — clear and start over\n\n"
    "*Account:*\n"
    "  `start` / `login` / `connect` — connect Zepto / select address\n"
    "  `history` / `past orders` — view past orders\n"
    "  `help` — show this message\n\n"
    "_Everything works as plain text — no slash needed!_"
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger order placement without waiting for timeout."""
    if not allowed(update):
        return await reject(update)

    chat_id = update.effective_chat.id
    cart: CartManager = context.bot_data.get("cart")
    address = context.bot_data.get("selected_address")

    if not cart or cart.is_empty():
        await update.message.reply_text("Cart is empty. Add items first with `add milk, bread` etc.", parse_mode="Markdown")
        return

    if not address:
        await update.message.reply_text("No delivery address selected. Use /start to set one.")
        return

    # Show order confirmation (same as timeout handler)
    keyboard = [
        [InlineKeyboardButton("✅ Yes, place order", callback_data="order_confirm")],
        [InlineKeyboardButton("➕ Keep adding items", callback_data="order_cancel")],
    ]
    await update.message.reply_text(
        f"{cart.format_cart()}\n\n"
        f"📍 Deliver to: *{address['label']}*\n\n"
        f"Place order now?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)

    await update.message.reply_text("🛒 Fetching cart from Zepto...")

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(None, zepto.get_browser_cart)

    if not items:
        await update.message.reply_text("Cart is empty on Zepto.")
        return

    lines = ["*Current Cart (from Zepto):*\n"]
    total = 0.0
    for i, item in enumerate(items, 1):
        price_str = item.get('price', '0').replace(',', '')
        try:
            price = float(price_str)
            total += price
        except ValueError:
            price = 0.0
        lines.append(f"{i}. {item['name']} — ₹{item['price']}")
    lines.append(f"\n*Total: ₹{total:.0f}*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)
    cart: CartManager = context.bot_data.get("cart")
    if cart:
        cart.clear()
    context.bot_data.pop("pending_items", None)
    await update.message.reply_text("🗑 Cart cleared. Start fresh by typing your items!")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)
    orders = db.get_recent_orders()
    if not orders:
        await update.message.reply_text("No past orders yet.")
        return
    lines = ["*Recent Orders:*\n"]
    for o in orders:
        lines.append(f"• Order #{o['order_id']} — ₹{o['total']:.0f} — {o['status']} — {o['placed_at'][:10]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── Save Address Flow ──────────────────────────────────────────────────────────

async def save_address_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)
    await update.message.reply_text("What label for this address? (e.g. Home, Office)")
    return SAVE_ADDRESS_LABEL


async def save_address_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_addr_label"] = update.message.text.strip()
    await update.message.reply_text("Enter the full address:")
    return SAVE_ADDRESS_FULL


async def save_address_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_addr_full"] = update.message.text.strip()
    await update.message.reply_text("Enter the PIN code:")
    return SAVE_ADDRESS_PIN


async def save_address_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin = update.message.text.strip()
    label = context.user_data.get("new_addr_label", "Home")
    full = context.user_data.get("new_addr_full", "")
    is_first = not address_mgr.has_addresses()
    address_mgr.add_address(label, full, pin, is_default=is_first)
    await update.message.reply_text(f"✅ Address '{label}' saved!" + (" (set as default)" if is_first else ""))
    return ConversationHandler.END


async def save_address_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Address saving cancelled.")
    return ConversationHandler.END


# ─── Item Search Flow ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form messages — parse items and start search flow."""
    if not allowed(update):
        return await reject(update)
    text = update.message.text.strip()

    # Ensure cart exists
    chat_id = update.effective_chat.id
    user = update.effective_user
    db.add_user(user.id, user.full_name)

    # Route login OTP reply
    if context.bot_data.get("awaiting_otp"):
        otp_future = context.bot_data.get("otp_future")
        if otp_future and not otp_future.done():
            otp_future.set_result(text)
            logger.info(f"[OTP] Received login OTP: {text}")
        return

    # Route payment OTP reply
    if context.bot_data.get("awaiting_payment_otp"):
        otp_future = context.bot_data.get("payment_otp_future")
        if otp_future and not otp_future.done():
            otp_future.set_result(text)
            logger.info(f"[OTP] Received payment OTP: {text}")
        return

    # Inline text command routing
    tl = text.lower().strip()
    if tl in ("start", "begin", "login", "connect"):
        await start(update, context)
        return
    if any(tl == k or tl.startswith(k) for k in ["place order", "checkout", "confirm order", "place order now"]):
        await checkout(update, context)
        return
    if tl in ("view cart", "cart", "show cart", "/cart"):
        await view_cart(update, context)
        return
    if tl in ("cancel", "clear cart", "reset"):
        await cancel(update, context)
        return
    if tl in ("history", "past orders", "orders"):
        await history(update, context)
        return
    if tl in ("help", "commands", "?"):
        await help_cmd(update, context)
        return

    # Session guard: must be logged in and have address selected
    if not context.bot_data.get("zepto_logged_in"):
        if context.bot_data.get("login_in_progress"):
            await update.message.reply_text("⏳ Login in progress, please wait for the OTP prompt before replying.")
        else:
            await update.message.reply_text("Please use /start to connect your Zepto account first.")
        return

    if not context.bot_data.get("selected_address"):
        await _prompt_address_selection(chat_id, context)
        return

    if "cart" not in context.bot_data:
        context.bot_data["cart"] = CartManager(
            db,
            on_timeout=lambda: None,  # No auto-timeout
        )

    items = parse_items(text)
    if not items:
        await update.message.reply_text("Please specify what you'd like to add. Example: `add milk, bread`", parse_mode="Markdown")
        return

    context.bot_data.setdefault("pending_items", [])
    context.bot_data["pending_items"].extend(items)

    # Start processing if not already in progress
    if not context.bot_data.get("searching"):
        await process_next_item(update, context)


async def process_next_item(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    """Pull next item from queue and search Zepto."""
    pending: list = context.bot_data.get("pending_items", [])
    if not pending:
        context.bot_data["searching"] = False
        return

    item = pending.pop(0)
    context.bot_data["current_search_item"] = item
    context.bot_data["searching"] = True

    # Get chat_id from either Update or CallbackQuery
    if hasattr(update_or_query, "effective_chat"):
        chat_id = update_or_query.effective_chat.id
        send = update_or_query.message.reply_text
    else:
        chat_id = update_or_query.message.chat_id
        send = lambda t, **kw: context.bot.send_message(chat_id, t, **kw)

    await context.bot.send_message(chat_id, f"🔍 Searching Zepto for *{item}*...", parse_mode="Markdown")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, zepto.start)
    if ZEPTO_PIN and not zepto._location_set:
        await context.bot.send_message(chat_id, "📍 Setting delivery location...")
        await loop.run_in_executor(None, lambda: zepto.set_delivery_location(ZEPTO_PIN))
        zepto._location_set = True
    products = await loop.run_in_executor(None, lambda: zepto.search_products(item))

    if not products:
        await context.bot.send_message(chat_id, f"❌ No results found for '{item}'. Skipping.")
        await process_next_item_by_chat(chat_id, context)
        return

    context.bot_data["search_results"] = products

    # Build inline keyboard with results
    keyboard = []
    for i, p in enumerate(products):
        stock_icon = "✅" if p.in_stock else "❌"
        label = f"{stock_icon} {p.name[:35]} — ₹{p.price:.0f} {p.quantity_unit}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"pick_{i}")])
    keyboard.append([InlineKeyboardButton("⏭ Skip this item", callback_data="pick_skip")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send screenshot for visual confirmation
    try:
        screenshot_path = zepto.take_screenshot()
        await context.bot.send_photo(
            chat_id,
            photo=open(screenshot_path, "rb"),
            caption=f"Results for *{item}* — pick one:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    except Exception:
        await context.bot.send_message(
            chat_id,
            f"Results for *{item}* — pick one:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )


async def process_next_item_by_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Helper to advance item queue without an Update object."""
    pending = context.bot_data.get("pending_items", [])
    if not pending:
        context.bot_data["searching"] = False
        return

    item = pending.pop(0)
    context.bot_data["current_search_item"] = item

    await context.bot.send_message(chat_id, f"🔍 Searching Zepto for *{item}*...", parse_mode="Markdown")

    loop = asyncio.get_event_loop()
    products = await loop.run_in_executor(None, lambda: zepto.search_products(item))
    context.bot_data["search_results"] = products

    if not products:
        await context.bot.send_message(chat_id, f"❌ No results for '{item}'. Skipping.")
        await process_next_item_by_chat(chat_id, context)
        return

    keyboard = []
    for i, p in enumerate(products):
        stock_icon = "✅" if p.in_stock else "❌"
        label = f"{stock_icon} {p.name[:35]} — ₹{p.price:.0f} {p.quantity_unit}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"pick_{i}")])
    keyboard.append([InlineKeyboardButton("⏭ Skip this item", callback_data="pick_skip")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        screenshot_path = zepto.take_screenshot()
        await context.bot.send_photo(
            chat_id,
            photo=open(screenshot_path, "rb"),
            caption=f"Results for *{item}* — pick one:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    except Exception:
        await context.bot.send_message(
            chat_id,
            f"Results for *{item}* — pick one:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )


# ─── Callback Query Handler ─────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await reject(update)
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user = update.effective_user
    db.add_user(user.id, user.full_name)
    user_row = db.get_user_by_telegram_id(user.id)
    user_db_id = user_row["user_id"] if user_row else user.id

    # ── Item selection ──
    if data.startswith("pick_"):
        products = context.bot_data.get("search_results", [])
        item_name = context.bot_data.get("current_search_item", "item")
        cart: CartManager = context.bot_data.get("cart")

        async def _edit(text, **kwargs):
            try:
                if query.message.photo:
                    await query.edit_message_caption(caption=text, **kwargs)
                else:
                    await query.edit_message_text(text=text, **kwargs)
            except Exception as e:
                logger.warning(f"[Callback] edit failed: {e}")

        if data == "pick_skip":
            await _edit(f"⏭ Skipped *{item_name}*", parse_mode="Markdown")
        else:
            idx = int(data.split("_")[1])
            if idx < len(products):
                p = products[idx]
                if not p.in_stock:
                    await _edit(
                        f"❌ *{p.name}* is out of stock. Please pick another or skip.",
                        reply_markup=query.message.reply_markup,
                        parse_mode="Markdown",
                    )
                    return
                zepto.add_to_cart(idx, item_name)
                cart.add_item(p.product_id, p.name, p.price, user_db_id)
                await _edit(
                    f"✅ Added *{p.name}* (₹{p.price:.0f}) to cart by {user.first_name}",
                    parse_mode="Markdown",
                )

        await process_next_item_by_chat(chat_id, context)

    # ── Address selection at session setup ──
    elif data.startswith("setup_addr_"):
        idx = int(data.split("_")[2])
        addresses = context.bot_data.get("zepto_addresses", [])
        address = next((a for a in addresses if a["index"] == idx), None)
        if not address:
            await query.edit_message_text("❌ Address not found.")
            return

        context.bot_data["selected_address"] = address
        await query.edit_message_text(
            f"⏳ Setting delivery address to *{address['label']}*...",
            parse_mode="Markdown",
        )

        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, lambda: zepto.select_zepto_address(address["label"]))

        if not ok:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Could not confirm address on Zepto. Will retry at checkout.",
            )

        # Show existing cart or ready message
        cart: CartManager = context.bot_data.get("cart")
        if cart and not cart.is_empty():
            await context.bot.send_message(
                chat_id,
                f"✅ Delivering to *{address['label']}*\n\n"
                f"{cart.format_cart()}\n\n"
                "Continue adding items or type `checkout` to place the order.",
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_message(
                chat_id,
                f"✅ Delivering to *{address['label']}*\n\n"
                "Type items to add to cart:\n"
                "  • `add milk`\n  • `add bread, eggs`",
                parse_mode="Markdown",
            )

    # ── Order confirmation — navigate to checkout and show payment options ──
    elif data == "order_confirm":
        address = context.bot_data.get("selected_address")
        cart: CartManager = context.bot_data.get("cart")
        if not cart or cart.is_empty():
            await query.edit_message_text("Cart is empty.")
            return

        # Save order context for later (after payment completes)
        context.bot_data["pending_order"] = {
            "items_json": json.dumps(cart.get_items()),
            "total": cart.get_total(),
            "address": address,
        }

        await query.edit_message_text(
            f"📦 Going to checkout for *{address['label']}*...",
            parse_mode="Markdown",
        )

        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, zepto.go_to_checkout)
        if not ok:
            await context.bot.send_message(chat_id, "❌ Could not reach checkout. Please try again.")
            return

        options = await loop.run_in_executor(None, zepto.get_payment_options)
        if not options:
            await context.bot.send_message(chat_id, "❌ Could not load payment options. Please check Zepto manually.")
            return

        keyboard = [[InlineKeyboardButton(o["label"], callback_data=f"pay_opt_{o['index']}")] for o in options]
        context.bot_data["payment_options"] = options
        await context.bot.send_message(
            chat_id,
            "💳 *Select payment method:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    # ── Payment option selected ──
    elif data.startswith("pay_opt_"):
        idx = int(data.split("_")[2])
        options = context.bot_data.get("payment_options", [])
        option = next((o for o in options if o["index"] == idx), None)
        if not option:
            await query.edit_message_text("❌ Payment option not found.")
            return

        await query.edit_message_text(f"💳 Processing *{option['label']}*...", parse_mode="Markdown")

        loop = asyncio.get_event_loop()
        otp_required, msg = await loop.run_in_executor(
            None, lambda: zepto.select_payment_option(option["label"])
        )

        if otp_required:
            # Set up async wait for payment OTP (same pattern as login OTP)
            otp_future = loop.create_future()
            context.bot_data["payment_otp_future"] = otp_future
            context.bot_data["awaiting_payment_otp"] = True
            await context.bot.send_message(chat_id, msg)

            async def wait_for_payment_otp():
                try:
                    otp = await asyncio.wait_for(asyncio.wrap_future(otp_future), timeout=120)
                except asyncio.TimeoutError:
                    context.bot_data["awaiting_payment_otp"] = False
                    context.bot_data.pop("payment_otp_future", None)
                    await context.bot.send_message(chat_id, "⏰ Payment OTP timed out.")
                    return

                context.bot_data["awaiting_payment_otp"] = False
                context.bot_data.pop("payment_otp_future", None)
                await context.bot.send_message(chat_id, "⏳ Entering payment OTP...")

                order = await loop.run_in_executor(None, lambda: zepto.enter_payment_otp(otp))
                await _finish_order(chat_id, context, order)

            asyncio.create_task(wait_for_payment_otp())
        else:
            # No OTP needed — check if order went through
            await context.bot.send_message(chat_id, msg)
            order = await loop.run_in_executor(None, zepto._capture_order_confirmation)
            await _finish_order(chat_id, context, order)

    elif data == "order_cancel":
        cart: CartManager = context.bot_data.get("cart")
        if cart:
            cart._reset_timer()
        await query.edit_message_text("Ok! Keep adding items. Timer reset. ⏱")


async def _finish_order(chat_id: int, context: ContextTypes.DEFAULT_TYPE, order):
    """Finalise order in DB, clear cart, send confirmation."""
    pending = context.bot_data.pop("pending_order", {})
    address = pending.get("address", {})
    total = pending.get("total", 0.0)
    items_json = pending.get("items_json", "[]")
    cart: CartManager = context.bot_data.get("cart")

    if order:
        db.create_order(items_json, 0, total, order.order_id, "saved_payment")
        if cart:
            cart.clear()
        context.bot_data.pop("selected_address", None)
        await context.bot.send_message(
            chat_id,
            f"🎉 *Order Placed!*\n\n"
            f"Order ID: `{order.order_id}`\n"
            f"Delivery to: *{address.get('label', '?')}*\n"
            f"Total: ₹{total:.0f}\n"
            f"ETA: {order.estimated_delivery}\n\n"
            "Cart cleared. Use /start to begin the next order!",
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(chat_id, "❌ Order failed. Please check Zepto manually.")


async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture OTP sent to group during login flow."""
    global _otp_future
    if _otp_future and not _otp_future.done():
        _otp_future.set_result(update.message.text.strip())
        await update.message.reply_text("✅ OTP received, logging in...")


# ─── Login Command ──────────────────────────────────────────────────────────────

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to connect Zepto account."""
    if not allowed(update):
        return await reject(update)
    global _otp_future

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /login <phone_number>")
        return

    phone = args[0]
    await update.message.reply_text(f"📲 Sending OTP to {phone}... Please reply with the OTP when received.")

    loop = asyncio.get_event_loop()
    _otp_future = loop.create_future()

    def otp_callback(ph):
        # Block until OTP arrives from Telegram chat
        future = asyncio.run_coroutine_threadsafe(asyncio.wrap_future(_otp_future), loop)
        return future.result(timeout=120)

    success = await loop.run_in_executor(None, lambda: zepto.login(phone, otp_callback))
    if success:
        await update.message.reply_text("✅ Logged in to Zepto successfully!")
    else:
        await update.message.reply_text("❌ Login failed. Please try again with /login.")


# ─── App Bootstrap ──────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Save address conversation
    save_addr_conv = ConversationHandler(
        entry_points=[CommandHandler("save_address", save_address_start)],
        states={
            SAVE_ADDRESS_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_address_label)],
            SAVE_ADDRESS_FULL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, save_address_full)],
            SAVE_ADDRESS_PIN:   [MessageHandler(filters.TEXT & ~filters.COMMAND, save_address_pin)],
        },
        fallbacks=[CommandHandler("cancel", save_address_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("view_cart", view_cart))
    app.add_handler(CommandHandler("checkout", checkout))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(save_addr_conv)
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started — polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
