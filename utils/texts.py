# utils/texts.py

WELCOME_TEXT = """
Welcome to the National ID Fayda Printable Converter Service! 🎉

🪪 To get your printable ID card:
1. Click *📄 One ID* to generate a single card, or *📚 Multiple IDs* for a batch.
2. Select your preferred color mode and template.
3. Enter your *FAN / FCN number*.
4. Enter your *12-digit FIN number*.
5. An OTP will be sent to your registered phone — enter it here.
6. Your print-ready ID card will be generated automatically!

━━━━━━━━━━━━━━━━━━━━━━━

እንኳን ወደ ብሔራዊ መታወቂያ ፋይዳ ካርድ ሊታተም የሚችል መቀየሪያ አገልግሎት በደህና መጡ! 🎉

🪪 ሊታተም የሚችል መታወቂያ ካርድዎን ለማግኘት፡-
1. *📄 One ID* ወይም *📚 Multiple IDs* ይምረጡ።
2. የካርድ አይነት (Color/B&W) እና Template ይምረጡ።
3. የፋይዳ *FAN / FCN ቁጥርዎን* ያስገቡ።
4. ባለ 12 አሃዝ *FIN ቁጥርዎን* ያስገቡ።
5. ወደ ስልክዎ የሚላከውን *OTP* (የማረጋገጫ ኮድ) ያስገቡ።
6. ቦቱ ለህትመት ዝግጁ የሆነ ብሄራዊ መታወቂያ ካርድ ወዲያው ይልክልዎታል!
"""

FAN_PROMPT = (
    "🪪 Please enter your *FAN / FCN number*:\n\n"
    "_(This is the Fayda Application Number)_"
)

FIN_PROMPT = (
    "🔢 Please enter your *12-digit FIN number* (Fayda Identification Number):\n\n"
    "_(e.g. `5906 1079 1745` or `590610791745`)_"
)

OTP_PROMPT = (
    "📱 An OTP has been sent to your registered phone number.\n\n"
    "Please enter the *6-digit OTP* you received:"
)

OTP_INVALID = (
    "❌ Incorrect OTP or verification failed. Please try again or use /cancel to restart."
)

BATCH_START_PROMPT = (
    "📚 *Batch Mode:*\n\n"
    "Please send the *FAN number* for the first ID:\n"
    "(Press *✅ Done* anytime when you are finished adding IDs)"
)

BATCH_NEXT_PROMPT = (
    "Send the *next FAN number* or press *✅ Done* when finished:"
)


# ── Payment / Deposit UI Texts (Amharic) ──────────────────────────────────────

DEPOSIT_CHOOSE_METHOD = (
    "💰 *ገንዘብ ማስገባት*\n\n"
    "ገንዘብ የሚልኩበትን መንገድ ይምረጡ፡\n\n"
    "🏦 *CBE (የኢትዮጵያ ንግድ ባንክ)*\n"
    "📱 *ቴሌብር (Telebirr)*\n\n"
    "ከታች ካሉት አማራጮች ውስጥ አንዱን ይምረጡ👇"
)

DEPOSIT_CBE_INFO = (
    "🏦 *የኢትዮጵያ ንግድ ባንክ (CBE)*\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📋 *የአካውንት ቁጥር:* `{account_number}`\n"
    "👤 *የአካውንት ስም:* {account_name}\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "💵 ገንዘቡን ከላኩ በኋላ *የላኩትን መጠን (ብር)* ያስገቡ👇"
)

DEPOSIT_TELEBIRR_INFO = (
    "📱 *ቴሌብር (Telebirr)*\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📋 *ስልክ ቁጥር:* `{account_number}`\n"
    "👤 *የአካውንት ስም:* {account_name}\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "💵 ገንዘቡን ከላኩ በኋላ *የላኩትን መጠን (ብር)* ያስገቡ👇"
)

DEPOSIT_PAYMENT_NOT_SET = (
    "⚠️ አስተዳዳሪዎ እስካሁን የክፍያ አካውንት አላስገቡም።\n\n"
    "እባክዎ በቀጥታ አከፋፋይዎን ያነጋግሩ።"
)

DEPOSIT_ASK_AMOUNT = (
    "💵 *የላኩትን የገንዘብ መጠን (ብር) ያስገቡ:*\n\n"
    "_(ለምሳሌ: `500` ወይም `1000`)_"
)

DEPOSIT_ASK_RECEIPT = (
    "📸 *የክፍያ ማረጋገጫ (ስክሪንሾት) ይላኩ*\n\n"
    "ገንዘቡን ከላኩ በኋላ የሚመጣውን የክፍያ ማረጋገጫ\n"
    "ስክሪንሾት (ፎቶ) እዚህ ይላኩ👇\n\n"
    "⚠️ _ፎቶውን ብቻ ይላኩ — ሌላ ጽሑፍ አይላኩ_"
)

DEPOSIT_SUBMITTED = (
    "✅ *የክፍያ ማረጋገጫዎ ተልኳል!*\n\n"
    "📋 *ዝርዝር:*\n"
    "• 💳 *የክፍያ መንገድ:* {method}\n"
    "• 💰 *መጠን:* {amount} ብር\n\n"
    "⏳ ክፍያዎ በአስተዳዳሪ እየተረጋገጠ ነው...\n"
    "ሲረጋገጥ ወዲያው ማሳወቂያ ይደርስዎታል። 🔔"
)

DEPOSIT_INVALID_AMOUNT = (
    "⚠️ እባክዎ ትክክለኛ የገንዘብ መጠን ያስገቡ።\n"
    "_(ለምሳሌ: `500` ወይም `1000`)_"
)

DEPOSIT_INVALID_RECEIPT = (
    "⚠️ እባክዎ የክፍያ ማረጋገጫ *ፎቶ (ስክሪንሾት)* ይላኩ።\n"
    "ጽሑፍ ሳይሆን ፎቶ ብቻ ነው የሚፈለገው።"
)

BALANCE_TEXT = (
    "💰 *የሂሳብ ቀሪ ሒሳብ*\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👤 *ተጠቃሚ:* {name}\n"
    "💵 *ቀሪ ሒሳብ:* {balance} ብር\n"
    "🪪 *ማዘጋጀት የሚችሉት:* {downloads} መታወቂያ _(በ {price} ብር/መታወቂያ)_\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "💳 ገንዘብ ለማስገባት *💰 ገንዘብ ማስገባት* ይጫኑ"
)


# ── Billing: insufficient balance / batch trim ────────────────────────────────

INSUFFICIENT_BALANCE = (
    "⚠️ *በቂ ቀሪ ሒሳብ የለዎትም!*\n\n"
    "💵 ያለዎት ቀሪ ሒሳብ: *{balance}* ብር\n"
    "🪪 የአንድ መታወቂያ ዋጋ: *{price}* ብር\n\n"
    "💳 ለመቀጠል *💰 ገንዘብ ማስገባት* ተጭነው ገንዘብ ያስገቡ።\n\n"
    "_Insufficient balance: you have {balance} Birr, each ID costs {price} Birr. "
    "Tap 💰 ገንዘብ ማስገባት to top up._"
)

BATCH_NO_BALANCE = (
    "⚠️ *በቂ ቀሪ ሒሳብ የለዎትም!*\n\n"
    "💵 ያለዎት: *{balance}* ብር — ለአንድ መታወቂያ *{price}* ብር ያስፈልጋል።\n\n"
    "💳 *💰 ገንዘብ ማስገባት* ተጭነው ገንዘብ ያስገቡ።"
)

BATCH_PARTIAL_BALANCE = (
    "⚠️ *ቀሪ ሒሳብዎ ለሁሉም አይበቃም*\n\n"
    "💵 ያለዎት: *{balance}* ብር _(ዋጋ: {price} ብር/መታወቂያ)_\n"
    "🪪 ከጠየቁት *{requested}* መታወቂያዎች ውስጥ *{affordable}* ብቻ ማዘጋጀት ይችላሉ።\n\n"
    "⏳ የመጀመሪያዎቹን *{affordable}* እያዘጋጀን ነው..."
)


# ── Deposit: admin review + user notifications ────────────────────────────────

DEPOSIT_ADMIN_NOTE = (
    "🧾 *New deposit — pending approval*\n\n"
    "👤 User: {name} (`{user_id}`)\n"
    "💳 Method: {method}\n"
    "💰 Amount: *{amount}* Birr\n"
    "🆔 Deposit #: `{deposit_id}`\n\n"
    "Approve to credit the user's balance."
)

DEPOSIT_APPROVED = (
    "✅ *ክፍያዎ ተረጋግጧል!*\n\n"
    "💰 የገባ መጠን: *{amount}* ብር\n"
    "💵 አዲስ ቀሪ ሒሳብ: *{balance}* ብር\n\n"
    "🪪 አሁን መታወቂያ ማዘጋጀት ይችላሉ!"
)

DEPOSIT_REJECTED = (
    "❌ *ክፍያዎ ተቀባይነት አላገኘም።*\n\n"
    "💰 መጠን: {amount} ብር\n\n"
    "እባክዎ ትክክለኛ የክፍያ ማረጋገጫ (ስክሪንሾት) ይላኩ ወይም እንደገና ይሞክሩ።\n"
    "ጥያቄ ካለዎት አስተዳዳሪውን ያነጋግሩ።"
)